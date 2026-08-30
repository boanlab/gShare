"""Images router. Catalog + import + image-builds.

Image registry catalog (Harbor abstraction; registry.gshare.internal). Image registration and
imports are persisted in the ``image`` table; container pulls/builds are delegated to async workers
(Kaniko/registry) and reported back via webhooks. This plane never pulls images inline; it records
desired state + ``import_status``/``status`` and lets the registry/build pipeline converge.
"""
from __future__ import annotations

import math
import re
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination, get_current_principal
from app.api.schemas.image import ImageBuildList, ImageList
from app.auth.rbac import Principal, rbac_allows
from app.cluster.crd import GShareImageBuildCRD
from app.core import ids
from app.core.config import settings
from app.core.errors import DomainError, Forbidden, NotFound, NotImplementedFeature
from app.db.base import get_db
from app.db.models import Cluster, Image, ImageBuild, Project
from app.db.models import Session as SessionRow
from app.domain.audit_service import AuditService

router = APIRouter(tags=["images"])

_IMAGE_KINDS = {"image", "template", "iso", "container"}
_BUILD_SOURCES = {"dockerfile", "git"}
_BUILD_TERMINAL = {"succeeded", "failed", "cancelled"}

# How many images one member may own at a time (built + imported combined). Admin-owned shared
# catalog rows (owner_user_id NULL) are not counted against anyone.
MAX_USER_IMAGES = 20

# Minimal shape check for a registry reference: [host[:port]/]path[:tag][@digest]. Deliberately
# permissive about tags — any tag is allowed — and only meant to reject obviously malformed input
# (whitespace, shell metacharacters, empty segments) before it reaches a node's container runtime.
_REF_SEG = r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*"
_IMAGE_REF_RE = re.compile(
    rf"^(?:{_REF_SEG}(?::[0-9]+)?/)?"              # optional registry host[:port]/
    rf"{_REF_SEG}(?:/{_REF_SEG})*"                 # repository path
    r"(?::[A-Za-z0-9_][A-Za-z0-9._-]{0,127})?"     # optional :tag
    r"(?:@[A-Za-z0-9]+:[A-Fa-f0-9]{32,})?$"        # optional @digest
)


class _Conflict(DomainError):
    code, http = "conflict", 409


class _Validation(DomainError):
    code, http = "validation_failed", 422


class _ImageLimit(DomainError):
    # A member may own only MAX_USER_IMAGES images; the cap keeps one user from filling the
    # catalogue (and the build registry) on their own.
    code, http = "image_limit_reached", 409


class _NoCluster(DomainError):
    code, http = "no_cluster", 503


class _HandoffFailed(DomainError):
    code, http = "build_handoff_failed", 502


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
        "owner_user_id": img.owner_user_id,
        "created_at": img.created_at,
    }
    if img.import_status is not None:
        out["import_status"] = img.import_status
    return out


def _serialize_build(b: ImageBuild) -> dict[str, Any]:
    return {
        "id": b.id,
        "group_id": b.group_id,
        "owner_user_id": b.owner_user_id,
        "name": b.name,
        "source": b.source,
        "status": b.status,
        "error": b.error,
        "image_ref": b.image_ref,
        "image_id": b.image_id,
        "created_at": b.created_at,
        "started_at": b.started_at,
        "finished_at": b.finished_at,
    }


@router.get("/images", response_model=ImageList)
async def list_images(
    pagination: Pagination = Depends(),
    kind: str | None = Query(default=None),
    q: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    public: bool | None = Query(default=None),
    mine: bool = Query(default=False),
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
        if public:
            # Session wizard: the shared catalog PLUS the caller's own private (built) images.
            base = base.where(or_(Image.public.is_(True), Image.owner_user_id == principal.user_id))
        else:
            base = base.where(Image.public.is_(False))
    if mine:
        base = base.where(Image.owner_user_id == principal.user_id)
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
    registry: str | None = None                # full image reference; sessions already created keep their spec
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
    if body.registry is not None and body.registry.strip() and body.registry.strip() != img.registry:
        changes["registry"] = {"from": img.registry, "to": body.registry.strip()}
        img.registry = body.registry.strip()

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


@router.delete("/images/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_image(
    image_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Delete a catalogue image that no session has ever used. super_admin, gated on image.create.

    Sessions keep a foreign key to their image for the audit trail, so an image with any session
    history cannot be deleted — retire it instead by setting ``public: false``.

    Owners may delete their OWN built (private) images; everything else stays admin-gated.
    """
    img = await db.get(Image, image_id)
    if img is None:
        raise NotFound("image", {"image_id": image_id})
    if not (img.owner_user_id and img.owner_user_id == principal.user_id):
        principal.require(action="image.create")
    referenced = await db.scalar(
        select(func.count()).select_from(SessionRow).where(SessionRow.image_id == image_id)
    )
    if referenced:
        raise _Conflict(
            "image is referenced by sessions; retire it with public=false instead",
            {"image_id": image_id, "session_count": int(referenced or 0)},
        )
    await db.delete(img)
    await AuditService(db).record(
        actor=principal.user_id, action="image.delete", target=image_id, result="ok",
        changes={"name": img.name, "registry": img.registry},
    )
    await db.commit()


def _clean_source(source_type: str, source: str) -> str:
    """Normalize and minimally validate the import source.

    A registry reference must look like ``[host[:port]/]path[:tag][@digest]``; a URL source only has
    to be non-empty and whitespace-free. Nothing here contacts the registry — a reference that is
    well-formed but missing surfaces as an ImagePullBackOff on the node when a session starts.
    """
    ref = (source or "").strip()
    if not ref:
        raise _Validation("source required", {})
    if len(ref) > 512 or any(ch.isspace() for ch in ref):
        raise _Validation("invalid image reference", {"source": source})
    if source_type == "registry" and not _IMAGE_REF_RE.fullmatch(ref):
        raise _Validation("invalid image reference", {"source": source})
    return ref


async def _assert_image_quota(db: AsyncSession, user_id: str) -> None:
    """Refuse a new owned image once the member holds MAX_USER_IMAGES (built + imported)."""
    owned = await db.scalar(
        select(func.count()).select_from(Image).where(Image.owner_user_id == user_id)
    )
    if (owned or 0) >= MAX_USER_IMAGES:
        raise _ImageLimit(
            "image limit reached; delete one of your images first",
            {"owned": int(owned or 0), "limit": MAX_USER_IMAGES},
        )


def _import_result(img: Image, *, existing: bool) -> dict[str, Any]:
    return {
        "id": img.id,
        "name": img.name,
        "kind": img.kind,
        "registry": img.registry,
        "import_status": img.import_status,
        "public": img.public,
        "owner_user_id": img.owner_user_id,
        "existing": existing,
        "created_at": img.created_at,
    }


@router.post("/images/import", status_code=status.HTTP_202_ACCEPTED)
async def import_image(
    body: ImageImport,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Register an image from an external registry reference. any authenticated.

    Registration is synchronous catalog metadata: the actual pull happens on the GPU node's
    container runtime when a session using the image first starts (there is no control-plane pull
    worker). The row is therefore created ``import_status=ready`` immediately. Private-registry
    credentials are not supported yet — imagePullSecrets plumbing does not exist — so a request
    carrying ``registry_auth`` is refused rather than silently losing the credential.

    Who owns the row:

    * ``image.create`` holders (super_admin) register SHARED catalog entries — ``owner_user_id``
      null, ``public`` as given. Re-registering a ref that already exists as a shared row is a 409.
    * everyone else registers a PRIVATE row they own (``public=false``, ``kind=container``), which
      only they and admins can see. The same public ref may therefore be imported by many members.

    Always answers 202. Deduplication never fails the request: re-importing your own ref, or a ref
    that is already in the shared catalogue, returns that row with ``existing: true`` instead of
    creating a second one — the caller uses the returned ``id`` either way.
    """
    if getattr(body, "registry_auth", None):
        raise NotImplementedFeature(
            "private registry credentials are not supported yet; the image must be publicly pullable"
        )

    if body.source_type not in {"registry", "url"}:
        raise _Validation("invalid source_type", {"source_type": body.source_type})
    if body.kind not in _IMAGE_KINDS:
        raise _Validation("invalid kind", {"kind": body.kind})
    ref = _clean_source(body.source_type, body.source)

    shared_catalog = rbac_allows(principal, "image.create")

    if shared_catalog:
        # Admin path, unchanged: one shared row per ref.
        dup = await db.scalar(
            select(Image.id).where(Image.registry == ref, Image.owner_user_id.is_(None))
        )
        if dup is not None:
            raise _Conflict("registry already registered", {"registry": ref})
    else:
        # Users may not bring their own images any more: everything a session can run comes
        # from the administrator-curated catalogue (build or import on the admin side). A
        # member import used to mint a private row here; that path is closed for security.
        raise Forbidden("importing images is restricted to administrators")

    imp_tags = dict(body.tags or {})
    if body.supported_gpus:
        imp_tags["supported_gpus"] = body.supported_gpus
    if body.cuda_version and body.cuda_version.strip():
        imp_tags["cuda_version"] = body.cuda_version.strip()
    img = Image(
        id=ids.new("image"),
        name=body.name,
        registry=ref,
        kind=body.kind if shared_catalog else "container",
        tags=imp_tags,
        import_status="ready",
        public=True if shared_catalog else False,
        owner_user_id=None if shared_catalog else principal.user_id,
    )
    db.add(img)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise _Conflict("registry already registered", {"registry": ref}) from exc

    await AuditService(db).record(
        actor=principal.user_id, action="image.import", target=img.id, result="accepted",
        source_type=body.source_type, source=ref, shared=shared_catalog,
    )
    await db.commit()
    return _import_result(img, existing=False)


# ── image-builds ──
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,61}$")


async def _pick_build_cluster(db: AsyncSession) -> Cluster:
    """Builds run on the primary ready cluster (kaniko needs no GPU)."""
    row = (
        await db.scalars(
            select(Cluster)
            .where(Cluster.deleted_at.is_(None), Cluster.status.in_(("ready", "connected")))
            .order_by((Cluster.role != "primary").asc(), Cluster.created_at.asc())
            .limit(1)
        )
    ).first()
    if row is None:
        raise _NoCluster("no build cluster available")
    return row


@router.post("/image-builds", status_code=status.HTTP_201_CREATED)
async def create_build(
    body: BuildCreate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Create a console image build: record desired state, then hand the GShareImageBuild CR to
    the operator (which runs kaniko and reports back via /internal/image-builds/{id}/status).

    Members build for themselves; the pushed image lands as a PRIVATE Image row
    (owner + admins only). One build in flight per user.
    """
    principal.require(action="image.build", group_id=body.group_id)

    if body.source not in _BUILD_SOURCES:
        raise _Validation("invalid source", {"source": body.source})
    if body.source == "dockerfile" and not (body.dockerfile or "").strip():
        raise _Validation("dockerfile required", {"source": "dockerfile"})
    if body.source == "git" and not body.git_url:
        raise _Validation("git_url required", {"source": "git"})
    if body.dockerfile and len(body.dockerfile) > 32768:
        raise _Validation("dockerfile too large (max 32KB)", {})
    name = body.name.strip().lower()
    if not _NAME_RE.fullmatch(name):
        raise _Validation("invalid image name (lowercase letters, digits, . _ -)", {"name": body.name})
    tag = (body.target_tag or "latest").strip().lower()
    if not _NAME_RE.fullmatch(tag):
        raise _Validation("invalid tag", {"tag": body.target_tag})

    project = await db.get(Project, body.group_id)
    if project is None or project.deleted_at is not None:
        raise NotFound("group", {"group_id": body.group_id})

    active = await db.scalar(
        select(func.count()).select_from(ImageBuild).where(
            ImageBuild.owner_user_id == principal.user_id,
            ImageBuild.status.in_(("pending", "queued", "running")),
        )
    )
    if (active or 0) >= 1:
        raise _Conflict("a build is already in progress", {"active": active})
    # A successful build mints an owned Image row, so the same per-member cap applies here.
    if not rbac_allows(principal, "image.create"):
        await _assert_image_quota(db, principal.user_id)

    cluster = await _pick_build_cluster(db)
    image_ref = f"{settings.BUILD_REGISTRY}/{principal.user_id.lower()}/{name}:{tag}"
    build = ImageBuild(
        id=ids.new("build"),
        group_id=body.group_id,
        owner_user_id=principal.user_id,
        name=name,
        source=body.source,
        dockerfile=body.dockerfile,
        git_url=body.git_url,
        git_ref=body.git_ref,
        context=body.context,
        build_args=body.build_args or None,
        target_tag=tag,
        cluster_id=cluster.id,
        status="queued",
        image_ref=image_ref,
    )
    db.add(build)
    await db.flush()

    try:
        await GShareImageBuildCRD(db).apply_build(cluster.id, build)
    except Exception as exc:  # noqa: BLE001 — record the failed handoff instead of a ghost queued row
        build.status = "failed"
        build.error = f"handoff failed: {exc}"[:2000]
        await AuditService(db).record(
            actor=principal.user_id, action="image.build.create", target=build.id,
            result="handoff_failed", group_id=body.group_id,
        )
        await db.commit()
        raise _HandoffFailed("build handoff failed") from exc

    await AuditService(db).record(
        actor=principal.user_id, action="image.build.create", target=build.id, result="accepted",
        group_id=body.group_id, source=body.source, name=name, image_ref=image_ref,
    )
    await db.commit()
    return _serialize_build(build)


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
    return {**_serialize_build(build), "dockerfile": build.dockerfile,
            "git_url": build.git_url, "git_ref": build.git_ref}


@router.get("/image-builds/{build_id}/logs")
async def build_logs(
    build_id: str,
    tail: int = Query(default=500, ge=1, le=10000),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Return the stored kaniko log tail (the operator reports the last ~16KB on each callback)."""
    build = await _load_build_for_read(build_id, principal, db)
    lines = (build.log_tail or "").splitlines()[-tail:]
    return {"build_id": build.id, "status": build.status, "lines": lines}
