"""Fixed-window rate limiter tests (fakeredis via the autouse fixture)."""
from __future__ import annotations

import pytest

from app.core.errors import RateLimited
from app.core.passwords import hash_password_async, verify_password_async
from app.core.ratelimit import check_rate


@pytest.mark.asyncio
async def test_check_rate_allows_up_to_limit_then_429():
    for _ in range(5):
        await check_rate("t:unit", limit=5, window_sec=60)
    with pytest.raises(RateLimited) as excinfo:
        await check_rate("t:unit", limit=5, window_sec=60)
    assert excinfo.value.http == 429
    assert excinfo.value.code == "rate_limited"
    assert excinfo.value.details["retry_after_sec"] <= 60


@pytest.mark.asyncio
async def test_check_rate_keys_are_independent():
    for _ in range(5):
        await check_rate("t:a", limit=5, window_sec=60)
    # a different key is a different window counter
    await check_rate("t:b", limit=5, window_sec=60)


@pytest.mark.asyncio
async def test_async_password_wrappers_roundtrip():
    stored = await hash_password_async("hunter2-hunter2")
    assert await verify_password_async("hunter2-hunter2", stored)
    assert not await verify_password_async("wrong", stored)
