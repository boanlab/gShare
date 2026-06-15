"""FastAPI app factory.

Mounts the public API routers under /api/v1 and the internal router (RS256 internal JWT,
aud=gshare-internal) without that prefix. Adds /healthz and X-Request-Id middleware.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.core.logging import get_logger

log = get_logger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):  # noqa: ANN202
    # Validate the required secrets; an empty one fails fast.
    from app.core.config import settings
    settings.require_runtime_secrets()
    # Bootstrap seeding: the administrator account, the all-in-one local cluster, and the catalogue.
    # A failure here is logged but must not stop the application from starting.
    try:
        from app.auth.bootstrap import (
            seed_base_images,
            seed_bootstrap_admin,
            seed_default_policy,
            seed_local_cluster,
            seed_offerings,
            seed_presets,
            seed_system_wallet,
        )
        await seed_bootstrap_admin()
        await seed_local_cluster()
        await seed_base_images()
        await seed_system_wallet()
        await seed_offerings()
        await seed_presets()
        await seed_default_policy()
    except Exception:  # noqa: BLE001
        log.exception("bootstrap seed failed")
    yield

from app.api import (
    audit_router,
    budgets_router,
    clusters_router,
    credits_router,
    dashboard_router,
    groups_router,
    images_router,
    infra_router,
    notifications_router,
    offerings_router,
    policies_router,
    presets_router,
    queue_router,
    sessions_router,
    users_router,
    volumes_router,
    webhooks_router,
)
from app.core.errors import register_exception_handlers
from app.internal import internal_router

# Public REST routers mounted under /api/v1.
API_ROUTERS = (
    users_router,
    groups_router,
    credits_router,
    budgets_router,
    offerings_router,
    presets_router,
    policies_router,
    sessions_router,
    queue_router,
    volumes_router,
    clusters_router,
    images_router,
    notifications_router,
    audit_router,
    webhooks_router,
    dashboard_router,
    infra_router,
)


def create_app() -> FastAPI:
    app = FastAPI(
        title="GShare API",
        version="v1",
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
        lifespan=_lifespan,
    )
    register_exception_handlers(app)

    @app.middleware("http")
    async def _request_id(request: Request, call_next):  # noqa: ANN202
        rid = request.headers.get("X-Request-Id", str(uuid.uuid4()))
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        response.headers["X-GShare-Api-Version"] = "v1"
        return response

    for r in API_ROUTERS:
        app.include_router(r.router, prefix="/api/v1")

    # Internal plane (no /api/v1 prefix): status / connect-verify / audit / jwks.
    app.include_router(internal_router)

    @app.get("/healthz", tags=["health"])
    async def healthz():
        return {"status": "ok"}

    return app


app = create_app()
