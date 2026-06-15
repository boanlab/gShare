"""Internal JWKS publication: GET /.well-known/gshare-internal-jwks.json.

Publishes the RS256 *public* keys (current + previous kid for rotation) used to verify the
internal plane-to-plane JWT. Public — no auth.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.auth.internal_jwt import load_internal_jwks

router = APIRouter(tags=["internal"])


@router.get("/.well-known/gshare-internal-jwks.json")
async def internal_jwks():
    return await load_internal_jwks()   # external-secrets RS256 public keys (kid rotation)
