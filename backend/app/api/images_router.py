"""Images router. Catalog + import + image-builds.

Image registry catalog (Harbor abstraction; registry.gshare.internal). Image registration and
imports are persisted in the ``image`` table; container pulls/builds are delegated to async workers
(Kaniko/registry) and reported back via webhooks. This plane never pulls images inline; it records
desired state + ``import_status``/``status`` and lets the registry/build pipeline converge.
"""
from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination, get_current_principal
from app.api.schemas.image import ImageBuildList, ImageList
from app.auth.rbac import Principal
from app.core import ids
from app.core.errors import DomainError, Forbidden, NotFound
from app.db.base import get_db
from app.db.models import Image, ImageBuild, Project
from app.domain.audit_service import AuditService

router = APIRouter(tags=["images"])

_IMAGE_KINDS = {"image", "template", "iso", "container"}
_BUILD_SOURCES = {"dockerfile", "git"}
_BUILD_TERMINAL = {"succeeded", "failed", "cancelled"}


class _Conflict(DomainError):
    code, http = "conflict", 409


class _Validation(DomainError):
    code, http = "validation_failed", 422


# ── request bodies ──
class ImageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    registry: str
    kind: str
    tags: dict[str, Any] = Field(default_factory=dict)
    supported_gpus: list[str] = Field(default_factory=list)   # supported GPU models; empty means all
    cuda_version: str | None = None                            # the image's CUDA version, e.g. '12.4'; empty means unspecified


class ImageImport(BaseModel):
    source_type: str            # registry | url
    source: str
    name: str = Field(min_length=1, max_length=120)
    kind: str
    registry_auth: dict[str, Any] | None = None
    tags: dict[str, Any] = Field(default_factory=dict)
    supported_gpus: list[str] = Field(default_factory=list)   # supported GPU models; empty means all
    cuda_version: str | None = None                            # the image's CUDA version, e.g. '12.4'; empty means unspecified


class BuildCreate(BaseModel):
    group_id: str
    name: str = Field(min_length=1, max_length=120)
    source: str                 # dockerfile | git
    dockerfile: str | None = None
    git_url: str | None = None
    git_ref: str = "main"
    context: str = "."
    build_args: dict[str, Any] = Field(default_factory=dict)
    target_tag: str | None = None


def _serialize_image(img: Image) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": img.id,
        "name": img.name,
        "registry": img.registry,
        "kind": img.kind,
        "tags": img.tags or {},
        # Supported GPU models, by CUDA and cuDNN compatibility. An empty list means every GPU,
        # which is what the session wizard filters on.
        "supported_gpus": (img.tags or {}).get("supported_gpus", []),
        # The image's CUDA version, compared against an offering's min_cuda to filter compatible
        # GPUs.
        "cuda_version": (img.tags or {}).get("cuda_version"),
        # A private image is hidden from the session wizard, though the admin catalogue always lists
        # it.
        "public": getattr(img, "public", True),
        "created_at": img.created_at,
    }
    if img.import_status is not None:
        out["import_status"] = img.import_status
    return out


def _serialize_build(b: ImageBuild) -> dict[str, Any]:
    return {
        "id": b.id,
        "group_id": b.group_id,
        "source": b.source,
        "status": b.status,
        "image_ref": b.image_ref,
        "created_at": b.created_at,
        "finished_at": b.updated_at if b.status in _BUILD_TERMINAL else None,
    }


@router.get("/images", response_model=ImageList)
async def list_images(
    pagination: Pagination = Depends(),
    kind: str | None = Query(default=None),
    q: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    public: bool | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """List images/templates/ISOs with optional kind/q/tag/public filters. any authenticated.

    ``public=true`` is what the session wizard uses, listing public images only. The administrative
    catalogue passes no filter and sees both public and private images.
    """
    base = select(Image)
    if kind is not None:
        if kind not in _IMAGE_KINDS:
            raise _Validation("invalid kind", {"kind": kind})
        base = base.where(Image.kind == kind)
    if public is not None:
        base = base.where(Image.public.is_(public))
    if q:
        like = f"%{q}%"
        base = base.where(or_(Image.name.ilike(like), Image.registry.ilike(like)))
    if tag:
        # tags is a JSONB free key/value map; match the tag as a present value.
        base = base.where(Image.tags.op("@>")({} if tag is None else _tag_filter(tag)))

    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = (
        await db.scalars(
            base.order_by(Image.created_at.desc()).offset(pagination.offset).limit(pagination.size)
        )
    ).all()
    size = pagination.size
    return {
        "data": [_serialize_image(i) for i in rows],
        "pagination": {
            "page": pagination.page,
            "size": size,
            "total": total,
            "total_pages": math.ceil(total / size) if size else 0,
        },
    }


def _tag_filter(tag: str) -> dict[str, Any]:
    """Build a JSONB containment filter from ``key=value`` or a bare value match."""
    if "=" in tag:
        k, v = tag.split("=", 1)
        return {k: v}
    # Bare tag: match any framework-style label set to this value is ambiguous; fall back to
    # a containment on a conventional "tag" key.
    return {"tag": tag}


@router.post("/images", status_code=status.HTTP_201_CREATED)
async def create_image(
    body: ImageCreate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Register a catalog image referencing an existing registry path. super_admin·org_admin."""
    principal.require(action="image.create")

    if body.kind not in _IMAGE_KINDS:
        raise _Validation("invalid kind", {"kind": body.kind})

    # Same registry ref must not be registered twice -> 409 conflict.
    dup = await db.scalar(select(Image.id).where(Image.registry == body.registry))
    if dup is not None:
        raise _Conflict("registry already registered", {"registry": body.registry})

    img_tags = dict(body.tags or {})
    if body.supported_gpus:
        img_tags["supported_gpus"] = body.supported_gpus
    if body.cuda_version and body.cuda_version.strip():
        img_tags["cuda_version"] = body.cuda_version.strip()
    img = Image(
        id=ids.new("image"),
        name=body.name,
        registry=body.registry,
        kind=body.kind,
        tags=img_tags,
        import_status="ready",   # directly-registered refs are assumed present in the registry.
    )
    db.add(img)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise _Conflict("registry already registered", {"registry": body.registry}) from exc

    await AuditService(db).record(
        actor=principal.user_id, action="image.create", target=img.id, result="ok",
        registry=body.registry, kind=body.kind,
    )
    await db.commit()
    return _serialize_image(img)


@router.get("/images/{image_id}")
async def get_image(
    image_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Image detail. any authenticated."""
    img = await db.get(Image, image_id)
    if img is None:
        raise NotFound("image", {"image_id": image_id})
    return _serialize_image(img)


class ImageUpdate(BaseModel):
    name: str | None = None
    public: bool | None = None
    cuda_version: str | None = None            # '' or null clears it
    supported_gpus: list[str] | None = None


@router.patch("/images/{image_id}")
async def update_image(
    image_id: str,
    body: ImageUpdate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Update image metadata: public/private, name, CUDA version, supported GPUs. super_admin, gated
    on image.create."""
    principal.require(action="image.create")
    img = await db.get(Image, image_id)
    if img is None:
        raise NotFound("image", {"image_id": image_id})

    changes: dict[str, Any] = {}
    if body.name is not None and body.name.strip() and body.name != img.name:
        changes["name"] = {"from": img.name, "to": body.name.strip()}
        img.name = body.name.strip()
    if body.public is not None and body.public != img.public:
        changes["public"] = {"from": img.public, "to": body.public}
        img.public = body.public

    # cuda_version and supported_gpus live inside the tags JSONB, so the whole dict has to be
    # replaced for the change to be detected.
    tags = dict(img.tags or {})
    if body.cuda_version is not None:
        new_cuda = body.cuda_version.strip() or None
        if new_cuda != tags.get("cuda_version"):
            changes["cuda_version"] = {"from": tags.get("cuda_version"), "to": new_cuda}
            if new_cuda:
                tags["cuda_version"] = new_cuda
            else:
                tags.pop("cuda_version", None)
    if body.supported_gpus is not None:
        if body.supported_gpus != tags.get("supported_gpus", []):
            changes["supported_gpus"] = {"from": tags.get("supported_gpus", []), "to": body.supported_gpus}
        tags["supported_gpus"] = body.supported_gpus
    img.tags = tags

    await db.flush()
    await AuditService(db).record(
        actor=principal.user_id, action="image.update", target=img.id, result="ok", changes=changes,
    )
    await db.commit()
    return _serialize_image(img)


@router.post("/images/import", status_code=status.HTTP_202_ACCEPTED)
async def import_image(
    body: ImageImport,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Register an image from an external registry/URL; async pull begins. super_admin·org_admin.

    Persists the Image row with ``import_status=pulling`` and returns 202. The registry pull worker
    converges ``import_status`` -> ready|failed and the result is observed via GET /images/{id} or a
    webhook. This plane never pulls inline; ``registry_auth`` is forwarded to the worker
    and never persisted in the catalog row.
    """
    principal.require(action="image.create")

    if body.source_type not in {"registry", "url"}:
        raise _Validation("invalid source_type", {"source_type": body.source_type})
    if body.kind not in _IMAGE_KINDS:
        raise _Validation("invalid kind", {"kind": body.kind})
    if not body.source:
        raise _Validation("source required", {})

    # Idempotency on the resolved registry ref -> 409 on duplicate.
    dup = await db.scalar(select(Image.id).where(Image.registry == body.source))
    if dup is not None:
        raise _Conflict("registry already registered", {"registry": body.source})

    imp_tags = dict(body.tags or {})
    if body.supported_gpus:
        imp_tags["supported_gpus"] = body.supported_gpus
    if body.cuda_version and body.cuda_version.strip():
        imp_tags["cuda_version"] = body.cuda_version.strip()
    img = Image(
        id=ids.new("image"),
        name=body.name,
        registry=body.source,
        kind=body.kind,
        tags=imp_tags,
        import_status="pulling",
    )
    db.add(img)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise _Conflict("registry already registered", {"registry": body.source}) from exc

    await AuditService(db).record(
        actor=principal.user_id, action="image.import", target=img.id, result="accepted",
        source_type=body.source_type, source=body.source,
    )
    await db.commit()
    return {
        "id": img.id,
        "name": img.name,
        "kind": img.kind,
        "registry": img.registry,
        "import_status": img.import_status,
        "created_at": img.created_at,
    }


# ── image-builds ──
@router.post("/image-builds", status_code=status.HTTP_201_CREATED)
async def create_build(
    body: BuildCreate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Start an async image build from a dockerfile/git source. group_admin+ on the project."""
    # Scope the capability check to the target project: group_admin+.
    principal.require(action="image.build", group_id=body.group_id)

    if body.source not in _BUILD_SOURCES:
        raise _Validation("invalid source", {"source": body.source})
    if body.source == "dockerfile" and not body.dockerfile:
        raise _Validation("dockerfile required", {"source": "dockerfile"})
    if body.source == "git" and not body.git_url:
        raise _Validation("git_url required", {"source": "git"})

    project = await db.get(Project, body.group_id)
    if project is None or project.deleted_at is not None:
        raise NotFound("group", {"group_id": body.group_id})

    build = ImageBuild(
        id=ids.new("build"),
        group_id=body.group_id,
        source=body.source,
        status="queued",
        image_ref=None,
    )
    db.add(build)
    await db.flush()

    await AuditService(db).record(
        actor=principal.user_id, action="image.build.create", target=build.id, result="accepted",
        group_id=body.group_id, source=body.source, name=body.name,
    )
    await db.commit()
    return {
        "id": build.id,
        "group_id": build.group_id,
        "name": body.name,
        "source": build.source,
        "status": build.status,
        "image_ref": build.image_ref,
        "created_at": build.created_at,
        "started_at": None,
        "finished_at": None,
    }


@router.get("/image-builds", response_model=ImageBuildList)
async def list_builds(
    pagination: Pagination = Depends(),
    group_id: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    source: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """List builds. member sees own-project builds; restricted to membership projects."""
    base = select(ImageBuild)

    # Non-super_admin principals only see builds in projects they belong to.
    if principal.global_role != "super_admin":
        group_ids = list(principal.memberships.keys())
        if group_id is not None:
            if group_id not in principal.memberships:
                raise Forbidden("not permitted: image-builds.read")
            base = base.where(ImageBuild.group_id == group_id)
        elif group_ids:
            base = base.where(ImageBuild.group_id.in_(group_ids))
        else:
            # No memberships -> empty result set.
            base = base.where(ImageBuild.group_id.is_(None))
    elif group_id is not None:
        base = base.where(ImageBuild.group_id == group_id)

    if status_filter is not None:
        base = base.where(ImageBuild.status == status_filter)
    if source is not None:
        if source not in _BUILD_SOURCES:
            raise _Validation("invalid source", {"source": source})
        base = base.where(ImageBuild.source == source)

    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = (
        await db.scalars(
            base.order_by(ImageBuild.created_at.desc())
            .offset(pagination.offset)
            .limit(pagination.size)
        )
    ).all()
    size = pagination.size
    return {
        "data": [_serialize_build(b) for b in rows],
        "pagination": {
            "page": pagination.page,
            "size": size,
            "total": total,
            "total_pages": math.ceil(total / size) if size else 0,
        },
    }


async def _load_build_for_read(
    build_id: str, principal: Principal, db: AsyncSession
) -> ImageBuild:
    """Fetch a build + enforce read access (owner project member / group_admin+ / super_admin)."""
    build = await db.get(ImageBuild, build_id)
    if build is None:
        raise NotFound("image_build", {"build_id": build_id})
    if principal.global_role == "super_admin":
        return build
    if build.group_id not in principal.memberships:
        raise Forbidden("not permitted: image-builds.read")
    return build


@router.get("/image-builds/{build_id}")
async def get_build(
    build_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Build detail incl. image_ref / scan summary."""
    build = await _load_build_for_read(build_id, principal, db)
    finished = build.status in _BUILD_TERMINAL
    return {
        "id": build.id,
        "group_id": build.group_id,
        "source": build.source,
        "status": build.status,
        "image_ref": build.image_ref,
        "created_at": build.created_at,
        "started_at": build.created_at if build.status not in {"queued"} else None,
        "finished_at": build.updated_at if finished else None,
    }


@router.get("/image-builds/{build_id}/logs")
async def build_logs(
    build_id: str,
    tail: int = Query(default=500, ge=1, le=10000),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Build (Kaniko) stage logs.

    Logs are produced by the build pipeline (registry/Kaniko) and are not persisted in this control
    plane's DB; we return the structured envelope with whatever log lines the pipeline has surfaced
    for this build. With no pipeline attached in-sandbox this is an empty (but well-formed) stream.
    """
    build = await _load_build_for_read(build_id, principal, db)
    return {"build_id": build.id, "lines": []}
