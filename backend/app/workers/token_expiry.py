"""token_expiry — token/idempotency-key cleanup, interval 60s.

ConnectionToken (Redis TTL) + leftover idempotency keys cleanup, plus housekeeping of the optional
``connection_token`` audit rows whose ``expires_at`` has passed. The runner
(app.workers.runner) already serializes this under a Redis ``lock:token_expiry`` SET NX EX, so
``run`` performs the work directly and must be idempotent — a failed pass is simply retried next
interval and never blocks the loop.

Scope (Python plane only, — no K8s execution):
  1. Redis ``cnx:*`` one-time connect tokens — Redis EXPIRE reaps most; backstop orphans.
  2. Leftover ``idem:*`` idempotency markers that lost their TTL (defensive bound).
  3. Optional ``connection_token`` audit rows whose ``expires_at`` < now (hard-delete).
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select

from app.core.logging import get_logger
from app.core.redis import get_redis
from app.db.base import get_sessionmaker
from app.db.models import ConnectionToken
from app.domain.connection_token import ConnectionTokenService

log = get_logger(__name__)


async def run() -> None:
    """Sweep expired connect tokens + stale idempotency keys.

    Each phase is wrapped so one failing datastore does not abort the rest of the sweep; the
    overall failure is re-raised to the runner which logs and continues next tick.
    """
    redis_purged = idem_purged = rows_deleted = 0
    errors: list[str] = []

    # 1 + 2 Redis-side sweep: orphaned cnx tokens + stale idem:cnx markers delegated to the
    # service that owns the cnx_ token format, then a broader idem:* backstop here.
    try:
        await ConnectionTokenService().purge_expired()
        idem_purged += await _purge_orphan_idempotency_keys()
    except Exception as exc:  # noqa: BLE001 — never let one phase stop the sweep
        errors.append(f"redis:{exc!r}")
        log.exception("token_expiry: redis sweep failed")

    # 3 DB-side sweep: delete connection_token audit rows past expiry.
    try:
        rows_deleted = await _purge_expired_token_rows()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"db:{exc!r}")
        log.exception("token_expiry: db sweep failed")

    log.info(
        "token_expiry: redis_orphans_bounded=%d idem_keys_bounded=%d cnx_rows_deleted=%d errors=%d",
        redis_purged,
        idem_purged,
        rows_deleted,
        len(errors),
    )
    if errors:
        # Surface to the runner (which catches + logs) so monitoring sees a failed pass.
        raise RuntimeError("; ".join(errors))


async def _purge_orphan_idempotency_keys() -> int:
    """Bound the lifetime of ``idem:*`` markers that lost their 24h TTL.

    Idempotency markers are stored with a 24h TTL (``idem:{ep}:{key}``). A crash between SET and
    EXPIRE can leave a TTL-less key that would otherwise live forever; re-apply a 24h bound SCAN,
    never KEYS, to avoid blocking Redis. ConnectionTokenService already handles ``idem:cnx:*``.
    """
    redis = get_redis()
    bounded = 0
    ttl_24h = 24 * 60 * 60
    async for key in redis.scan_iter(match="idem:*", count=200):
        if key.startswith("idem:cnx:"):
            continue  # owned by ConnectionTokenService.purge_expired()
        if await redis.ttl(key) == -1:  # -1 = exists but no expiry
            await redis.expire(key, ttl_24h)
            bounded += 1
    return bounded


async def _purge_expired_token_rows() -> int:
    """Hard-delete ``connection_token`` rows whose ``expires_at`` has passed.

    The authoritative token store is Redis (TTL-reaped); the DB row is an optional audit artifact,
    so once expired it is safe to remove. Idempotent: a no-op when nothing is expired.
    """
    now = datetime.now(UTC)
    async with get_sessionmaker()() as db:
        expired_ids = list(
            await db.scalars(
                select(ConnectionToken.id).where(ConnectionToken.expires_at < now)
            )
        )
        if not expired_ids:
            return 0
        await db.execute(
            delete(ConnectionToken).where(ConnectionToken.id.in_(expired_ids))
        )
        await db.commit()
        return len(expired_ids)
