"""queue_ticker — re-schedule on capacity return (interval 10s).

On operator-reported resource-return events, ZPOPMAX gshare:queue (highest score = top priority)
and make a reschedule decision. Decision only; the desired handoff is performed by the scheduler.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.core.redis import get_redis
from app.db.base import get_sessionmaker
from app.domain.scheduler import QUEUE_KEY, SchedulerService

log = get_logger(__name__)

# Bound the per-tick drain so a single tick can't spin forever (re-evaluated next tick).
MAX_DEQUEUE_PER_TICK = 50


async def run() -> None:
    """Drain queued sessions onto returned capacity.

    Each tick: while the queue has entries and capacity allows, ZPOPMAX the top-priority session
    and re-run admission (SchedulerService.reschedule_from_queue). On still-no-capacity the entry
    is pushed back and we stop (no further progress this tick).
    """
    redis = get_redis()
    if await redis.zcard(QUEUE_KEY) == 0:
        return

    maker = get_sessionmaker()
    for _ in range(MAX_DEQUEUE_PER_TICK):
        before = await redis.zcard(QUEUE_KEY)
        if before == 0:
            return
        async with maker() as db:
            scheduler = SchedulerService(db)
            await scheduler.reschedule_from_queue()
            await db.commit()
        after = await redis.zcard(QUEUE_KEY)
        # No net progress (the top entry was pushed back) -> capacity exhausted; stop this tick.
        if after >= before:
            return
