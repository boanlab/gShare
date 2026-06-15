"""Redis pool + lock/token helpers.

Used for: distributed worker locks (``lock:{job}``), idempotency keys (``idem:...``),
connection-token store (``cnx:{id}``), and the queue ZSET (``gshare:queue``).
"""
from __future__ import annotations

import redis.asyncio as redis

from app.core.config import settings

_pool: redis.Redis | None = None


def get_redis() -> redis.Redis:
    """Process-wide async Redis client (lazy singleton)."""
    global _pool
    if _pool is None:
        _pool = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _pool
