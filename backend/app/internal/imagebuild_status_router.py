"""Internal image-build callback: POST /internal/image-builds/{id}/status.

The operator's ImageBuildReconciler reports kaniko Job progress. On success the pushed ref becomes
a private Image row owned by the requester (public=false — visible to the owner + admins only).
Idempotent: terminal states never regress, the Image row is created once. RS256 internal JWT.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.internal_jwt import require_internal_jwt
from app.core import ids
from app.db.base import get_db
from app.db.models import Image, ImageBuild
from app.domain.audit_service import AuditService
from app.domain.notification_service import NotificationService

router = APIRouter(tags=["internal"])

_TERMINAL = {"succeeded", "failed", "cancelled"}
_PHASES = {"queued", "running", "succeeded", "failed"}


class BuildStatusEvent(BaseModel):
    phase: str
    image_ref: str | None = None
    error: str | None = None
    log_tail: str | None = Field(default=None, max_length=32768)


@router.post("/internal/image-builds/{build_id}/status", status_code=status.HTTP_202_ACCEPTED)
async def report_build_status(
    build_id: str,
    ev: BuildStatusEvent,
    _claims: dict = Depends(require_internal_jwt),   # aud=gshare-internal
    db: AsyncSession = Depends(get_db),
):
    build = await db.get(ImageBuild, build_id, with_for_update=True)
    if build is None or ev.phase not in _PHASES:
        return {"accepted": False}
    if build.status in _TERMINAL:                    # terminal never regresses (retried callbacks)
        return {"accepted": True, "status": build.status}

    now = datetime.now(UTC)
    if ev.log_tail:
        build.log_tail = ev.log_tail
    if ev.phase == "running":
        build.status = "running"
        if build.started_at is None:
            build.started_at = now
    elif ev.phase == "failed":
        build.status = "failed"
        build.error = (ev.error or "build failed")[:2000]
        build.finished_at = now
        if build.owner_user_id:
            await NotificationService(db).notify(
                [build.owner_user_id], "image_build_failed", "Image build failed",
                f"{build.name or build.id}: {build.error}",
                params={"name": build.name or build.id, "error": build.error},
                build_id=build.id,
            )
        await AuditService(db).record(
            actor="operator", action="image.build.finish", target=build.id, result="failed",
            error=build.error,
        )
    elif ev.phase == "succeeded":
        build.status = "succeeded"
        build.finished_at = now
        if ev.image_ref:
            build.image_ref = ev.image_ref
        if build.image_id is None and build.image_ref:
            img = Image(
                id=ids.new("image"),
                name=build.name or build.id,
                registry=build.image_ref,
                kind="container",
                tags={"built_from": build.id},
                import_status="ready",
                public=False,                         # private: owner + admins only
                owner_user_id=build.owner_user_id,
            )
            db.add(img)
            await db.flush()
            build.image_id = img.id
        if build.owner_user_id:
            await NotificationService(db).notify(
                [build.owner_user_id], "image_build_succeeded", "Image build finished",
                f"{build.name or build.id} is ready to use.",
                params={"name": build.name or build.id},
                build_id=build.id,
            )
        await AuditService(db).record(
            actor="operator", action="image.build.finish", target=build.id, result="succeeded",
            image_ref=build.image_ref,
        )
    await db.commit()
    return {"accepted": True, "status": build.status}
