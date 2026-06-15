"""Resource presets router."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import func, nulls_last, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination, get_current_principal
from app.auth.rbac import Principal
from app.core import ids
from app.core.errors import DomainError, NotFound
from app.db.base import get_db
from app.db.models import ResourcePreset
from app.domain.audit_service import AuditService

router = APIRouter(prefix="/resource-presets", tags=["presets"])


class _Unprocessable(DomainError):
    code, http = "validation_failed", 422


class _Conflict(DomainError):
    code, http = "conflict", 409


class PresetCreate(BaseModel):
    name: str
    kind: str = "gpu"                   # compute | gpu
    # compute preset
    cpu: int | None = None
    mem_gb: int | None = None
    disk_gb: int | None = None
    # gpu preset: a per-model fraction
    gpu_frac: float | None = None       # 0 < frac <= 1; VRAM is the chosen model's full card multiplied by frac
    gpu_cores: int | None = None
    mode: str | None = None             # fractional | exclusive


def _preset_view(p: ResourcePreset) -> dict:
    # Model stores memory in `mem` (GiB); API surfaces it as `mem_gb`.
    return {
        "id": p.id,
        "name": p.name,
        "kind": p.kind,
        "cpu": p.cpu,
        "mem_gb": p.mem,
        "disk_gb": p.disk_gb,
        "gpu_frac": float(p.gpu_frac) if p.gpu_frac is not None else None,
        "gpu_cores": p.gpu_cores,
        "mode": p.mode,
        "gpu_mem_mb": p.gpu_mem_mb,
    }


@router.get("")
async def list_presets(
    page: Pagination = Depends(),
    q: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    base = select(ResourcePreset)
    if kind is not None:
        base = base.where(ResourcePreset.kind == kind)
    if q is not None:
        base = base.where(ResourcePreset.name.ilike(f"%{q}%"))
    total = await db.scalar(select(func.count()).select_from(base.subquery()))
    rows = (
        await db.scalars(
            base.order_by(nulls_last(ResourcePreset.gpu_frac.desc()), ResourcePreset.name.asc())
            .limit(page.size)
            .offset(page.offset)
        )
    ).all()
    return {
        "data": [_preset_view(p) for p in rows],
        "pagination": {
            "page": page.page, "size": page.size, "total": total or 0,
            "total_pages": ((total or 0) + page.size - 1) // page.size,
        },
    }


@router.get("/{preset_id}")
async def get_preset(
    preset_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Fetch one preset, for deep links from the edit page."""
    preset = await db.get(ResourcePreset, preset_id)
    if preset is None:
        raise NotFound("preset not found", {"preset_id": preset_id})
    return _preset_view(preset)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_preset(
    body: PresetCreate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    principal.require(action="preset.create")

    if body.kind not in ("compute", "gpu"):
        raise _Unprocessable("kind must be compute|gpu")

    # name is UNIQUE -> 409 on duplicate.
    dup = await db.scalar(select(ResourcePreset).where(ResourcePreset.name == body.name))
    if dup is not None:
        raise _Conflict("a preset with this name already exists")

    if body.kind == "compute":
        if not body.cpu or body.cpu < 1:
            raise _Unprocessable("compute preset: cpu must be >= 1")
        if not body.mem_gb or body.mem_gb < 1:
            raise _Unprocessable("compute preset: mem_gb must be >= 1")
        preset = ResourcePreset(
            id=ids.new("preset"), name=body.name, kind="compute",
            cpu=body.cpu, mem=body.mem_gb, disk_gb=body.disk_gb,
        )
    else:  # gpu
        if body.gpu_frac is None or not (0 < body.gpu_frac <= 1):
            raise _Unprocessable("gpu preset: gpu_frac must be in (0, 1]")
        mode = body.mode or ("exclusive" if body.gpu_frac >= 1 else "fractional")
        if mode not in ("fractional", "exclusive"):
            raise _Unprocessable("mode must be fractional|exclusive")
        cores = 100 if mode == "exclusive" else (
            body.gpu_cores if body.gpu_cores is not None else min(100, max(5, round(body.gpu_frac * 100)))
        )
        preset = ResourcePreset(
            id=ids.new("preset"), name=body.name, kind="gpu",
            gpu_frac=body.gpu_frac, gpu_cores=cores, mode=mode,
        )
    db.add(preset)
    await db.flush()
    await AuditService(db).record(
        actor=principal.user_id, action="preset.create", target=preset.id, name=body.name, kind=body.kind,
    )
    await db.commit()
    return _preset_view(preset)


class PresetUpdate(BaseModel):
    name: str | None = None
    cpu: int | None = None
    mem_gb: int | None = None
    disk_gb: int | None = None
    gpu_frac: float | None = None
    gpu_cores: int | None = None
    mode: str | None = None


@router.patch("/{preset_id}")
async def update_preset(
    preset_id: str,
    body: PresetUpdate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Update a preset; super_admin only. kind is immutable, and only the fields belonging to that
    kind are applied."""
    principal.require(action="preset.create")
    p = await db.get(ResourcePreset, preset_id)
    if p is None:
        raise NotFound("preset not found")
    if body.name is not None and body.name != p.name:
        dup = await db.scalar(
            select(ResourcePreset.id).where(ResourcePreset.name == body.name, ResourcePreset.id != preset_id)
        )
        if dup is not None:
            raise _Conflict("a preset with this name already exists")
        p.name = body.name
    if p.kind == "compute":
        if body.cpu is not None:
            p.cpu = body.cpu
        if body.mem_gb is not None:
            p.mem = body.mem_gb
        if body.disk_gb is not None:
            p.disk_gb = body.disk_gb
    else:  # gpu
        if body.gpu_frac is not None:
            if not (0 < body.gpu_frac <= 1):
                raise _Unprocessable("gpu_frac must be in (0, 1]")
            p.gpu_frac = body.gpu_frac
        if body.mode is not None:
            if body.mode not in ("fractional", "exclusive"):
                raise _Unprocessable("mode must be fractional|exclusive")
            p.mode = body.mode
        if p.mode == "exclusive":
            p.gpu_cores = 100
        elif body.gpu_cores is not None:
            p.gpu_cores = body.gpu_cores
    await db.flush()
    await AuditService(db).record(
        actor=principal.user_id, action="preset.update", target=p.id, name=p.name,
    )
    await db.commit()
    return _preset_view(p)


@router.delete("/{preset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_preset(
    preset_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Delete a preset. Same authority as preset.create: super_admin."""
    principal.require(action="preset.create")
    preset = await db.get(ResourcePreset, preset_id)
    if preset is None:
        raise NotFound("preset not found")
    await db.delete(preset)
    await AuditService(db).record(
        actor=principal.user_id, action="preset.delete", target=preset_id, name=preset.name,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
