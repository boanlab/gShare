"""queue_ticker — re-schedule on capacity return (interval 10s).

Each tick admits queued sessions head-first until the head no longer fits (strict head-of-line:
the queue promises order, so a smaller entry behind a blocked head must wait). Ranking comes from
app.domain.queue_ranking over the PG QueueEntry rows — there is no Redis queue.
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.core.logging import get_logger
from app.core.metrics import QUEUE_DEPTH
from app.db.base import get_sessionmaker
from app.db.models import QueueEntry
from app.domain.scheduler import SchedulerService

log = get_logger(__name__)

# Bound the per-tick drain so a single tick can't spin forever (re-evaluated next tick).
MAX_DEQUEUE_PER_TICK = 50


async def run() -> None:
    """Drain queued sessions onto returned capacity.

    reschedule_from_queue reports its outcome: keep going on "admitted" (capacity may fit more)
    and "skipped" (a stale entry was dropped, the real head is still unexamined); stop on
    "blocked" (head-of-line holds) or "empty".
    """
    maker = get_sessionmaker()
    async with maker() as db:
        QUEUE_DEPTH.set(int(await db.scalar(select(func.count()).select_from(QueueEntry)) or 0))
        await db.commit()  # close the autobegun read tx (the session may be shared in tests)
    for _ in range(MAX_DEQUEUE_PER_TICK):
        async with maker() as db:
            scheduler = SchedulerService(db)
            outcome = await scheduler.reschedule_from_queue()
            await db.commit()
        if outcome in ("blocked", "empty"):
            return
