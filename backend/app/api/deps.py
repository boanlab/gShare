"""Shared FastAPI dependencies: auth, pagination, idempotency key."""
from __future__ import annotations

from fastapi import Depends, Header, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt_auth import verify_jwt
from app.auth.rbac import Principal, resolve_principal
from app.core.errors import IdempotencyKeyRequired, PasswordChangeRequired, Unauthenticated
from app.db.base import get_db, get_sessionmaker

# The only paths reachable while must_change_password is set: read your own profile and change the
# password. Every other endpoint is blocked.
_PWCHANGE_ALLOWED = ("/auth/change-password", "/auth/me")


async def get_current_principal(
    request: Request,
    authorization: str | None = Header(default=None, description="Bearer <jwt>"),
    access_token: str | None = Query(
        default=None,
        description="Token fallback for clients that cannot set custom headers, such as EventSource (SSE)",
    ),
    db: AsyncSession = Depends(get_db),
) -> Principal:
    """Verify the bearer JWT and resolve the Principal (global_role ∪ Membership).

    The canonical form is the ``Authorization: Bearer <jwt>`` header, but a browser ``EventSource``
    (SSE) cannot set custom headers, so ``?access_token=<jwt>`` is accepted as well. With neither,
    the response is 401 unauthenticated rather than FastAPI's default 422. """
    token = authorization
    if token is None and access_token:
        token = f"Bearer {access_token}"
    if token is None:
        raise Unauthenticated("missing credentials")
    claims = await verify_jwt(token)      # -> 401 unauthenticated on failure
    # Force a password change after a first login or a reset: until it happens, everything except
    # /auth/me and change-password is blocked server-side.
    if claims.get("must_change_password") and not request.url.path.endswith(_PWCHANGE_ALLOWED):
        raise PasswordChangeRequired("password change required")
    principal = await resolve_principal(db, claims)
    # resolve_principal's SELECT auto-begins a transaction on the session. This dependency is
    # read-only, so rolling back immediately closes it and lets the endpoint's unit of work (`async
    # with db.begin`) start a fresh transaction without colliding — SQLAlchemy 2.0 autobegin.
    await db.rollback()
    return principal


async def get_sse_principal(
    request: Request,
    authorization: str | None = Header(default=None, description="Bearer <jwt>"),
    access_token: str | None = Query(
        default=None,
        description="Token fallback for clients that cannot set custom headers, such as EventSource (SSE)",
    ),
) -> Principal:
    """get_current_principal for SSE endpoints: identical auth, but the DB session used to
    resolve the Principal is opened and CLOSED here, before the stream starts.

    A ``Depends(get_db)`` session lives until the response finishes — for an
    ``EventSourceResponse`` that is the whole stream, so every open browser tab would pin one
    pooled Postgres connection. Long-lived streams must therefore never take the request-scoped
    session; they authenticate through this dependency and hold no connection afterwards.
    """
    token = authorization
    if token is None and access_token:
        token = f"Bearer {access_token}"
    if token is None:
        raise Unauthenticated("missing credentials")
    claims = await verify_jwt(token)
    if claims.get("must_change_password") and not request.url.path.endswith(_PWCHANGE_ALLOWED):
        raise PasswordChangeRequired("password change required")
    async with get_sessionmaker()() as db:
        principal = await resolve_principal(db, claims)
        await db.rollback()
    return principal


class Pagination:
    """Standard page/size pagination."""

    def __init__(
        self,
        page: int = Query(1, ge=1),
        size: int = Query(20, ge=1, le=100),
    ):
        self.page = page
        self.size = size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


async def idempotency_key(
    key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> str | None:
    """Return the Idempotency-Key header (None if absent). Routers that require it call
    require_idem."""
    return key


def require_idem(key: str | None) -> str:
    """Raise 400 idempotency_key_required if missing."""
    if key is None:
        raise IdempotencyKeyRequired()
    return key
