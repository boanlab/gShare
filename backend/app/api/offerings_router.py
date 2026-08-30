"""Offerings router. Catalog + price history."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination, get_current_principal
from app.auth.rbac import Principal
from app.core import ids
from app.core.cuda import parse_cuda
from app.core.errors import DomainError, NotFound
from app.db.base import get_db
from app.db.models import Offering, OfferingPriceHistory
from app.db.models import Session as SessionModel
from app.domain.audit_service import AuditService

router = APIRouter(prefix="/offerings", tags=["offerings"])


class _Unprocessable(DomainError):
    code, http = "validation_failed", 422


class _Conflict(DomainError):
    code, http = "conflict", 409


class OfferingCreate(BaseModel):
    name: str
    resource_class: str = "gpu"
    gpu_model: str | None = None
    gpu_mem_mb: int | None = None
    gpu_cores: int | None = None
    cpu: int | None = None
    mem_gb: int | None = None
    disk_gb: int | None = None
    credit_per_hour: str
    status: str = "active"
    min_cuda: str | None = None         # minimum CUDA, e.g. '12.8'; GPU offerings only


class OfferingUpdate(BaseModel):
    name: str | None = None
    gpu_model: str | None = None
    gpu_mem_mb: int | None = None
    gpu_cores: int | None = None
    cpu: int | None = None
    mem_gb: int | None = None
    disk_gb: int | None = None
    credit_per_hour: str | None = None
    status: str | None = None
    min_cuda: str | None = None         # send '' or null to clear it


def _offering_view(o: Offering) -> dict:
    return {
        "id": o.id,
        "name": o.name,
        "resource_class": o.resource_class,
        "gpu_model": o.gpu_model,
        "gpu_mem_mb": o.gpu_mem_mb,
        "gpu_cores": o.gpu_cores,
        "cpu": o.cpu,
        "mem_gb": o.mem_gb,
        "disk_gb": o.disk_gb,
        "credit_per_hour": str(o.credit_per_hour),
        "status": o.status,
        "min_cuda": o.min_cuda,
    }


def _normalize_cuda(raw: str | None) -> str | None:
    """Normalise a min_cuda input: an empty string clears it, a malformed one is 422."""
    if raw is None:
        return None
    raw = raw.strip()
    if raw == "":
        return None
    if parse_cuda(raw) is None:
        raise _Unprocessable("min_cuda must look like '12.8'", {"min_cuda": raw})
    return raw


def _parse_credit(raw: str) -> Decimal:
    try:
        amount = Decimal(raw)
    except (InvalidOperation, TypeError):
        raise _Unprocessable("credit_per_hour is not a valid number", {"credit_per_hour": raw}) from None
    if amount < Decimal(0):
        raise _Unprocessable("credit_per_hour must be >= 0", {"credit_per_hour": raw})
    return amount


@router.get("")
async def list_offerings(
    page: Pagination = Depends(),
    gpu_model: str | None = Query(default=None),
    q: str | None = Query(default=None),
    sort: str = Query(default="credit_per_hour"),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Catalog list. Authenticated-but-open read; no admin gate.

    Retired (status=inactive) offerings are hidden from non-admins; administrators still see
    them so they can reactivate or audit pricing."""
    base = select(Offering)
    if principal.global_role not in ("super_admin", "org_admin"):
        base = base.where(Offering.status != "inactive")
    if gpu_model is not None:
        base = base.where(Offering.gpu_model == gpu_model)
    if q is not None:
        base = base.where(Offering.name.ilike(f"%{q}%"))

    total = await db.scalar(select(func.count()).select_from(base.subquery()))

    # sort: default credit_per_hour asc; "-field" => desc.
    desc = sort.startswith("-")
    field = sort[1:] if desc else sort
    col = getattr(Offering, field, Offering.credit_per_hour)
    order = col.desc() if desc else col.asc()

    rows = (
        await db.scalars(base.order_by(order).limit(page.size).offset(page.offset))
    ).all()
    return {
        "data": [_offering_view(o) for o in rows],
        "pagination": {
            "page": page.page, "size": page.size, "total": total or 0,
            "total_pages": ((total or 0) + page.size - 1) // page.size,
        },
    }


@router.get("/{offering_id}")
async def get_offering(
    offering_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Fetch one offering, for deep links from the edit page. Readable by any authenticated
    caller."""
    offering = await db.get(Offering, offering_id)
    if offering is None:
        raise NotFound("offering not found", {"offering_id": offering_id})
    return _offering_view(offering)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_offering(
    body: OfferingCreate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    principal.require(action="offering.create")
    credit = _parse_credit(body.credit_per_hour)
    min_cuda = _normalize_cuda(body.min_cuda)

    if body.resource_class == "cpu":
        # CPU offerings are free; GPU dims must be zeroed.
        if credit != Decimal(0):
            raise _Unprocessable("cpu offering must have credit_per_hour = 0")
        if (body.gpu_mem_mb or 0) != 0 or (body.gpu_cores or 0) != 0:
            raise _Unprocessable("cpu offering must not request gpu_mem_mb/gpu_cores")
        gpu_model = None
        gpu_mem_mb = 0
        gpu_cores = 0
        min_cuda = None        # a CPU offering carries no CUDA constraint
    elif body.resource_class == "gpu":
        if not body.gpu_model:
            raise _Unprocessable("gpu offering requires gpu_model")
        if body.gpu_mem_mb is None or body.gpu_mem_mb <= 0:
            raise _Unprocessable("gpu offering requires gpu_mem_mb > 0")
        if body.gpu_cores is None or not (0 <= body.gpu_cores <= 100):
            raise _Unprocessable("gpu offering requires gpu_cores in 0..100")
        gpu_model = body.gpu_model
        gpu_mem_mb = body.gpu_mem_mb
        gpu_cores = body.gpu_cores
    else:
        raise _Unprocessable("resource_class must be gpu or cpu")

    offering = Offering(
        id=ids.new("offering"),
        name=body.name,
        resource_class=body.resource_class,
        gpu_model=gpu_model,
        gpu_mem_mb=gpu_mem_mb,
        gpu_cores=gpu_cores,
        cpu=body.cpu,
        mem_gb=body.mem_gb,
        disk_gb=body.disk_gb,
        credit_per_hour=credit,
        status=body.status if body.status in ("active", "inactive") else "active",
        min_cuda=min_cuda,
    )
    db.add(offering)
    # Seed the initial price-history entry.
    db.add(OfferingPriceHistory(
        id=ids.new("oph"),
        offering_id=offering.id,
        credit_per_hour=credit,
        changed_by=principal.user_id,
    ))
    await db.flush()
    await AuditService(db).record(
        actor=principal.user_id, action="offering.create", target=offering.id,
        credit_per_hour=str(credit), resource_class=body.resource_class,
    )
    await db.commit()
    return _offering_view(offering)


@router.patch("/{offering_id}")
async def update_offering(
    offering_id: str,
    body: OfferingUpdate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Update offering; appends to price history on credit_per_hour change."""
    principal.require(action="offering.update")
    offering = await db.get(Offering, offering_id)
    if offering is None:
        raise NotFound("offering not found")

    changes: dict[str, Any] = {}   # {field: {"from": old, "to": new}} for the audit log

    def _set(attr: str, new: Any) -> None:
        old = getattr(offering, attr, None)
        if new is not None and new != old:
            changes[attr] = {"from": old, "to": new}
            setattr(offering, attr, new)

    _set("name", body.name)
    _set("gpu_model", body.gpu_model)
    _set("gpu_mem_mb", body.gpu_mem_mb)
    _set("gpu_cores", body.gpu_cores)
    _set("cpu", body.cpu)
    _set("mem_gb", body.mem_gb)
    _set("disk_gb", body.disk_gb)
    if body.status is not None:
        if body.status not in ("active", "inactive"):
            raise _Unprocessable("status must be active|inactive")
        _set("status", body.status)
    if body.min_cuda is not None:
        # An empty string clears the field. _set treats None as 'not supplied', so this is handled
        # here.
        new_cuda = _normalize_cuda(body.min_cuda)
        if new_cuda != offering.min_cuda:
            changes["min_cuda"] = {"from": offering.min_cuda, "to": new_cuda}
            offering.min_cuda = new_cuda

    price_changed = False
    if body.credit_per_hour is not None:
        new_credit = _parse_credit(body.credit_per_hour)
        if new_credit != offering.credit_per_hour:
            changes["credit_per_hour"] = {"from": str(offering.credit_per_hour), "to": str(new_credit)}
            offering.credit_per_hour = new_credit
            db.add(OfferingPriceHistory(
                id=ids.new("oph"),
                offering_id=offering.id,
                credit_per_hour=new_credit,
                changed_by=principal.user_id,
            ))
            price_changed = True

    await db.flush()
    await AuditService(db).record(
        actor=principal.user_id, action="offering.update", target=offering.id, result="ok",
        changes=changes, price_changed=price_changed,
    )
    await db.commit()
    return _offering_view(offering)


@router.delete("/{offering_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_offering(
    offering_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Delete an offering; super_admin only. Rejected while a session still references it —
    deactivate it instead."""
    principal.require(action="offering.update")
    offering = await db.get(Offering, offering_id)
    if offering is None:
        raise NotFound("offering not found")
    used = await db.scalar(
        select(func.count()).select_from(SessionModel).where(SessionModel.offering_id == offering_id)
    )
    if used and int(used) > 0:
        raise _Conflict("offering is referenced by sessions; deactivate instead", {"sessions": int(used)})
    # Clear the price history first, since it holds a foreign key.
    from sqlalchemy import delete as _delete

    await db.execute(_delete(OfferingPriceHistory).where(OfferingPriceHistory.offering_id == offering_id))
    await db.delete(offering)
    await AuditService(db).record(
        actor=principal.user_id, action="offering.delete", target=offering_id, result="ok", name=offering.name,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{offering_id}/price-history")
async def price_history(
    offering_id: str,
    page: Pagination = Depends(),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    offering = await db.get(Offering, offering_id)
    if offering is None:
        raise NotFound("offering not found")

    base = select(OfferingPriceHistory).where(
        OfferingPriceHistory.offering_id == offering_id
    )
    if from_ is not None:
        base = base.where(OfferingPriceHistory.created_at >= from_)
    if to is not None:
        base = base.where(OfferingPriceHistory.created_at < to)

    total = await db.scalar(select(func.count()).select_from(base.subquery()))
    rows = (
        await db.scalars(
            base.order_by(OfferingPriceHistory.created_at.desc())
            .limit(page.size).offset(page.offset)
        )
    ).all()
    return {
        "offering_id": offering_id,
        "data": [
            {
                "id": r.id,
                "credit_per_hour": str(r.credit_per_hour),
                "changed_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "pagination": {
            "page": page.page, "size": page.size, "total": total or 0,
            "total_pages": ((total or 0) + page.size - 1) // page.size,
        },
    }
