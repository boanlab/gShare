"""Round-trip tests for user token verification (jwt_auth.verify_jwt).

User authentication has a single path: email-and-password login issues an HS256 token signed with
USER_JWT_SECRET, and verify_jwt validates only that token — the OIDC verification path was removed.
These tests assert that a valid token passes and that forged, expired, and malformed ones do not.
"""
from __future__ import annotations

import time

import pytest
from jose import jwt

from app.auth.jwt_auth import verify_jwt
from app.core.config import settings
from app.core.errors import Unauthenticated


def _make_token(**claims) -> str:
    now = int(time.time())
    payload = {
        "sub": "usr_x", "aud": "gshare-user", "global_role": "super_admin",
        "iat": now, "exp": now + 60,
    }
    payload.update(claims)
    return jwt.encode(payload, settings.USER_JWT_SECRET, algorithm="HS256")


async def test_verify_rejects_missing_audience():
    # A token without an audience — a session connect cookie, for instance — must not pass as a user
    # token.
    now = int(time.time())
    no_aud = jwt.encode(
        {"sub": "usr_x", "iat": now, "exp": now + 60}, settings.USER_JWT_SECRET, algorithm="HS256"
    )
    with pytest.raises(Unauthenticated):
        await verify_jwt(f"Bearer {no_aud}")


async def test_verify_valid_token_returns_claims():
    token = _make_token(email="a@b.c", must_change_password=True)
    claims = await verify_jwt(f"Bearer {token}")
    assert claims["sub"] == "usr_x"
    assert claims["global_role"] == "super_admin"
    assert claims["email"] == "a@b.c"
    assert claims["must_change_password"] is True


async def test_verify_rejects_wrong_secret():
    bad = jwt.encode({"sub": "x", "exp": int(time.time()) + 60}, "wrong-secret", algorithm="HS256")
    with pytest.raises(Unauthenticated):
        await verify_jwt(f"Bearer {bad}")


async def test_verify_rejects_expired_token():
    with pytest.raises(Unauthenticated):
        await verify_jwt(f"Bearer {_make_token(exp=int(time.time()) - 10)}")


async def test_verify_rejects_garbage():
    with pytest.raises(Unauthenticated):
        await verify_jwt("Bearer not-a-jwt")


# ── Server-side enforcement of must_change_password, gated in get_current_principal ──

async def test_must_change_password_blocks_normal_endpoints(db):
    from types import SimpleNamespace

    from app.api.deps import get_current_principal
    from app.core.errors import PasswordChangeRequired

    tok = _make_token(must_change_password=True)
    req = SimpleNamespace(url=SimpleNamespace(path="/api/v1/sessions"))
    with pytest.raises(PasswordChangeRequired):
        await get_current_principal(request=req, authorization=f"Bearer {tok}", access_token=None, db=db)


async def test_must_change_password_allows_change_password_path(db):
    from types import SimpleNamespace

    from app.api.deps import get_current_principal
    from app.db.models import User

    # The principal resolver now verifies the account row exists and is not suspended/deleted.
    async with db.begin():
        db.add(User(id="usr_x", email="x@example.com", name="x", status="active"))

    tok = _make_token(must_change_password=True)
    req = SimpleNamespace(url=SimpleNamespace(path="/api/v1/auth/change-password"))
    principal = await get_current_principal(
        request=req, authorization=f"Bearer {tok}", access_token=None, db=db
    )
    assert principal.user_id == "usr_x"


@pytest.mark.asyncio
async def test_suspended_user_is_rejected_on_login_and_token(db, fake_redis):
    """Suspension locks the account out on both paths: fresh login and an existing token."""
    from types import SimpleNamespace

    from app.api.deps import get_current_principal
    from app.api.users_router import _LoginRequest, auth_login
    from app.auth import rbac
    from app.core.errors import Forbidden, Unauthenticated
    from app.db.models import User

    async with db.begin():
        db.add(User(id="usr_susp", email="susp@example.com", name="s", status="suspended"))

    fake_req = SimpleNamespace(headers={}, client=None)
    with pytest.raises(Unauthenticated):
        await auth_login(_LoginRequest(email="susp@example.com", password="x"), fake_req, db)

    rbac._PRINCIPAL_CACHE.clear()
    tok = _make_token(sub="usr_susp")
    req = SimpleNamespace(url=SimpleNamespace(path="/api/v1/sessions"))
    with pytest.raises(Forbidden):
        await get_current_principal(
            request=req, authorization=f"Bearer {tok}", access_token=None, db=db
        )
