"""token_expiry — token/idempotency-key cleanup, interval 60s.

Connect tokens live ONLY in Redis (TTL-reaped); there is no DB token table. The runner
(app.workers.runner) already serializes this under a Redis ``lock:token_expiry`` SET NX EX, so
``run`` performs the work directly and must be idempotent — a failed pass is simply retried next
interval and never blocks the loop.

Scope (Python plane only, — no K8s execution):
  1. Redis ``cnx:*`` one-time connect tokens — Redis EXPIRE reaps most; backstop orphans.
  2. Leftover ``idem:*`` idempotency markers that lost their TTL (defensive bound).
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.core.redis import get_redis
from app.domain.connection_token import ConnectionTokenService

log = get_logger(__name__)


async def run() -> None:
    """Sweep expired connect tokens + stale idempotency keys."""
    await ConnectionTokenService().purge_expired()
    idem_bounded = await _purge_orphan_idempotency_keys()
    log.info("token_expiry: idem_keys_bounded=%d", idem_bounded)


async def _purge_orphan_idempotency_keys() -> int:
    """Bound the lifetime of ``idem:*`` markers that lost their 24h TTL.

    Idempotency markers are stored with a 24h TTL (``idem:{ep}:{key}``). A crash between SET and
    EXPIRE can leave a TTL-less key that would otherwise live forever; re-apply a 24h bound SCAN,
    never KEYS, to avoid blocking Redis.
    """
    redis = get_redis()
    bounded = 0
    ttl_24h = 24 * 60 * 60
    async for key in redis.scan_iter(match="idem:*", count=200):
        if await redis.ttl(key) == -1:  # -1 = exists but no expiry
            await redis.expire(key, ttl_24h)
            bounded += 1
    return bounded
