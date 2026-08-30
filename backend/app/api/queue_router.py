"""Queue router. List / mine / cancel / PATCH priority.

``QueueEntry`` (PG) IS the queue; ordering comes from app.domain.queue_ranking, the same module
the dequeue ticker uses, so the position shown here is exactly the dequeue order. ``position`` is
the 1-based rank by descending score among queued entries.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination, get_current_principal
from app.api.schemas.session import QueueList, QueueMineList
from app.auth.rbac import Principal
from app.core.errors import DomainError, NotFound
from app.core.redis import get_redis
from app.db.base import get_db
from app.db.models import QueueEntry, Session
from app.domain import queue_ranking
from app.domain.audit_service import AuditService
from app.domain.credit_engine import CreditEngine
from app.domain.session_events import record_session_event

router = APIRouter(prefix="/queue", tags=["queue"])

PRIORITY_MAX = 10  # manual_priority policy ceiling


class _Conflict(DomainError):
    code, http = "conflict", 409


class _Unprocessable(DomainError):
    code, http = "validation_failed", 422


class QueuePriorityPatch(BaseModel):
    priority: int = Field(ge=0)


async def _ranked(db: AsyncSession) -> list[tuple[QueueEntry, float]]:
    """All queued entries ordered by descending score — delegated to queue_ranking."""
    return await queue_ranking.rank(db)


def _entry_view(entry: QueueEntry, score: float, position: int) -> dict:
    enq = entry.enqueued_at
    return {
        "id": entry.id,
        "session_id": entry.session_id,
        "priority": entry.priority,
        "status": "queued",
        "position": position,
        "score": round(score, 3),
        "session_req": entry.session_req,
        "enqueued_at": enq.isoformat() if enq else None,
    }


@router.get("", response_model=QueueList)
async def list_queue(
    page: Pagination = Depends(),
    group_id: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    principal.require(action="queue.read")
    ranked = await _ranked(db)

    if group_id is not None:
        # Filter to entries whose session belongs to group_id.
        sess_rows = (
            await db.scalars(
                select(Session.id).where(Session.group_id == group_id)
            )
        ).all()
        allowed = set(sess_rows)
        ranked = [(e, s) for e, s in ranked if e.session_id in allowed]

    data = [
        _entry_view(e, s, pos)
        for pos, (e, s) in enumerate(ranked[page.offset:page.offset + page.size],
                                     start=page.offset + 1)
    ]
    return {"data": data, "pagination": {"page": page.page, "size": page.size,
                                         "total": len(ranked)}}


@router.get("/mine", response_model=QueueMineList)
async def my_queue(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    ranked = await _ranked(db)
    # Map session -> owner to filter to the caller's entries (preserve global position).
    sess_ids = [e.session_id for e, _ in ranked]
    owners: dict[str, str] = {}
    if sess_ids:
        owner_rows = (
            await db.execute(
                select(Session.id, Session.owner_user_id).where(Session.id.in_(sess_ids))
            )
        ).all()
        owners = {sid: oid for sid, oid in owner_rows}
    # Rough wait estimate from the median of the last realized queue waits: position × median.
    # Deliberately labelled an estimate in the UI; None until enough samples exist.
    median_wait_sec: float | None = None
    try:
        samples = [float(x) for x in await get_redis().lrange("gshare:queue:wait_samples", 0, 99)]
        if len(samples) >= 5:
            samples.sort()
            median_wait_sec = samples[len(samples) // 2]
    except Exception:  # noqa: BLE001 — the ETA is a convenience only
        median_wait_sec = None

    data = []
    for pos, (e, s) in enumerate(ranked, start=1):
        if owners.get(e.session_id) != principal.user_id:
            continue
        view = _entry_view(e, s, pos)
        view["eta_minutes"] = (
            round(pos * median_wait_sec / 60) if median_wait_sec is not None else None
        )
        data.append(view)
    return {"data": data}


@router.delete("/{queue_entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_queue_entry(
    queue_entry_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a queued entry; the session goes terminated and its hold is released."""
    entry = await db.get(QueueEntry, queue_entry_id)
    if entry is None:
        raise NotFound("queue entry not found")
    session = await db.get(Session, entry.session_id)
    if session is None:
        raise NotFound("session not found")

    # Owner or group_admin+.
    if session.owner_user_id != principal.user_id:
        principal.require(action="queue.update", group_id=session.group_id)

    # Already assigned/scheduled -> cannot cancel from the queue (409).
    if session.status not in ("pending", "preparing"):
        raise _Conflict("session already assigned")

    # Release the credit hold (refund) for the never-started session.
    if session.billing_wallet_id is not None:
        await CreditEngine(db).settle(session, key=f"settle:{session.id}")

    session.status = "terminated"
    session.status_reason = session.status_reason or "user_stopped"
    session.terminated_at = datetime.now(UTC)
    await db.delete(entry)
    # The timeline must not end on 'queued' for a session the user just cancelled.
    record_session_event(db, session.id, "terminated", reason=session.status_reason)

    await AuditService(db).record(
        actor=principal.user_id, action="queue.cancel", target=entry.id,
        session_id=session.id,
    )
    await db.commit()
    return None


@router.patch("/{queue_entry_id}")
async def update_priority(
    queue_entry_id: str,
    body: QueuePriorityPatch,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Admin reorders the queue: QueueEntry.priority sets the priority band."""
    principal.require(action="queue.update")
    entry = await db.get(QueueEntry, queue_entry_id)
    if entry is None:
        raise NotFound("queue entry not found")
    session = await db.get(Session, entry.session_id)
    if session is None:
        raise NotFound("session not found")

    # Only queued entries can be reprioritized (-> 409 if assigned/cancelled).
    if session.status not in ("pending", "preparing"):
        raise _Conflict("entry not in queued state")

    # Manual priority must stay within the policy ceiling (-> 422).
    if body.priority > PRIORITY_MAX:
        raise _Unprocessable(
            "priority out of range",
            {"max": PRIORITY_MAX, "got": body.priority},
        )

    if session.group_id is not None:
        principal.require(action="queue.update", group_id=session.group_id)

    entry.priority = body.priority
    await db.flush()
    new_score = queue_ranking.score(entry)

    await AuditService(db).record(
        actor=principal.user_id, action="queue.priority.set", target=entry.id,
        priority=entry.priority,
    )
    await db.commit()

    # Recompute position after the change.
    ranked = await _ranked(db)
    position = next((i for i, (e, _) in enumerate(ranked, start=1) if e.id == entry.id), 1)
    return _entry_view(entry, new_score, position)
