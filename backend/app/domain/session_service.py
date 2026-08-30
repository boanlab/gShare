"""SessionService — lifecycle state machine.

States: pending -> preparing -> running -> paused -> terminating -> terminated (+ error).
Disallowed transitions raise InvalidStateTransition (409). start (resume billing) / stop
(pause billing) / restart / terminate (settle). stop/terminate immediately finalize remaining
consume + settle (event correction removes batch-boundary error).
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cluster.sse_bus import publish_session_event
from app.core.config import settings
from app.core.errors import (  # noqa: F401
    InsufficientCredit,
    InvalidStateTransition,
    NoCapacity,
    NotFound,
)
from app.core.logging import get_logger
from app.db.models import Allocation, CreditWallet, GpuDevice, Session
from app.domain.credit_engine import CreditEngine
from app.domain.notification_service import NotificationService
from app.domain.pool import maybe_apply_drained_mode
from app.domain.session_events import record_session_event

log = get_logger(__name__)

# Grace window, in seconds, after credits run out. Re-exported here for grace_enforcer to import.
GRACE_PERIOD_SEC = settings.GRACE_PERIOD_SEC

# Allowed status transitions.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    # pending and preparing sessions can be cancelled too, so a session can be torn down right after
    # admission or before the running callback arrives.
    "pending": {"preparing", "terminating", "terminated", "error"},
    "preparing": {"running", "terminating", "terminated", "error"},
    "running": {"paused", "terminating", "error"},
    "paused": {"running", "terminating", "error"},
    "terminating": {"terminated", "error"},
    "terminated": set(),
    # error is cleanable: terminate() deletes the orphaned CR/pod and settles any stranded hold
    # (both idempotent), then marks the row terminated.
    "error": {"terminating"},
}


def _now() -> datetime:
    return datetime.now(UTC)


def _minute_bucket(now: datetime) -> int:
    return int(now.timestamp() // 60)


class SessionService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.credit = CreditEngine(db)
        from app.cluster.handoff import Handoff  # lazy: avoid import cycle at module load
        self.handoff = Handoff(db)

    def _assert_transition(self, frm: str, to: str) -> None:
        if to not in ALLOWED_TRANSITIONS.get(frm, set()):
            raise InvalidStateTransition(f"{frm} -> {to}")

    async def _get(self, session_id: str) -> Session:
        sess = await self.db.get(Session, session_id)
        if sess is None or sess.deleted_at is not None:
            raise NotFound(f"session {session_id}")
        return sess

    async def start(self, session_id: str):
        """Resume: re-acquire the GPU, recreate the pod, and restart billing. paused -> running.

        A GPU session re-reserves its slice; if nothing fits it stays paused and the call returns
        NoCapacity (409). Otherwise the operator is given spec.paused=false and recreates the pod.
        started_at is rebased so the paused interval is not billed.
        """
        from app.domain.scheduler import SchedulerService  # lazy: avoid import cycle

        # Close the ambient read transaction the router's RBAC lookup opened, so begin() is clean.
        await self.db.rollback()
        async with self.db.begin():
            sess = await self.db.get(Session, session_id, with_for_update=True)
            if sess is None or sess.deleted_at is not None:
                raise NotFound(f"session {session_id}")
            self._assert_transition(sess.status, "running")
            # Yield resume: with a live allocation there is nothing to re-reserve — the operator
            # just toggles VRAM back, losslessly. Decide inside the begin() block; a read between
            # blocks would collide with the next begin().
            yield_resume = sess.pause_mode == "yield" and (
                await self.db.scalar(
                    select(Allocation.id)
                    .where(Allocation.session_id == session_id, Allocation.ended_at.is_(None))
                    .limit(1)
                )
            ) is not None

            # Credit gate: a resume is a fresh admission point, so a session whose wallet cannot
            # pay must not restart. Creation gates with a hold keyed hold:{ses}; that key is
            # already spent by the time a session resumes (hold() would return idempotently), so
            # solvency is checked explicitly here. Without it a credit-exhausted session could be
            # resumed by hand and run free until the next grace window paused it again.
            # The verdict is computed here but raised AFTER the transaction closes — raising
            # inside begin() leaves the connection awaiting rollback.
            short: tuple | None = None
            if sess.credit_per_hour_snapshot and sess.billing_wallet_id:
                from decimal import Decimal as _Dec

                from app.domain.credit_engine import _occupancy as _occ
                from app.domain.credit_engine import _round2 as _r2

                w = await self.db.get(CreditWallet, sess.billing_wallet_id)
                avail = (w.balance - w.reserved) if w is not None else _Dec("0")
                # One minute of billing: the smallest unit the billing worker charges, so a
                # session that cannot cover it would be paused again before doing any work.
                need = _r2(_Dec(sess.credit_per_hour_snapshot) * _occ(sess) / _Dec("60"))
                if avail < need:
                    short = (avail, need)
        if short is not None:
            raise InsufficientCredit(available=short[0], need=short[1])

        sched = SchedulerService(self.db)
        req = sched._req_from_entry(None, sess)
        # A rollback inside reclaim or set_paused expires `sess`, so capture the scalars first.
        owner_uid, sname, sid = sess.owner_user_id, sess.name, sess.id
        is_gpu = sess.resource_class == "gpu"

        # On a yield resume, take the card back: if it is lent, preempt the spot sessions — unless a
        # spot session outranks us, in which case hold off (NoCapacity, stay paused). reclaim can
        # roll back, so it runs after the scalars are captured.
        if yield_resume:
            if not await self._reclaim_spot(session_id):
                raise NoCapacity(
                    "resume deferred: a higher-priority session is using the card", {"session_id": session_id}
                )

        if is_gpu and not yield_resume:
            async with self.db.begin():
                reserved = await sched.reserve_slice(sess, req)
                if reserved:
                    sess.status = "running"
                    sess.started_at = _now()
                    sess.status_reason = None     # the pause's reason is history now
                    sess.terminated_at = None     # a restarted session is not "ended"
            if not reserved:
                # No capacity: stay paused, do not recreate the pod, and tell the caller with a 409.
                raise NoCapacity(
                    "cannot resume: no GPU capacity available", {"session_id": session_id}
                )
        else:
            # A CPU session, or a yield resume: straight to running with no GPU re-reservation,
            # since a yield keeps its allocation. reclaim's rollback may have expired `sess`, so
            # reload it.
            async with self.db.begin():
                sess = await self.db.get(Session, session_id, with_for_update=True)
                sess.status = "running"
                sess.started_at = _now()
                sess.status_reason = None
                sess.terminated_at = None

        # ── Resume re-snapshot: the CURRENT offering price applies from a resume onward ──
        # A resume is a fresh admission point, so an admin's price fix reaches paused sessions
        # without waiting for termination; running sessions keep their start-time rate. Spot
        # sessions keep their discount, mirroring the creation-time snapshot rule.
        if is_gpu:
            from decimal import Decimal as _D

            from app.core.config import settings as _settings

            async with self.db.begin():
                est = await sched._estimate(req)
                fresh = await self.db.get(Session, session_id)
                if fresh is not None and fresh.billing_wallet_id and est.credit_per_hour is not None:
                    new_rate = _D(str(est.credit_per_hour))
                    if getattr(fresh, "preemptible", False):
                        new_rate = (new_rate * _D(str(_settings.SPOT_DISCOUNT))).quantize(_D("0.01"))
                    if fresh.credit_per_hour_snapshot != new_rate:
                        old_rate = fresh.credit_per_hour_snapshot
                        fresh.credit_per_hour_snapshot = new_rate
                        record_session_event(
                            self.db, sid, "rate_resnapshot",
                            message=f"{old_rate} -> {new_rate} C/h (resume)",
                        )

        # Operator: cold recreates the pod and rebinds the GPU; yield toggles VRAM back, losslessly.
        await self.handoff.set_paused(sess, False)
        # Close the ambient transaction set_paused's read opened, so notify's begin() is clean.
        await self.db.rollback()
        async with self.db.begin():
            record_session_event(self.db, sid, "resumed")
            await NotificationService(self.db).notify(
                [owner_uid], "session_resumed", "Session resumed",
                f"Session '{sname or sid}' resumed and re-acquired its GPU.",
                params={"session_name": sname or sid}, session_id=sid,
            )
        await publish_session_event(sid, {"phase": "running", "status": "running"})
        # Clear any credit-exhaustion grace window on resume: top-up clears grace.
        await self._clear_grace(session_id)
        return sess

    async def stop(self, session_id: str, *, reason: str = "user_stopped"):
        """Pause: return the GPU and stop billing. running -> paused.

        The operator is given spec.paused=true, tears the pod down, and returns the physical GPU —
        reporting Paused, not terminated. In cold mode the database reservation (Allocation) is
        released too, returning the capacity. Consumption so far is trued up and billing stops. The
        hold is kept; settling belongs to terminate. Resuming reserves and recreates.
        """
        now = _now()
        sess = await self._get(session_id)
        # A session that has not started yet has no pod to pause and no billing to stop — "stop"
        # from the user's point of view is a cancel, so delegate to terminate (which releases the
        # reservation and settles the hold) instead of answering 409.
        if sess.status in ("pending", "preparing"):
            return await self.terminate(session_id, reason=reason)
        self._assert_transition(sess.status, "paused")
        # Yield mode: the operator keeps the pod and evicts VRAM, so a live pod still holds the card
        # and the allocation stays — resume toggles back without re-reserving. Capture before the
        # commit expires the object.
        is_yield = sess.pause_mode == "yield"
        # Close our read tx so credit.consume owns a clean per-wallet FOR UPDATE tx.
        await self.db.commit()
        # Event-correction: charge the remaining (unbilled) running time up to now.
        await self.credit.consume(sess, _minute_bucket(now), now)
        # Return GPU capacity (DB) — operator releases the physical pod via spec.paused below.
        # A yield keeps its pod alive and therefore keeps the card, so nothing is released.
        if not is_yield:
            await self._release_allocation(session_id, now)
        else:
            # Mark the card lendable so a preemptible spot session can borrow it via
            # reserve_spot_slice.
            await self._set_resident_device_lend_state(session_id, "yielded")
        sess = await self._get(session_id)
        sess.status = "paused"
        sess.status_reason = reason
        await self.db.flush()
        _paused_titles = {
            "credit_exhausted": ("Session paused: out of credits",
                                 f"Session '{sess.name or sess.id}' was paused because its credits ran out. "
                                 "Top up and resume to continue; your work is preserved."),
            "idle": ("Session paused: idle GPU reclaimed",
                     f"Session '{sess.name or sess.id}' was paused after its GPU sat idle. "
                     "Resume any time to re-acquire a GPU."),
            "admin_stopped": ("Session paused by an administrator",
                              f"Session '{sess.name or sess.id}' was paused by an administrator."),
        }
        n_title, n_body = _paused_titles.get(reason, (
            "Session paused",
            f"Session '{sess.name or sess.id}' paused: GPU returned, billing stopped.",
        ))
        record_session_event(self.db, sess.id, "paused", reason=reason)
        await NotificationService(self.db).notify(
            [sess.owner_user_id], "session_paused", n_title, n_body,
            params={"session_name": sess.name or sess.id, "reason": reason},
            session_id=sess.id, reason=reason,
        )
        await publish_session_event(sess.id, {"phase": "paused", "status": "paused"})
        await self.db.commit()
        # The operator tears the pod down and returns the physical GPU, reporting Paused rather than
        # terminated. The custom resource stays.
        await self.handoff.set_paused(sess, True)
        return sess

    async def demote(self, session_id: str):
        """The resume reservation expired while still insolvent: give up the lossless hold and
        demote to durable, staying paused.

        The resident allocation is released, and any spot session using that card is promoted to the
        normal occupant of a now-empty card (the borrow ends, used_* is applied). pause_mode is set
        to 'cold', which drops the priority claim: once topped up, the session resumes cold with no
        preferential treatment. This is the boundary that stops an unpaid, idle session from holding
        a GPU and host RAM indefinitely.

        If the card is not lent — physically free — the demotion is graceful: the operator toggles
        VRAM back so the job can write a fresh checkpoint on SIGTERM, and only then deletes the pod,
        which preserves the latest progress rather than the last periodic checkpoint. When the card
        is lent, a spot session is on it, so the demotion is a plain cold one with no restore. (See
        docs/paper/manuscript, §Design.) """
        graceful = False  # set when the card is not lent, so a restore and fresh checkpoint are possible
        await self.db.rollback()
        async with self.db.begin():
            resident_alloc = (
                await self.db.scalars(
                    select(Allocation).where(
                        Allocation.session_id == session_id,
                        Allocation.ended_at.is_(None),
                        Allocation.kind == "resident",
                    )
                )
            ).first()
            dev = None
            if resident_alloc is not None and resident_alloc.device_id is not None:
                dev = await self.db.get(GpuDevice, resident_alloc.device_id, with_for_update=True)
            if dev is not None:
                spot_alloc = (
                    await self.db.scalars(
                        select(Allocation).where(
                            Allocation.device_id == dev.id,
                            Allocation.ended_at.is_(None),
                            Allocation.kind == "spot",
                        )
                    )
                ).first()
                if spot_alloc is not None:
                    # Promote: the spot session becomes the normal occupant of the now-empty card
                    # (borrow becomes resident, used_* applied).
                    spot_alloc.kind = "resident"
                    dev.used_mem_mb = spot_alloc.gpu_mem_mb or dev.total_mem_mb
                    dev.used_cores = spot_alloc.gpu_cores or dev.total_cores
                else:
                    dev.used_mem_mb = 0
                    dev.used_cores = 0
                    graceful = True  # empty card: restore, take a fresh checkpoint, then delete cold
                dev.lend_state = ""
            if resident_alloc is not None:
                resident_alloc.status = "released"
                resident_alloc.ended_at = _now()
            sess = await self.db.get(Session, session_id, with_for_update=True)
            if sess is not None:
                sess.pause_mode = "cold"  # the lossless claim is gone; resume will be cold
        # Patch the custom resource so the operator demotes the still-live yielded pod
        # (pauseMode=cold, still paused). With graceful=True the operator toggles VRAM back, sends
        # SIGTERM so the job writes a fresh checkpoint, then deletes; otherwise it deletes cold.
        sess = await self._get(session_id)
        await self.handoff.set_paused(sess, True, graceful_demote=graceful)
        await publish_session_event(session_id, {"phase": "paused", "status": "paused"})
        return sess

    async def restart(self, session_id: str):
        """Restart a session: stop (pause + finalize consume) then start (resume)."""
        sess = await self._get(session_id)
        status = sess.status
        if status == "running":
            await self.stop(session_id)
            status = "paused"
        # After a stop the session is paused; resume it.
        if status == "paused":
            return await self.start(session_id)
        return sess

    async def terminate(self, session_id: str, *, forced: bool = False, reason: str = "user_stopped"):
        """Terminate + settle. Also invoked by operator idle-reaper -> terminated->settle.

        running/paused -> terminating -> terminated. settle finalizes remaining consume, releases
        the hold (reserved) and refunds the unconsumed balance. GPU capacity is reclaimed.
        """
        now = _now()
        # Serialise concurrent terminations: lock the session row FOR UPDATE and commit the
        # `terminating` claim atomically.
        sess = await self.db.get(Session, session_id, with_for_update=True)
        if sess is None or sess.deleted_at is not None:
            raise NotFound(f"session {session_id}")
        if sess.status == "terminated":
            return sess
        # running/paused -> terminating (skip if already terminating).
        if sess.status != "terminating":
            was_error = sess.status == "error"
            self._assert_transition(sess.status, "terminating")
            sess.status = "terminating"
            if not was_error or sess.status_reason is None:
                # cleaning up an error wreck keeps its original reason (quota_exceeded, ...)
                sess.status_reason = reason
        await self.db.commit()             # publish the claim and drop the lock; concurrent callers see `terminating` and proceed idempotently
        sess = await self._get(session_id)  # reload so attributes are accessible after the commit
        # Reclaim GPU capacity (idempotent — released rows are skipped).
        await self._release_allocation(session_id, now)
        billable = sess.billing_wallet_id is not None and bool(sess.credit_per_hour_snapshot)
        await self.db.commit()
        # Settle: finalize remaining consume + release/refund hold (idempotent on settle:{ses}).
        if billable:
            await self.credit.settle(sess, key=f"settle:{sess.id}")
        sess = await self._get(session_id)
        sess.status = "terminated"
        sess.terminated_at = now
        if sess.status_reason is None:
            sess.status_reason = reason
        await self.db.flush()
        # Notify the owner. The operator callback _on_terminated is guarded by was_terminal, so this
        # cannot fire twice.
        record_session_event(self.db, sess.id, "terminated", reason=sess.status_reason)
        await NotificationService(self.db).notify(
            [sess.owner_user_id], "session_terminated", "Session terminated",
            f"Session '{sess.name or sess.id}' has been terminated.",
            params={"session_name": sess.name or sess.id, "reason": sess.status_reason},
            session_id=sess.id,
        )
        await publish_session_event(sess.id, {"phase": "terminated", "status": "terminated"})
        await self.db.commit()
        await self._clear_grace(session_id)
        # Drop the desired state: deleting the GShareSession lets the operator's finalizer clean up
        # the pod, service, and secret. NotFound is ignored for offline and development runs where
        # no custom resource exists.
        try:
            await self.handoff.delete_desired(sess)
        except Exception:  # noqa: BLE001 — a missing CR or an offline cluster must not block termination; the ledger is already settled
            log.warning("CR delete skipped for %s (already gone or offline)", session_id)
        return sess

    async def start_grace(self, session):
        """Begin grace period on credit exhaustion.

        Called by CreditEngine.consume (inside the consume transaction) when available balance
        hits 0. This only ARMS the grace window in Redis — it performs NO DB writes/commits so it
        cannot corrupt the in-flight consume transaction. The user may top up during the window to
        clear it (start clears it); on expiry the enforcer gracefully stops the session
        (paused, VRAM returned, hold KEPT — same as manual stop). Idempotent on the grace marker.
        """
        from app.core.redis import get_redis

        session_id = session.id if isinstance(session, Session) else session
        redis = get_redis()
        marker = f"grace:{session_id}"
        # Arm the grace window once (NX); value = armed timestamp. TTL is a safety leak-guard, NOT
        # the deadline — grace_enforcer enforces the GRACE_PERIOD elapsed-check, so the marker must
        # outlive the deadline for the enforcer to act on it (and clears it on pause/solvency).
        first = await redis.set(marker, str(_now().timestamp()), nx=True, ex=GRACE_PERIOD_SEC * 6)
        if first:
            log.warning(
                "session %s entered credit-exhaustion grace (%ds)", session_id, GRACE_PERIOD_SEC
            )

    async def _clear_grace(self, session_id: str) -> None:
        """Clear yield/grace reservation markers (on resume/top-up or terminate)."""
        from app.core.redis import get_redis

        await get_redis().delete(f"grace:{session_id}", f"yield-idle:{session_id}")

    # ── private helpers ──
    async def _release_allocation(self, session_id: str, now: datetime) -> None:
        """Release the live Allocation and return device capacity (used_* down).

A spot allocation (kind='spot') never added to device.used_*, so nothing is subtracted; the card
        simply returns to the lendable pool (lend_state 'lent' -> 'yielded', since the resident is
        still yielding). Releasing a resident allocation does subtract from used_*.
        (See docs/paper/manuscript, §Design.)
        """
        alloc = (
            await self.db.scalars(
                select(Allocation).where(
                    Allocation.session_id == session_id,
                    Allocation.ended_at.is_(None),
                )
            )
        ).first()
        if alloc is None:
            return
        if alloc.device_id is not None:
            dev = await self.db.get(GpuDevice, alloc.device_id, with_for_update=True)
            if dev is not None:
                if alloc.kind == "spot":
                    # The physical card was free, so used_* is untouched. If the resident is still
                    # yielding, return the card to the lendable pool.
                    if dev.lend_state == "lent":
                        dev.lend_state = "yielded"
                else:
                    dev.used_mem_mb = max(0, dev.used_mem_mb - (alloc.gpu_mem_mb or 0))
                    dev.used_cores = max(0, dev.used_cores - (alloc.gpu_cores or 0))
                maybe_apply_drained_mode(dev)
        alloc.status = "released"
        alloc.ended_at = now
        await self.db.flush()

    async def _set_resident_device_lend_state(self, session_id: str, state: str) -> None:
        """Set lend_state on the device the resident session's live allocation points at, marking it
        yielded or reclaimed."""
        alloc = (
            await self.db.scalars(
                select(Allocation).where(
                    Allocation.session_id == session_id,
                    Allocation.ended_at.is_(None),
                    Allocation.kind == "resident",
                )
            )
        ).first()
        if alloc is not None and alloc.device_id is not None:
            dev = await self.db.get(GpuDevice, alloc.device_id, with_for_update=True)
            if dev is not None:
                dev.lend_state = state

    async def _reclaim_spot(self, resident_session_id: str) -> bool:
        """Take the card back when a resident resumes.

        If the card is lent, preempt (terminate) the spot sessions on it, but respect priority: when
        a spot session outranks the resident, preempt nothing and return False, deferring the
        resident's resume. Returns True when the card was reclaimed or was already free.

        A rollback expires the ORM objects, so the scalars we need are captured beforehand.
        (See docs/paper/manuscript, §Design.)
        """
        await self.db.rollback()
        resident_alloc = (
            await self.db.scalars(
                select(Allocation).where(
                    Allocation.session_id == resident_session_id,
                    Allocation.ended_at.is_(None),
                    Allocation.kind == "resident",
                )
            )
        ).first()
        if resident_alloc is None or resident_alloc.device_id is None:
            return True  # no card held, nothing to reclaim
        dev_id = resident_alloc.device_id
        dev = await self.db.get(GpuDevice, dev_id)
        if dev is None or dev.lend_state not in ("yielded", "lent"):
            return True
        borrow_sids: list[str] = []
        if dev.lend_state == "lent":
            # A card can carry several spot sessions (fractional borrowing through HAMi). All of
            # them have to go before the resident gets the full card back: a lossless restore needs
            # the card's entire VRAM free. (See build/hami-fork.)
            borrows = (
                await self.db.scalars(
                    select(Allocation).where(
                        Allocation.device_id == dev_id,
                        Allocation.ended_at.is_(None),
                        Allocation.kind == "spot",
                    )
                )
            ).all()
            borrow_sids = [b.session_id for b in borrows if b.session_id is not None]
        if borrow_sids:
            # Priority-aware: if any spot session outranks the resident, preempt none of them and
            # let the resident wait.
            resident_prio = (await self.db.scalar(
                select(Session.priority).where(Session.id == resident_session_id))) or 0
            max_spot_prio = (await self.db.scalar(
                select(func.max(Session.priority)).where(Session.id.in_(borrow_sids)))) or 0
            if resident_prio < max_spot_prio:
                log.info(
                    "reclaim deferred: a spot session has higher priority (resident=%d < spot=%d) session=%s",
                    resident_prio, max_spot_prio, resident_session_id,
                )
                return False
            for borrow_sid in borrow_sids:
                try:
                    await SessionService(self.db).terminate(borrow_sid, forced=True)
                except Exception:  # noqa: BLE001 — a failed preemption must not block reclaim; the next tick retries
                    log.exception("reclaim: failed to preempt spot session session=%s", borrow_sid)
        # The resident has the card back: active again, the loan is over.
        await self.db.rollback()
        async with self.db.begin():
            d = await self.db.get(GpuDevice, dev_id, with_for_update=True)
            if d is not None:
                d.lend_state = ""
        return True
