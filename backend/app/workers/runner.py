"""Worker runner — asyncio periodic loop with Redis single-runner locks.

Runs only the Python-plane workers (money/budget/token/queue). Health/cordon, idle reaper,
inventory reconcile are the Go operator's responsibility, NOT here.

    python -m app.workers.runner
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.redis import get_redis
from app.workers import (
    billing_worker,
    budget_rollup,
    credit_refill,
    grace_enforcer,
    queue_ticker,
    snapshot_completer,
    storage_billing,
    token_expiry,
)

log = get_logger(__name__)

Job = Callable[[], Awaitable[None]]

# (job_callable, interval_seconds, lock_name)
JOBS: list[tuple[Job, int, str]] = [
    (billing_worker.run, settings.BILLING_INTERVAL_SEC, "billing_worker"),
    (storage_billing.run, settings.BILLING_INTERVAL_SEC, "storage_billing"),  # bills provisioned volume capacity
    (budget_rollup.run, 60, "budget_rollup"),
    (token_expiry.run, 60, "token_expiry"),
    (queue_ticker.run, 10, "queue_ticker"),
    (grace_enforcer.run, 30, "grace_enforcer"),   # graceful pause once the post-exhaustion grace window expires
    (snapshot_completer.run, 15, "snapshot_completer"),
    (credit_refill.run, 3600, "credit_refill"),   # checks hourly, resets once a month, idempotently
]


async def loop(job: Job, interval: int, name: str) -> None:
    """Run ``job`` every ``interval`` seconds under a Redis SET NX EX lock."""
    redis = get_redis()
    while True:
        try:
            if await redis.set(f"lock:{name}", "1", nx=True, ex=interval):
                await job()
        except Exception:  # noqa: BLE001 — a failing job must not stop the loop
            log.exception("worker %s failed", name)
        await asyncio.sleep(interval)


async def main() -> None:
    configure_logging()
    log.info("starting GShare workers: %s", [n for _, _, n in JOBS])
    await asyncio.gather(*(loop(j, i, n) for j, i, n in JOBS))


if __name__ == "__main__":
    asyncio.run(main())
