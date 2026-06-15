"""StatusSync — receive operator status callbacks, reflect into state + ledger.

Drives credit triggers from status transitions:
  running -> GpuDevice.used_* increase + Allocation create + start consume (per-minute batch)
  terminated-> end Allocation + settle (release/refund hold)
  error -> session error + refund hold (per policy)

Idempotent: first running event only creates the Allocation partial UNIQUE on session_id WHERE
ended_at IS NULL, so duplicate running events are no-ops and operator restarts cannot double-start
consume. Python is the only DB writer; the operator never touches credit.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.internal import OperatorStatusEvent
from app.cluster.sse_bus import publish_session_event
from app.core import ids
from app.core.redis import get_redis
from app.db.models import Allocation, GpuDevice, Session
from app.domain.credit_engine import CreditEngine
from app.domain.notification_service import NotificationService
from app.domain.session_service import SessionService


def _sess_label(sess: Session) -> str:
    return sess.name or sess.id


class StatusSync:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.credit = CreditEngine(db)
        self.sessions = SessionService(db)

    async def on_status(self, session_id: str, ev: OperatorStatusEvent) -> None:
        """Apply an operator status event idempotently.

        running -> bind ledger (GpuDevice.used_* + Allocation) + session running + start consume.
        terminated-> end allocation, release ledger, session terminated, settle hold.
        error -> session error, release ledger, refund hold (per policy).
        preparing / terminating -> reflect interim phase only (no ledger/credit move).
        """
        # One transaction boundary for the whole callback: the operations below assume an ambient
        # transaction and use flush or begin_nested savepoints, and the credit operations (consume
        # and settle) open their savepoints inside it. Committing exactly once here avoids colliding
        # with a top-level begin() in the credit path.
        async with self.db.begin():
            sess = await self._load_session(session_id)
            if sess is None:
                return  # unknown session: nothing to reconcile

            phase = (ev.phase or "").lower()
            # Pause and resume are driven by the backend (stop and start); the operator
            # only takes the pod down or brings it up in response to spec.paused. Stale
            # callbacks arriving mid transition are ignored to avoid a race:
            #  - paused locally but running, preparing, or pending remotely: a leftover
            #    event from the pause still in flight.
            #  - running locally but paused remotely: a leftover event from the resume
            #    still in flight.
            if sess.status == "paused" and phase in ("running", "preparing", "pending"):
                return
            if phase == "paused":
                # The operator took the pod down and returned the GPU. A backend-driven pause (stop)
                # has already set paused, so this is a no-op; an operator-driven automatic pause
                # (the idle reaper) settles the billing and closes the GPU accounting here.
                if sess.status not in ("terminating", "terminated", "error", "paused"):
                    now = self._event_ts(ev)
                    await self.credit.consume(sess, self._minute_bucket(now), now)
                    # Accounting follows what the operator **actually did** (ev.yield_state), not
                    # what was asked for: pause_mode may say yield while the operator fell back to
                    # cold under RAM pressure, in which case ev.yield_state is empty and cold
                    # accounting applies.
                    if ev.yield_state == "Yielded":
                        # An operator-driven yield, typically from idling: the pod is alive and
                        # still holds the card, so the allocation stays and the card is marked
                        # lendable (lend_state=yielded). The idle-yield reservation marker is armed
                        # so host RAM cannot be held indefinitely: past the TTL, grace_enforcer
                        # demotes to durable. This has nothing to do with credits and never resumes
                        # automatically. (docs/paper/manuscript, §Design)
                        dev = await self._resolve_device(sess, ev)
                        if dev is not None:
                            dev.lend_state = "yielded"
                        await get_redis().set(f"yield-idle:{sess.id}", str(now.timestamp()), nx=True)
                    else:
                        await self._release_allocation(sess, now)
                        await self._release_device_usage(sess)
                    sess.status = "paused"
                    await self.db.flush()
                return
            if phase == "running":
                await self._on_running(sess, ev)
            elif phase == "terminated":
                await self._on_terminated(sess, ev)
            elif phase == "error":
                await self._on_error(sess, ev)
            elif phase in ("preparing", "terminating"):
                await self._reflect_phase(sess, phase, ev)
            # unknown phases are ignored (forward-compatible, no-op)

    # ── running: bind ledger + start consume ──
    async def _on_running(self, sess: Session, ev: OperatorStatusEvent) -> None:
        now = self._event_ts(ev)
        was_running = sess.status == "running"

        # Idempotency: a single live Allocation per session (partial UNIQUE WHERE ended_at IS NULL).
        # If one already exists, this is a duplicate `running` (operator restart) -> no-op for the
        # ledger, but we still converge interim session fields.
        created = await self._ensure_allocation(sess, ev, now)

        if created:
            # First running event only: increase device occupancy ledger (FOR UPDATE per device).
            await self._bump_device_usage(sess, ev)

        # Reflect binding result onto the session row.
        if ev.bound_gpu_uuid and sess.bound_gpu_uuid is None:
            sess.bound_gpu_uuid = ev.bound_gpu_uuid
        if ev.pod_ref and sess.pod_ref is None:
            sess.pod_ref = ev.pod_ref
        if sess.started_at is None:
            sess.started_at = now
        if sess.status != "running":
            # preparing/pending -> running (allowed by the lifecycle SM).
            sess.status = "running"

        await self.db.flush()

        # Start per-minute billing now (idempotent on consume:{ses}:{bucket}); the billing_worker
        # continues subsequent minutes. cpu / snapshot==0 is a no-op inside consume.
        if sess.resource_class == "gpu" and sess.credit_per_hour_snapshot:
            await self.credit.consume(sess, self._minute_bucket(now), now)

        await publish_session_event(sess.id, {"phase": "running", "status": sess.status})
        if not was_running:  # notify once, on the first transition to running, so an operator restart
            # does not repeat it
            await NotificationService(self.db).notify(
                [sess.owner_user_id], "session_running", "Session running",
                f"Session '{_sess_label(sess)}' is running and ready to connect to.",
                session_id=sess.id,
            )

    # ── terminated: end allocation + settle ──
    async def _on_terminated(self, sess: Session, ev: OperatorStatusEvent) -> None:
        now = self._event_ts(ev)
        was_terminal = sess.status in ("terminated", "error")
        await self._release_allocation(sess, now)
        await self._release_device_usage(sess)
        if sess.status not in ("terminated", "error"):
            sess.status = "terminated"
        if sess.terminated_at is None:
            sess.terminated_at = now
        await self.db.flush()
        # Finalize remaining consume + release/refund the hold (idempotent settle:{ses}).
        if sess.billing_wallet_id and sess.credit_per_hour_snapshot:
            await self.credit.settle(sess, f"settle:{sess.id}")
        await publish_session_event(sess.id, {"phase": "terminated", "status": sess.status})
        if not was_terminal:
            await NotificationService(self.db).notify(
                [sess.owner_user_id], "session_terminated", "Session terminated",
                f"Session '{_sess_label(sess)}' has been terminated.", session_id=sess.id,
            )

    # ── error: session error + refund hold ──
    async def _on_error(self, sess: Session, ev: OperatorStatusEvent) -> None:
        now = self._event_ts(ev)
        was_terminal = sess.status in ("terminated", "error")
        await self._release_allocation(sess, now)
        await self._release_device_usage(sess)
        if sess.status not in ("terminated", "error"):
            sess.status = "error"
        if sess.terminated_at is None:
            sess.terminated_at = now
        await self.db.flush()
        # Settle releases any remaining hold (release/refund) idempotently — money only moves here.
        if sess.billing_wallet_id and sess.credit_per_hour_snapshot:
            await self.credit.settle(sess, f"settle:{sess.id}")
        await publish_session_event(sess.id, {"phase": "error", "status": sess.status})
        if not was_terminal:
            msg = getattr(ev, "message", None) or getattr(ev, "reason", None)
            await NotificationService(self.db).notify(
                [sess.owner_user_id], "session_error", "Session error",
                f"Session '{_sess_label(sess)}' hit an error." + (f" ({msg})" if msg else ""),
                session_id=sess.id,
            )

    async def _reflect_phase(self, sess: Session, phase: str, ev: OperatorStatusEvent) -> None:
        mapped = "preparing" if phase == "preparing" else "terminating"
        # Only advance forward; never overwrite a terminal/running state from an interim event.
        if sess.status in ("pending", "preparing") and mapped == "preparing":
            sess.status = "preparing"
        elif sess.status in ("running", "paused") and mapped == "terminating":
            sess.status = "terminating"
        if ev.pod_ref and sess.pod_ref is None:
            sess.pod_ref = ev.pod_ref
        await self.db.flush()
        await publish_session_event(sess.id, {"phase": mapped, "status": sess.status})

    # ── ledger helpers ──
    async def _ensure_allocation(
        self, sess: Session, ev: OperatorStatusEvent, now: datetime
    ) -> bool:
        """Create the live Allocation for this session; return True iff newly created.

        Relies on the partial UNIQUE index uq_alloc_session_live (WHERE ended_at IS NULL) for
        idempotency under concurrent/duplicate running events.
        """
        existing = (
            await self.db.execute(
                select(Allocation.id).where(
                    Allocation.session_id == sess.id, Allocation.ended_at.is_(None)
                )
            )
        ).first()
        if existing is not None:
            return False

        device = await self._resolve_device(sess, ev)
        alloc = Allocation(
            id=ids.new("allocation"),
            session_id=sess.id,
            device_id=device.id if device else None,
            gpu_uuid=ev.bound_gpu_uuid or (device.gpu_uuid if device else None),
            gpu_mem_mb=sess.gpu_mem_mb,
            gpu_cores=sess.gpu_cores,
            status="bound" if (device or ev.bound_gpu_uuid) else "reserved",
            started_at=now,
        )
        try:
            # SAVEPOINT so a lost insert race only rolls back this INSERT, not the whole callback
            # tx.
            async with self.db.begin_nested():
                self.db.add(alloc)
                await self.db.flush()
        except IntegrityError:
            # Lost the race to a concurrent running event; the partial UNIQUE guards the ledger.
            return False
        return True

    async def _release_allocation(self, sess: Session, now: datetime) -> None:
        alloc = (
            await self.db.execute(
                select(Allocation).where(
                    Allocation.session_id == sess.id, Allocation.ended_at.is_(None)
                )
            )
        ).scalar_one_or_none()
        if alloc is None:
            return
        alloc.status = "released"
        alloc.ended_at = now

    async def _resolve_device(
        self, sess: Session, ev: OperatorStatusEvent
    ) -> GpuDevice | None:
        """Locate the bound GpuDevice (by reported physical UUID) within the session's cluster."""
        if sess.resource_class != "gpu":
            return None
        uuid = ev.bound_gpu_uuid or sess.bound_gpu_uuid
        if not uuid:
            return None
        return (
            await self.db.execute(
                select(GpuDevice)
                .where(GpuDevice.gpu_uuid == uuid, GpuDevice.cluster_id == sess.cluster_id)
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def _bump_device_usage(self, sess: Session, ev: OperatorStatusEvent) -> None:
        """Increase GpuDevice.used_mem_mb / used_cores under FOR UPDATE (overcommit CHECK is the
        final defense). exclusive mode occupies the whole device."""
        if sess.resource_class != "gpu":
            return
        device = await self._resolve_device(sess, ev)
        if device is None:
            return
        if sess.mode == "exclusive":
            device.used_mem_mb = device.total_mem_mb
            device.used_cores = device.total_cores
        else:
            device.used_mem_mb = device.used_mem_mb + (sess.gpu_mem_mb or 0)
            device.used_cores = device.used_cores + (sess.gpu_cores or 0)

    async def _release_device_usage(self, sess: Session) -> None:
        """Reverse the occupancy this session held on its bound device (FOR UPDATE)."""
        if sess.resource_class != "gpu":
            return
        uuid = sess.bound_gpu_uuid
        if not uuid:
            return
        device = (
            await self.db.execute(
                select(GpuDevice)
                .where(GpuDevice.gpu_uuid == uuid, GpuDevice.cluster_id == sess.cluster_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if device is None:
            return
        if sess.mode == "exclusive":
            device.used_mem_mb = 0
            device.used_cores = 0
        else:
            device.used_mem_mb = max(0, device.used_mem_mb - (sess.gpu_mem_mb or 0))
            device.used_cores = max(0, device.used_cores - (sess.gpu_cores or 0))

    # ── misc helpers ──
    async def _load_session(self, session_id: str) -> Session | None:
        sess = (
            await self.db.execute(select(Session).where(Session.id == session_id))
        ).scalar_one_or_none()
        if sess is None:
            # The operator addresses the custom resource by its RFC 1123 name (lower-cased with `_`
            # replaced by `-`; see crd._cr_name), which does not equal the original session id
            # (ses_<ULID>), so the same normalisation is applied to match back.
            sess = (
                await self.db.execute(
                    select(Session).where(
                        func.lower(func.replace(Session.id, "_", "-")) == session_id
                    )
                )
            ).scalar_one_or_none()
        return sess

    @staticmethod
    def _event_ts(ev: OperatorStatusEvent) -> datetime:
        ts = ev.ts
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts

    @staticmethod
    def _minute_bucket(now: datetime) -> int:
        """Epoch-minute bucket keying consume idempotency (consume:{ses}:{bucket})."""
        return int(now.timestamp() // 60)
