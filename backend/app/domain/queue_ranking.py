"""The single queue-ranking authority.

The Postgres ``QueueEntry`` table IS the queue; there is no Redis mirror. Order is derived at
read time — the router views, the ticker's dequeue, and any notification therefore always agree,
and there is no second scoring scheme to drift (the previous design kept a Redis ZSET that two
modules scored differently and keyed by different members).

    score = PRIORITY_WEIGHT * priority + min(waited_minutes, AGING_CAP_MIN)

``PRIORITY_WEIGHT`` is far above the aging cap so an admin-set priority band always outranks any
wait time; within a band the queue is FIFO (aging, tie-broken by enqueued_at). The queue is small
by construction — per-user ``max_queued`` policy bounds it — so ranking all rows in Python per
call is fine at this scale.

The fairness terms keep free-use sharing honest without full fair-share machinery. In one
sentence for the console tooltip: *waiting longer moves you up; sessions you already have
running, or heavy GPU use in the last 24 hours, move you down; admin priority overrides both.*
The constants guarantee that a user with no usage eventually outranks any non-priority heavy
user: the maximum combined penalty is strictly smaller than the aging cap.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Allocation, QueueEntry, Session

# One priority step (admin-set, 0..10) dominates the whole aging range and every penalty.
PRIORITY_WEIGHT = 10_000.0
# Waiting stops earning score after a day; keeps scores bounded and explainable.
AGING_CAP_MIN = 1440.0

_ACTIVE_STATUSES = ("pending", "preparing", "running", "paused", "terminating")


def score(
    entry: QueueEntry,
    now: datetime | None = None,
    *,
    active_sessions: int = 0,
    recent_gpu_hours: float = 0.0,
) -> float:
    """priority band + capped minutes waited − usage penalties (bounded below the aging cap)."""
    now = now or datetime.now(UTC)
    enq = entry.enqueued_at
    if enq.tzinfo is None:
        enq = enq.replace(tzinfo=UTC)
    waited_min = max((now - enq).total_seconds() / 60.0, 0.0)
    penalty = (
        settings.QUEUE_ACTIVE_PENALTY * min(active_sessions, 10)
        + settings.QUEUE_RECENT_HOURS_PENALTY * min(recent_gpu_hours, 24.0)
    )
    return PRIORITY_WEIGHT * entry.priority + min(waited_min, AGING_CAP_MIN) - penalty


async def _usage_by_owner(
    db: AsyncSession, owner_ids: set[str], now: datetime
) -> tuple[dict[str, int], dict[str, float]]:
    """Two grouped queries: active session counts and GPU hours overlapping the last 24h."""
    if not owner_ids:
        return {}, {}
    active_rows = (
        await db.execute(
            select(Session.owner_user_id, func.count())
            .where(
                Session.owner_user_id.in_(owner_ids),
                Session.status.in_(_ACTIVE_STATUSES),
                Session.deleted_at.is_(None),
                Session.resource_class == "gpu",
            )
            .group_by(Session.owner_user_id)
        )
    ).all()
    active = {uid: int(cnt) for uid, cnt in active_rows}

    window_start = now - timedelta(hours=24)
    alloc_rows = (
        await db.execute(
            select(Session.owner_user_id, Allocation.started_at, Allocation.ended_at)
            .join(Session, Session.id == Allocation.session_id)
            .where(
                Session.owner_user_id.in_(owner_ids),
                Allocation.started_at.is_not(None),
                # Overlaps the window: started before now and not ended before window start.
                (Allocation.ended_at.is_(None)) | (Allocation.ended_at >= window_start),
            )
        )
    ).all()
    hours: dict[str, float] = {}
    for uid, started, ended in alloc_rows:
        if started is None:
            continue
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        end = ended or now
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
        overlap = (min(end, now) - max(started, window_start)).total_seconds()
        if overlap > 0:
            hours[uid] = hours.get(uid, 0.0) + overlap / 3600.0
    return active, hours


async def rank(db: AsyncSession) -> list[tuple[QueueEntry, float]]:
    """Every queued entry, ordered by descending score with FIFO tiebreak.

    Usage penalties are computed once for the whole queue (two grouped queries), so ranking a
    few-hundred-entry queue stays cheap.
    """
    rows = (await db.scalars(select(QueueEntry))).all()
    if not rows:
        return []
    now = datetime.now(UTC)
    owners = {
        sid: uid
        for sid, uid in (
            await db.execute(
                select(Session.id, Session.owner_user_id).where(
                    Session.id.in_([e.session_id for e in rows])
                )
            )
        ).all()
    }
    active, hours = await _usage_by_owner(db, set(owners.values()), now)
    scored = []
    for e in rows:
        uid = owners.get(e.session_id, "")
        scored.append((e, score(
            e, now,
            active_sessions=active.get(uid, 0),
            recent_gpu_hours=hours.get(uid, 0.0),
        )))
    scored.sort(key=lambda t: (-t[1], t[0].enqueued_at))
    return scored


async def head(db: AsyncSession) -> QueueEntry | None:
    """The entry that dequeues next, or None when the queue is empty."""
    ranked = await rank(db)
    return ranked[0][0] if ranked else None
