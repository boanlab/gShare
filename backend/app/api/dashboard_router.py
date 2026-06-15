"""User dashboard summary. Aggregates KPIs the console dashboard renders:
credit, active sessions, GPU VRAM, region (GPU model) availability, resource allocation.
Read-only; principal-scoped (owner's sessions/wallet). Real ledger/inventory values."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_principal
from app.api.schemas.dashboard import DashboardSummary
from app.auth.rbac import Principal
from app.db.base import get_db
from app.db.models import CreditWallet, GpuDevice, Session

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/summary", response_model=DashboardSummary)
async def dashboard_summary(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    # Active sessions, by owner.
    running = (
        await db.scalar(
            select(func.count())
            .select_from(Session)
            .where(Session.owner_user_id == principal.user_id, Session.status == "running")
        )
    ) or 0
    active = (
        await db.scalar(
            select(func.count())
            .select_from(Session)
            .where(
                Session.owner_user_id == principal.user_id,
                Session.status.in_(("pending", "preparing", "running", "paused")),
            )
        )
    ) or 0

    # The owner's credit wallet: balance and reserved.
    wallet = (
        await db.execute(
            select(CreditWallet).where(
                CreditWallet.owner_type == "user", CreditWallet.owner_id == principal.user_id
            )
        )
    ).scalar_one_or_none()
    credit = {
        "available": float(wallet.balance - wallet.reserved) if wallet else None,
        "balance": float(wallet.balance) if wallet else None,
        "reserved": float(wallet.reserved) if wallet else None,
    }

    # GPU inventory over the cluster's ready devices: total VRAM and availability per model.
    devices = (
        await db.execute(select(GpuDevice).where(GpuDevice.status == "ready"))
    ).scalars().all()
    total_mem = sum(d.total_mem_mb or 0 for d in devices)
    used_mem = sum(d.used_mem_mb or 0 for d in devices)
    by_model: dict[str, dict] = {}
    for d in devices:
        m = by_model.setdefault(d.model or "unknown", {"model": d.model or "unknown", "total": 0, "free": 0})
        m["total"] += 1
        if (d.used_mem_mb or 0) == 0 and (d.used_cores or 0) == 0:
            m["free"] += 1
    regions = sorted(by_model.values(), key=lambda x: x["model"])

    return {
        "credit": credit,
        "sessions": {"running": running, "active": active},
        "vram": {"used_mb": used_mem, "total_mb": total_mem},
        "regions": regions,
        "allocation": {
            "instances": {"used": active, "total": max(active, len(devices) or 1)},
            "vram": {"used_mb": used_mem, "total_mb": total_mem},
        },
    }
