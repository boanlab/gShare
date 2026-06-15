"""ConnectionTokenService — short-lived, one-time cnx_ tokens in Redis.

The control plane's only connection credential. issue stores only token_hash under cnx:{cnx_id}
with (session_id, kind, owner) and a TTL; redeem is delete-on-use (single-use). Verify/redeem is
called by /internal/connect/verify, behind nginx auth-url.

Note: the Pod-side proxy_token is a *different* layer — created by
the operator as a per-session Secret; this service only handles the Redis cnx_ token.
"""
from __future__ import annotations

import hashlib
import secrets

from app.core import ids
from app.core.config import settings
from app.core.redis import get_redis


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


class ConnectionTokenService:
    def __init__(self):
        self.redis = get_redis()

    async def issue(self, session_id: str, kind: str, owner_user_id: str,
                    ttl: int | None = None) -> str:
        """Issue a one-time token, returning ``{cnx_id}.{secret}`` (plaintext only here)."""
        ttl = ttl or settings.CONNECTION_TOKEN_TTL_SEC
        cnx_id = ids.new("connection")
        tok = secrets.token_urlsafe(32)
        await self.redis.hset(f"cnx:{cnx_id}", mapping={
            "token_hash": _sha256(tok), "session_id": session_id,
            "kind": kind, "owner": owner_user_id,
        })
        await self.redis.expire(f"cnx:{cnx_id}", ttl)
        return f"{cnx_id}.{tok}"

    async def redeem(self, presented: str) -> dict | None:
        """Validate + consume (delete-on-use). Returns the record or None."""
        cnx_id, _, tok = presented.partition(".")
        rec = await self.redis.hgetall(f"cnx:{cnx_id}")
        if not rec or rec.get("token_hash") != _sha256(tok):
            return None
        await self.redis.delete(f"cnx:{cnx_id}")     # one-time
        return rec

    async def purge_expired(self) -> None:
        """token_expiry worker hook. Redis TTL handles most; sweep idempotency keys here.

        Redis EXPIRE already reaps live ``cnx:{id}`` tokens. This sweep is a defensive, idempotent
        backstop: it removes orphaned ``cnx:`` hashes that lost their TTL e.g. a crash between
        ``hset`` and ``expire`` by re-applying the configured TTL so they cannot live forever, and
        clears any stale ``idem:cnx:*`` idempotency markers that are already past TTL. SCAN is used
        (never KEYS) to avoid blocking the server.
        """
        ttl = settings.CONNECTION_TOKEN_TTL_SEC
        async for key in self.redis.scan_iter(match="cnx:*", count=200):
            # A cnx token with no expiry is an orphan (issue always sets one); bound its lifetime.
            if await self.redis.ttl(key) == -1:  # -1 = key exists but has no TTL
                await self.redis.expire(key, ttl)
        async for key in self.redis.scan_iter(match="idem:cnx:*", count=200):
            if await self.redis.ttl(key) == -1:
                await self.redis.expire(key, ttl)
