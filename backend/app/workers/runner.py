"""Worker runner — asyncio periodic loop with Redis single-runner locks.

Runs only the Python-plane workers (money/budget/token/queue). Health/cordon, idle reaper,
inventory reconcile are the Go operator's responsibility, NOT here.

    python -m app.workers.runner
"""
from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.metrics import WORKER_JOB_FAILURES
from app.core.redis import get_redis
from app.workers import (
    audit_retention,
    billing_worker,
    budget_rollup,
    credit_refill,
    grace_enforcer,
    pool_rebalancer,
    queue_ticker,
    token_expiry,
    webhook_dispatcher,
)

log = get_logger(__name__)

Job = Callable[[], Awaitable[None]]

# (job_callable, interval_seconds, lock_name)
JOBS: list[tuple[Job, int, str]] = [
    (billing_worker.run, settings.BILLING_INTERVAL_SEC, "billing_worker"),
    (budget_rollup.run, 60, "budget_rollup"),
    (token_expiry.run, 60, "token_expiry"),
    (queue_ticker.run, 10, "queue_ticker"),
    (grace_enforcer.run, 30, "grace_enforcer"),   # graceful pause once the post-exhaustion grace window expires
    (credit_refill.run, 3600, "credit_refill"),   # checks hourly, resets once a month, idempotently
    (audit_retention.run, 86400, "audit_retention"),   # prune audit rows past the retention window
    (webhook_dispatcher.run, 15, "webhook_dispatcher"),  # deliver the webhook outbox
    (pool_rebalancer.run, 30, "pool_rebalancer"),   # drive hami-core<->mig card transitions
]


# Atomic owner-checked release: delete the lock only if this runner still holds it.
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


async def loop(job: Job, interval: int, name: str) -> None:
    """Run ``job`` every ``interval`` seconds under an owner-token Redis lock.

    The TTL is a generous multiple of the interval, NOT equal to it: with TTL == interval, a job
    that ran even slightly long let a second replica start a concurrent run the moment the lock
    expired. The token makes release safe — a runner whose lock DID expire mid-run (crash-length
    overrun) cannot delete the next runner's lock.
    """
    redis = get_redis()
    while True:
        token = secrets.token_hex(8)
        ttl = max(interval * 3, 300)
        try:
            if await redis.set(f"lock:{name}", token, nx=True, ex=ttl):
                try:
                    await job()
                finally:
                    try:
                        await redis.eval(_RELEASE_LUA, 1, f"lock:{name}", token)
                    except Exception:  # noqa: BLE001 — TTL reclaims the lock if release fails
                        pass
        except Exception:  # noqa: BLE001 — a failing job must not stop the loop
            WORKER_JOB_FAILURES.labels(job=name).inc()
            log.exception("worker %s failed", name)
        await asyncio.sleep(interval)


async def main() -> None:
    configure_logging()
    log.info("starting GShare workers: %s", [n for _, _, n in JOBS])
    await asyncio.gather(*(loop(j, i, n) for j, i, n in JOBS))


if __name__ == "__main__":
    asyncio.run(main())
