"""Queue router. List / mine / cancel / PATCH priority.

The truth-source is ``QueueEntry`` (PG); the Redis ZSET ``gshare:queue`` mirrors it for dequeue
(ZPOPMAX, highest score = top priority). Score = priority_weight*priority + aging(now-enqueued_at).
``position`` is the 1-based rank by descending score among queued entries.
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
from app.domain.audit_service import AuditService
from app.domain.credit_engine import CreditEngine

router = APIRouter(prefix="/queue", tags=["queue"])

# scoring knobs (sched.* defaults).
QUEUE_ZSET = "gshare:queue"
PRIORITY_WEIGHT = 10
AGING_PER_MIN = 1
AGING_CAP = 100
PRIORITY_MAX = 10  # manual_priority policy ceiling


class _Conflict(DomainError):
    code, http = "conflict", 409


class _Unprocessable(DomainError):
    code, http = "validation_failed", 422


class QueuePriorityPatch(BaseModel):
    priority: int = Field(ge=0)


def _score(entry: QueueEntry, now: datetime) -> float:
    """priority_weight*priority + capped aging(now - enqueued_at)."""
    enq = entry.enqueued_at
    if enq.tzinfo is None:
        enq = enq.replace(tzinfo=UTC)
    waited_min = max((now - enq).total_seconds() / 60.0, 0.0)
    aging = min(waited_min * AGING_PER_MIN, float(AGING_CAP))
    return PRIORITY_WEIGHT * entry.priority + aging


async def _ranked(db: AsyncSession) -> list[tuple[QueueEntry, float]]:
    """All queued entries ordered by descending score (FIFO tiebreak on enqueued_at)."""
    rows = (await db.scalars(select(QueueEntry))).all()
    now = datetime.now(UTC)
    scored = [(e, _score(e, now)) for e in rows]
    scored.sort(key=lambda t: (-t[1], t[0].enqueued_at))
    return scored


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
    data = [
        _entry_view(e, s, pos)
        for pos, (e, s) in enumerate(ranked, start=1)
        if owners.get(e.session_id) == principal.user_id
    ]
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
    session.terminated_at = datetime.now(UTC)
    await db.delete(entry)

    # Mirror removal from the Redis ZSET (truth-source already updated in PG).
    try:
        await get_redis().zrem(QUEUE_ZSET, entry.id)
    except Exception:  # noqa: BLE001  (Redis best-effort mirror; PG is source of truth)
        pass

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
    """Admin reorders the queue. Updates QueueEntry.priority + gshare:queue ZSET score."""
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

    now = datetime.now(UTC)
    new_score = _score(entry, now)
    # Update the Redis ZSET score so dequeue order is re-derived immediately.
    try:
        await get_redis().zadd(QUEUE_ZSET, {entry.id: new_score})
    except Exception:  # noqa: BLE001  (best-effort mirror; PG is source of truth)
        pass

    await AuditService(db).record(
        actor=principal.user_id, action="queue.priority.set", target=entry.id,
        priority=entry.priority,
    )
    await db.commit()

    # Recompute position after the change.
    ranked = await _ranked(db)
    position = next((i for i, (e, _) in enumerate(ranked, start=1) if e.id == entry.id), 1)
    return _entry_view(entry, new_score, position)
