"""User bearer-JWT verification.

User tokens are HS256, signed with ``USER_JWT_SECRET`` and issued by the email+password login
(``/auth/login``). This module verifies the ``Authorization: Bearer <jwt>`` header and returns the
claims. There is no API Key path.
"""
from __future__ import annotations

from jose import jwt

from app.core.config import settings
from app.core.errors import Unauthenticated


async def verify_jwt(authorization: str) -> dict:
    """Verify a ``Bearer <jwt>`` header, returning claims, or raise Unauthenticated (401).

    Claims: sub=usr_ULID, global_role(super_admin|null), email,
    must_change_password, optional group_id.
    """
    token = authorization.removeprefix("Bearer ").strip()
    try:
        # Require aud=gshare-user, so a session connect cookie — same secret, different audience —
        # cannot pass as a user token. require_aud=True also rejects tokens with no audience at all,
        # which jose would otherwise skip checking.
        return jwt.decode(
            token, settings.USER_JWT_SECRET, algorithms=["HS256"],
            audience="gshare-user", options={"require_aud": True},
        )
    except Exception as exc:  # noqa: BLE001
        raise Unauthenticated("invalid token") from exc
