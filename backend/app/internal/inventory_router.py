"""Internal inventory callbacks: operator reflects measured GPU inventory.

POST /internal/inventory/gpu-devices — upsert one GpuDevice (+ its GpuNode) into the ledger.
POST /internal/inventory/drift — Σused>total drift signal (logged; ledger stays the truth).
POST /internal/nodes/health-events — node health transition (cordon/alert) — accepted/logged.
RS256 internal JWT required (aud=gshare-internal). Python is the only DB writer.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.internal import (
    OperatorGpuDeviceUpsert,
    OperatorNodeHealthEvent,
    OperatorNodeUpsert,
)
from app.auth.internal_jwt import require_internal_jwt
from app.cluster.inventory_sync import InventorySync
from app.core import ids
from app.core.logging import get_logger
from app.db.base import get_db
from app.db.models import GpuNode, NodeHealthEvent

log = get_logger(__name__)
router = APIRouter(tags=["internal"])


@router.post("/internal/inventory/gpu-devices", status_code=status.HTTP_202_ACCEPTED)
async def upsert_gpu_device(
    ev: OperatorGpuDeviceUpsert,
    claims: dict = Depends(require_internal_jwt),     # aud=gshare-internal
    db: AsyncSession = Depends(get_db),
):
    # cluster_id from payload, else derive from the operator token subject
    # ("operator:<cluster_id>").
    cluster_id = ev.cluster_id
    if not cluster_id:
        sub = str(claims.get("sub", ""))
        cluster_id = sub.split(":", 1)[1] if sub.startswith("operator:") else sub
    await InventorySync(db).upsert_device(ev, cluster_id)
    return {"accepted": True}


@router.post("/internal/inventory/nodes", status_code=status.HTTP_202_ACCEPTED)
async def upsert_node(
    ev: OperatorNodeUpsert,
    claims: dict = Depends(require_internal_jwt),     # aud=gshare-internal
    db: AsyncSession = Depends(get_db),
):
    # cluster_id from payload, else derive from the operator token subject
    # ("operator:<cluster_id>").
    cluster_id = ev.cluster_id
    if not cluster_id:
        sub = str(claims.get("sub", ""))
        cluster_id = sub.split(":", 1)[1] if sub.startswith("operator:") else sub
    await InventorySync(db).upsert_node(ev, cluster_id)
    return {"accepted": True}


@router.post("/internal/inventory/drift", status_code=status.HTTP_202_ACCEPTED)
async def report_drift(
    _claims: dict = Depends(require_internal_jwt),
    body: dict = Body(default_factory=dict),
):
    # Ledger is the source of truth; we log the drift signal for reconciliation/alerting.
    log.warning("inventory drift reported: %s", body)
    return {"accepted": True}


@router.post("/internal/nodes/health-events", status_code=status.HTTP_202_ACCEPTED)
async def node_health_event(
    ev: OperatorNodeHealthEvent,
    _claims: dict = Depends(require_internal_jwt),
    db: AsyncSession = Depends(get_db),
):
    """Record a NodeHealthEvent and, on a cordon action, mark GpuNode.status=cordoned.

    The operator already cordoned the K8s node; the ledger reflects it so the console/scheduler
    stop placing new sessions there.
    """
    async with db.begin():
        db.add(
            NodeHealthEvent(
                id=ev.id or ids.new("healthevent"),
                node_id=ev.node_id,
                kind=ev.kind,
                severity=ev.severity,
                action=ev.action,
            )
        )
        hostname = ev.node_id
        if (ev.action or "").lower() == "cordon":
            node = (
                await db.execute(select(GpuNode).where(GpuNode.id == ev.node_id))
            ).scalar_one_or_none()
            if node is not None:
                node.status = "cordoned"
                hostname = node.hostname
        # Warning, critical, and cordon events notify the system administrators.
        if (ev.severity or "").lower() in ("warn", "warning", "critical") or (ev.action or "").lower() == "cordon":
            from app.domain.notification_service import NotificationService

            notifier = NotificationService(db)
            await notifier.notify(
                await notifier.system_admins(), "node_health",
                f"Node health: {ev.kind}",
                f"Node '{hostname}': {ev.kind} (severity={ev.severity}, action={ev.action}).",
                node_id=ev.node_id, kind=ev.kind, action=ev.action,
            )
    log.warning("node health event: node=%s kind=%s action=%s", ev.node_id, ev.kind, ev.action)
    return {"accepted": True}
