"""Internal-only plane — RS256 internal JWT (aud=gshare-internal), not public.

Routers are aggregated into a single ``internal_router`` mounted (without /api/v1 prefix) by
``app.main.create_app``.
"""
from fastapi import APIRouter

from app.internal import (
    audit_router,
    connect_verify_router,
    imagebuild_status_router,
    inventory_router,
    jwks_router,
    status_router,
    volumes_router,
)

internal_router = APIRouter()
internal_router.include_router(status_router.router)
internal_router.include_router(imagebuild_status_router.router)
internal_router.include_router(connect_verify_router.router)
internal_router.include_router(audit_router.router)
internal_router.include_router(inventory_router.router)
internal_router.include_router(jwks_router.router)
internal_router.include_router(volumes_router.router)
