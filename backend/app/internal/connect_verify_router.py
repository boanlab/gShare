"""Internal connect verify: GET /internal/connect/verify.

The nginx ingress forward-auth (auth-url) calls this as a subrequest on every request. A cnx_ token
is single-use and cannot be redeemed once per asset on a page, so this follows the standard
forward-auth pattern:

  1. A valid signed cookie (gshare_session) returns 200, letting the request through.
  2. Otherwise the cnx_ token is redeemed once — from ?token=, which the ingress populates from the
     original request's ?gshare_cnx — the owner and the running session are verified, and the response
     is 200 with a short-lived signed Set-Cookie carrying the session id and owner.

The cnx_ token is itself an owner-bound, short-lived, single-use credential, so browser navigation
needs no separate JWT header — which the browser could not attach anyway.
"""
from __future__ import annotations

import time
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Depends, Request, Response
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import Unauthenticated
from app.db.base import get_db
from app.db.models import Session
from app.domain.connection_token import ConnectionTokenService

router = APIRouter(tags=["internal"])

COOKIE_NAME = "gshare_session"
_COOKIE_ALG = "HS256"


def get_token_service() -> ConnectionTokenService:
    return ConnectionTokenService()


def _make_cookie(session_id: str, owner: str) -> str:
    ttl = settings.CONNECTION_TOKEN_TTL_SEC * 12  # the connect cookie outlives the cnx token, covering asset loads and reconnects
    return jwt.encode(
        {"sid": session_id, "owner": owner, "exp": int(time.time()) + ttl},
        settings.USER_JWT_SECRET, algorithm=_COOKIE_ALG,
    )


def _verify_cookie(raw: str | None) -> str | None:
    """Return the session_id when the cookie is valid, otherwise None."""
    if not raw:
        return None
    try:
        claims = jwt.decode(raw, settings.USER_JWT_SECRET, algorithms=[_COOKIE_ALG])
    except JWTError:
        return None
    return claims.get("sid")


def _cnx_token(request: Request, token_qs: str | None) -> str | None:
    """Locate the cnx token, in order: ?token= for a direct call, then the gshare_cnx query
    parameter inside the X-Original-URI header the ingress sends, then an X-Gshare-Cnx header
    when an auth-snippet is in use.

    ingress-nginx's forward-auth passes the original request URI as X-Original-URI, which is where
    the session URL's ?gshare_cnx= is parsed from."""
    if token_qs:
        return token_qs
    orig = request.headers.get("x-original-uri") or request.headers.get("x-original-url") or ""
    if orig:
        vals = parse_qs(urlsplit(orig).query).get("gshare_cnx")
        if vals:
            return vals[0]
    return request.headers.get("x-gshare-cnx")


async def _running(db: AsyncSession, session_id: str | None) -> Session | None:
    if not session_id:
        return None
    sess = await db.scalar(
        select(Session).where(Session.id == session_id, Session.deleted_at.is_(None))
    )
    return sess if (sess and sess.status == "running") else None


@router.get("/internal/connect/verify")
async def connect_verify(
    request: Request,
    token: str | None = None,
    tok_svc: ConnectionTokenService = Depends(get_token_service),
    db: AsyncSession = Depends(get_db),
):
    # 1. An already-issued, still-valid session cookie passes, which covers a page's many asset
    #    requests.
    sid = _verify_cookie(request.cookies.get(COOKIE_NAME))
    if sid and await _running(db, sid):
        return Response(status_code=200, headers={"X-GShare-Session": sid})

    # 2. Otherwise redeem the cnx_ token once and issue the cookie, parsing ?gshare_cnx out of
    #    X-Original-URI.
    cnx = _cnx_token(request, token)
    rec = await tok_svc.redeem(cnx) if cnx else None
    if rec is None:
        raise Unauthenticated("invalid connection token")
    session_id = rec.get("session_id")
    if await _running(db, session_id) is None:
        raise Unauthenticated("invalid connection token")
    cookie = _make_cookie(session_id, rec["owner"])
    max_age = settings.CONNECTION_TOKEN_TTL_SEC * 12
    return Response(
        status_code=200,
        headers={
            "X-GShare-User": rec["owner"],
            "X-GShare-Session": session_id,
            "Set-Cookie": (
                f"{COOKIE_NAME}={cookie}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"
            ),
        },
    )
