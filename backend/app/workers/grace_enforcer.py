"""grace_enforcer — enforces the grace period after credits run out. Runs every 30 seconds.

When a wallet runs dry, CreditEngine arms a Redis marker (grace:{ses} = armed_ts). This worker
enforces the deadline: once GRACE_PERIOD has elapsed and the balance is still short, the session is
gracefully paused (stop returns the GPU and keeps the hold). Tearing the pod down sends SIGTERM,
which gives a training job time to write an application-level checkpoint.

A top-up during the grace period clears the marker and the session simply continues.
"""
from __future__ import annotations

import time

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.db.base import get_sessionmaker
from app.db.models import Allocation, CreditWallet, GpuDevice, GpuNode, Session
from app.domain.notification_service import NotificationService
from app.domain.session_service import GRACE_PERIOD_SEC, SessionService

log = get_logger(__name__)

_ZERO = 0


def select_ram_pressure_victims(items, budget_mib):
    """Host-RAM pressure (SP1): evicted VRAM lives in host RAM, so the per-node sum of yielded
    VRAM is bounded by a budget. Given the node's yield items ``[(sid, prio, age_ts, vram_mib)]``
    and ``budget_mib``, return the session ids to demote — lowest priority first, then oldest
    (smallest age_ts) — until committed VRAM ≤ budget. Pure/deterministic (unit-testable).
    """
    committed = sum(t[3] for t in items)
    if budget_mib <= 0 or committed <= budget_mib:
        return []
    victims = []
    for sid, _prio, _age, mem in sorted(items, key=lambda t: (t[1], t[2])):
        if committed <= budget_mib:
            break
        victims.append(sid)
        committed -= mem
    return victims


async def run() -> None:
    redis = get_redis()
    now = time.time()
    maker = get_sessionmaker()
    async for key in redis.scan_iter(match="grace:*"):
        armed = await redis.get(key)
        if armed is None:
            continue
        try:
            elapsed = now - float(armed)
        except (TypeError, ValueError):
            await redis.delete(key)
            continue
        sid = key.split("grace:", 1)[1]
        if elapsed < GRACE_PERIOD_SEC:
            # Still inside the grace window — warn the owner ONCE that the pause is coming, so a
            # top-up can prevent it. The marker outlives the window, so guard with NX.
            if await redis.set(f"grace-warned:{sid}", "1", nx=True, ex=GRACE_PERIOD_SEC * 6):
                minutes = max(1, int((GRACE_PERIOD_SEC - elapsed) // 60))
                async with maker() as db:
                    sess = await db.get(Session, sid)
                    if sess is not None and sess.status == "running":
                        # db.get autobegan a read tx; close it (commit, not rollback — rollback
                        # expires the ORM object) so begin() below owns a clean transaction.
                        # Without this the warn crashed every time and, because the NX marker was
                        # already consumed, the pre-pause warning was silently lost for good.
                        await db.commit()
                        async with db.begin():
                            await NotificationService(db).notify(
                                [sess.owner_user_id], "grace_started",
                                "Credits exhausted: session pauses soon",
                                f"Session '{sess.name or sess.id}' is out of credits and will be "
                                f"paused in about {minutes} minute(s) unless you top up.",
                                params={"session_name": sess.name or sess.id, "minutes": minutes},
                                session_id=sid, reason="credit_exhausted",
                            )
            continue
        async with maker() as db:
            sess = await db.get(Session, sid)
            if sess is None or sess.status != "running":
                await redis.delete(key)
                continue
            # Re-check the balance: a top-up during grace clears the marker and the session runs on.
            if sess.billing_wallet_id:
                w = await db.get(CreditWallet, sess.billing_wallet_id)
                if w is not None and (w.balance - w.reserved) > _ZERO:
                    await redis.delete(key)
                    log.info("grace cleared (topped up, solvent) session=%s", sid)
                    continue
            # Grace expired and still short: pause gracefully, so the app checkpoints on SIGTERM
            # before the GPU is returned.
            try:
                await SessionService(db).stop(sid, reason="credit_exhausted")
                # Mark the pause as credit-driven; the value is the yield timestamp. On a top-up the
                # resume pass reclaims losslessly; without one, exceeding YIELD_RESERVATION_TTL
                # demotes to durable, so a lossless hold cannot occupy memory indefinitely.
                await redis.set(f"credit-paused:{sid}", str(now))
                log.warning("credit-exhaustion grace expired -> graceful pause session=%s", sid)
            except Exception:  # noqa: BLE001 — per-session isolation
                log.exception("grace enforce stop failed session=%s", sid)
                continue
        await redis.delete(key)

    # Resume and demotion pass over sessions paused for credit reasons.
    #  - Solvent again: resume automatically — losslessly under yield, cold once demoted.
    #  - Still short, holding a yield, and past the reservation TTL: demote to durable,
    #    giving up the lossless hold and returning the card and the priority claim.
    #  - Otherwise: wait for the next tick.
    async for key in redis.scan_iter(match="credit-paused:*"):
        sid = key.split("credit-paused:", 1)[1]
        yielded_at = await redis.get(key)
        async with maker() as db:
            sess = await db.get(Session, sid)
            if sess is None or sess.status != "paused":
                await redis.delete(key)  # terminated or manually resumed; drop the marker
                continue
            solvent = True
            if sess.billing_wallet_id:
                w = await db.get(CreditWallet, sess.billing_wallet_id)
                solvent = w is not None and (w.balance - w.reserved) > _ZERO
            if solvent:
                try:
                    await SessionService(db).start(sid)  # lossless reclaim under yield, or a cold resume once demoted
                    log.info("credit topped up -> auto-resume session=%s", sid)
                except Exception:  # noqa: BLE001 — a 409 for capacity and similar failures retry next tick
                    log.info("auto-resume deferred session=%s (retry next tick)", sid)
                    continue
            else:
                # Still short. While holding a lossless yield, check the reservation TTL and demote
                # to durable once it is exceeded.
                if sess.pause_mode == "yield":
                    try:
                        elapsed = now - float(yielded_at)
                    except (TypeError, ValueError):
                        elapsed = settings.YIELD_RESERVATION_TTL_SEC + 1  # unparseable timestamp; demote immediately
                    if elapsed >= settings.YIELD_RESERVATION_TTL_SEC:
                        try:
                            await SessionService(db).demote(sid)
                            log.warning(
                                "yield reservation expired (%ds) -> durable demotion session=%s",
                                settings.YIELD_RESERVATION_TTL_SEC, sid,
                            )
                        except Exception:  # noqa: BLE001 — retry next tick
                            log.exception("yield demotion failed session=%s", sid)
                continue  # still short: keep the marker, waiting for a top-up or a cold resume
        await redis.delete(key)  # resumed successfully while solvent; drop the marker

    # Idle-yield demotion pass. An operator-driven idle yield (armed by status_sync) has nothing to
    # do with credits — the session is solvent. It is never resumed automatically, since we wait for
    # user activity or an explicit resume; the only action is demoting to durable past the
    # reservation TTL, so an idle session cannot hold host RAM indefinitely. (See
    # docs/paper/manuscript, §Design.)
    async for key in redis.scan_iter(match="yield-idle:*"):
        sid = key.split("yield-idle:", 1)[1]
        armed = await redis.get(key)
        async with maker() as db:
            sess = await db.get(Session, sid)
            if sess is None or sess.status != "paused" or sess.pause_mode != "yield":
                await redis.delete(key)  # resumed or already demoted; drop the marker
                continue
            try:
                elapsed = now - float(armed)
            except (TypeError, ValueError):
                elapsed = settings.YIELD_RESERVATION_TTL_SEC + 1
            if elapsed < settings.YIELD_RESERVATION_TTL_SEC:
                continue  # the lossless hold is still valid
            try:
                await SessionService(db).demote(sid)
                log.warning("idle-yield reservation expired -> durable demotion session=%s", sid)
            except Exception:  # noqa: BLE001 — retry next tick
                log.exception("idle-yield demotion failed session=%s", sid)
                continue
        await redis.delete(key)

    # Host-RAM pressure demotion. Evicted VRAM lives in host RAM, so once a node's yielded footprint
    # exceeds its budget (node.mem * fraction), the lowest-priority and oldest yields are gracefully
    # demoted to free memory. This is the answer to the host-RAM bound on in-place yield.
    # (See docs/paper/manuscript, §Design.)
    frac = settings.YIELD_HOST_RAM_FRACTION
    if frac > 0:
        async with maker() as db:
            rows = (
                await db.execute(
                    select(
                        Session.id, Session.priority, Session.updated_at,
                        GpuNode.id, GpuNode.mem, GpuDevice.total_mem_mb,
                    )
                    .join(Allocation, Allocation.session_id == Session.id)
                    .join(GpuDevice, GpuDevice.id == Allocation.device_id)
                    .join(GpuNode, GpuNode.id == GpuDevice.node_id)
                    .where(
                        Session.status == "paused",
                        Session.pause_mode == "yield",
                        Allocation.ended_at.is_(None),
                        Allocation.kind == "resident",
                    )
                )
            ).all()
        by_node: dict = {}
        for sid, prio, upd, node_id, node_mem_gib, dev_mem_mb in rows:
            n = by_node.setdefault(node_id, {"mem": node_mem_gib, "items": []})
            n["items"].append((sid, prio or 0, upd, dev_mem_mb or 0))
        for node_id, g in by_node.items():
            budget = int((g["mem"] or 0) * 1024 * frac)   # MiB
            if budget <= 0:
                continue
            # Demote lowest priority first, then oldest, so the least important yields free memory
            # first. See select_ram_pressure_victims.
            victims = select_ram_pressure_victims(
                [(sid, prio, (upd.timestamp() if upd else 0.0), mem) for sid, prio, upd, mem in g["items"]],
                budget,
            )
            for sid in victims:
                async with maker() as db:
                    try:
                        await SessionService(db).demote(sid)
                        log.warning("host-RAM pressure -> demote yield session=%s node=%s budget=%dMiB", sid, node_id, budget)
                    except Exception:  # noqa: BLE001 — retry next tick
                        log.exception("host-RAM demote failed session=%s", sid)
