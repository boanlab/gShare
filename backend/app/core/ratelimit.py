"""Fixed-window rate limiting on Redis INCR + EXPIRE.

A window is one Redis counter keyed ``rl:{name}:{window_start}``; the first INCR arms the TTL.
Fixed windows admit up to 2× the limit across a boundary, which is acceptable for the abuse
cases this guards (credential stuffing, login bursts, bulk-endpoint hammering) and keeps the
implementation to one round trip.
"""
from __future__ import annotations

import time

from app.core.errors import RateLimited
from app.core.redis import get_redis


async def check_rate(name: str, limit: int, window_sec: int) -> None:
    """Count one hit against ``name``; raise RateLimited (429) past ``limit`` per window.

    Fails open on Redis errors: availability of login and admin bulk actions must not depend on
    the rate-limit store.
    """
    now = int(time.time())
    window = now - (now % window_sec)
    key = f"rl:{name}:{window}"
    try:
        redis = get_redis()
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window_sec)
    except Exception:  # noqa: BLE001 — fail open; the limiter is protection, not a dependency
        return
    if count > limit:
        raise RateLimited(
            "too many requests; retry later",
            {"retry_after_sec": window + window_sec - now},
        )
