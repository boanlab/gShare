"""Infra/monitor read endpoints for the admin console.
GET /nodes, /gpu-devices, /metrics/cluster — aggregate the ledger inventory + sessions + credit.
Read-only; admin pages. Real values from GpuNode/GpuDevice/Allocation/Session/QueueEntry."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_principal
from app.api.schemas.metrics import (
    BillingReport,
    BillingReportRow,
    ClusterMetrics,
    GpuDeviceList,
    NodeList,
)
from app.auth.rbac import Principal, rbac_allows
from app.core import ids
from app.core.errors import DomainError, Forbidden, NotFound
from app.db.base import get_db
from app.db.models import (
    Allocation,
    Cluster,
    CreditTransaction,
    CreditWallet,
    GpuDevice,
    GpuNode,
    Offering,
    Organization,
    Project,
    QueueEntry,
    Session,
    User,
)
from app.domain.audit_service import AuditService

router = APIRouter(tags=["infra"])


class _Validation(DomainError):
    code, http = "validation_failed", 422


class _BadQuery(DomainError):
    code, http = "invalid_query_parameter", 400


def _node_out(n: GpuNode, devices: list[GpuDevice] | None = None) -> dict:
    """Serialise a node in the same shape as a list item; shared by the cordon, drain, and register
    responses."""
    nd = devices or []
    modes = {d.mode for d in nd if d.mode}
    return {
        "id": n.id,
        "hostname": n.hostname,
        "status": n.status,
        "cpu": n.cpu or 0,
        "mem_gb": n.mem or 0,
        "region": n.region or "-",
        "gpu_mode": ("fractional" if "fractional" in modes else ("exclusive" if "exclusive" in modes else "-")),
        "device_count": len(nd),
        "heartbeat_at": (n.updated_at.isoformat() if n.updated_at else None),
    }


@router.get("/nodes", response_model=NodeList)
async def list_nodes(
    status: str | None = Query(default=None),
    region: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    principal.require(action="node.read")
    stmt = select(GpuNode)
    if status:
        stmt = stmt.where(GpuNode.status == status)
    nodes = (await db.execute(stmt)).scalars().all()
    # Aggregate device count and mode per node.
    devs = (await db.execute(select(GpuDevice))).scalars().all()
    by_node: dict[str, list] = {}
    for d in devs:
        by_node.setdefault(d.node_id, []).append(d)
    # Resolve cluster ids to names for the node table's cluster column.
    cluster_names = dict(
        (await db.execute(select(Cluster.id, Cluster.name))).all()
    )
    out = []
    for n in nodes:
        nd = by_node.get(n.id, [])
        modes = {d.mode for d in nd if d.mode}
        out.append({
            "id": n.id,
            "hostname": n.hostname,
            "cluster_id": n.cluster_id,
            "cluster_name": cluster_names.get(n.cluster_id) if n.cluster_id else None,
            "status": n.status,
            "cpu": n.cpu or 0,
            "mem_gb": n.mem or 0,
            "region": n.region or "-",
            "gpu_mode": ("fractional" if "fractional" in modes else ("exclusive" if "exclusive" in modes else "-")),
            "device_count": len(nd),
            "heartbeat_at": (n.updated_at.isoformat() if n.updated_at else None),
        })
    return {"data": out, "total": len(out)}


@router.get("/nodes/{node_id}")
async def get_node(
    node_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Fetch one node, for deep links from the drain and device pages."""
    principal.require(action="node.read")
    n = await db.get(GpuNode, node_id)
    if n is None:
        raise NotFound("node", {"node_id": node_id})
    nd = (await db.execute(select(GpuDevice).where(GpuDevice.node_id == node_id))).scalars().all()
    modes = {d.mode for d in nd if d.mode}
    cname = None
    if n.cluster_id:
        cname = await db.scalar(select(Cluster.name).where(Cluster.id == n.cluster_id))
    return {
        "id": n.id,
        "hostname": n.hostname,
        "cluster_id": n.cluster_id,
        "cluster_name": cname,
        "status": n.status,
        "cpu": n.cpu or 0,
        "mem_gb": n.mem or 0,
        "region": n.region or "-",
        "gpu_mode": ("fractional" if "fractional" in modes else ("exclusive" if "exclusive" in modes else "-")),
        "device_count": len(nd),
        "heartbeat_at": (n.updated_at.isoformat() if n.updated_at else None),
    }


class _CordonBody(BaseModel):
    cordon: bool = True
    reason: str | None = None


class _DrainBody(BaseModel):
    mode: str = Field(default="reschedule", pattern="^(reschedule|force_terminate)$")
    reason: str | None = None


class _NodeRegister(BaseModel):
    hostname: str = Field(min_length=1, max_length=120)
    region: str | None = None
    gpu_mode: str | None = None
    cpu: int | None = Field(default=None, ge=0)
    mem_gb: int | None = Field(default=None, ge=0)
    cluster_id: str | None = None


async def _load_node(db: AsyncSession, node_id: str) -> GpuNode:
    node = await db.get(GpuNode, node_id)
    if node is None:
        raise NotFound("node", {"node_id": node_id})
    return node


async def _node_devices(db: AsyncSession, node_id: str) -> list[GpuDevice]:
    return list((await db.scalars(select(GpuDevice).where(GpuDevice.node_id == node_id))).all())


@router.post("/nodes", status_code=status.HTTP_201_CREATED)
async def register_node(
    body: _NodeRegister,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Register a node, initially offline. super_admin only.

    Without a cluster_id the node is attached to the only registered cluster, which covers test and
    single-cluster setups. With several clusters the field is required.
    """
    principal.require(action="node.create")
    cluster_id = body.cluster_id
    if cluster_id is None:
        clusters = (
            await db.scalars(select(Cluster).where(Cluster.deleted_at.is_(None)))
        ).all()
        if len(clusters) == 1:
            cluster_id = clusters[0].id
        else:
            raise _Validation(
                "cluster_id required (multiple or zero clusters registered)",
                {"clusters": len(clusters)},
            )
    elif await db.get(Cluster, cluster_id) is None:
        raise NotFound("cluster", {"cluster_id": cluster_id})

    node = GpuNode(
        id=ids.new("node"),
        cluster_id=cluster_id,
        hostname=body.hostname,
        status="offline",
        cpu=body.cpu,
        mem=body.mem_gb,
        region=body.region,
    )
    db.add(node)
    await db.flush()
    await AuditService(db).record(
        actor=principal.user_id, action="node.register", target=node.id, result="ok",
        hostname=body.hostname, cluster_id=cluster_id, gpu_mode=body.gpu_mode,
    )
    await db.commit()
    await db.refresh(node)  # pick up server defaults such as updated_at before serialising, avoiding MissingGreenlet
    return _node_out(node, [])


@router.post("/nodes/{node_id}/cordon")
async def cordon_node(
    node_id: str,
    body: _CordonBody,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Cordon or uncordon a node. super_admin only.

    cordon=true blocks new scheduling (status=cordoned); false restores it (status=ready). Sessions
    already running are left alone.
    """
    principal.require(action="node.cordon")
    node = await _load_node(db, node_id)
    devs = await _node_devices(db, node_id)
    node.status = "cordoned" if body.cordon else "ready"
    await AuditService(db).record(
        actor=principal.user_id, action="node.cordon", target=node_id, result=node.status,
        cordon=body.cordon, reason=body.reason,
    )
    await db.commit()
    # updated_at (onupdate=now) is expired by the flush, so refresh before serialising; a
    # synchronous lazy load here would raise MissingGreenlet.
    await db.refresh(node)
    return _node_out(node, devs)


@router.post("/nodes/{node_id}/drain")
async def drain_node(
    node_id: str,
    body: _DrainBody,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Drain a node. super_admin only.

    Cordons the node and reports the sessions holding live allocations on its devices as affected.
    The actual rescheduling or forced termination is the operator's job; this endpoint only records
    the desired state and computes the blast radius.
    """
    principal.require(action="node.drain")
    node = await _load_node(db, node_id)
    devs = await _node_devices(db, node_id)
    dev_ids = [d.id for d in devs]
    affected: list[str] = []
    if dev_ids:
        affected = list(
            (
                await db.scalars(
                    select(Allocation.session_id)
                    .where(Allocation.device_id.in_(dev_ids), Allocation.ended_at.is_(None))
                )
            ).all()
        )
    node.status = "cordoned"
    drain_id = f"drain_{node_id}"
    await db.flush()
    await AuditService(db).record(
        actor=principal.user_id, action="node.drain", target=node_id, result="ok",
        mode=body.mode, reason=body.reason, affected=len(affected),
    )
    await db.commit()
    return {
        "node_id": node_id,
        "status": node.status,
        "drain_id": drain_id,
        "mode": body.mode,
        "affected_sessions": affected,
    }


@router.get("/gpu-devices", response_model=GpuDeviceList)
async def list_gpu_devices(
    node_id: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    principal.require(action="node.read")
    stmt = select(GpuDevice)
    if node_id:
        stmt = stmt.where(GpuDevice.node_id == node_id)
    devs = (await db.execute(stmt)).scalars().all()
    # Live allocations per device (reserved or bound) become bound_sessions.
    allocs = (
        await db.execute(select(Allocation).where(Allocation.ended_at.is_(None)))
    ).scalars().all()
    by_dev: dict[str, list] = {}
    for a in allocs:
        by_dev.setdefault(a.device_id, []).append(a)
    out = []
    for d in devs:
        bound = [
            {"session_id": a.session_id, "gpu_mem_mb": a.gpu_mem_mb or 0, "gpu_cores": a.gpu_cores or 0}
            for a in by_dev.get(d.id, [])
        ]
        out.append({
            "id": d.id,
            "node_id": d.node_id,
            "model": d.model,
            "mode": d.mode or "-",
            "status": d.status,
            "gpu_uuid": d.gpu_uuid,
            "total_mem_mb": d.total_mem_mb,
            "used_mem_mb": d.used_mem_mb,
            "free_mem_mb": (d.total_mem_mb or 0) - (d.used_mem_mb or 0),
            "total_cores": d.total_cores,
            "used_cores": d.used_cores,
            "free_cores": (d.total_cores or 0) - (d.used_cores or 0),
            "bound_sessions": bound,
        })
    return {"data": out, "total": len(out)}


@router.get("/metrics/cluster", response_model=ClusterMetrics)
async def metrics_cluster(
    region: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    principal.require(action="node.read")
    nodes = (await db.execute(select(GpuNode))).scalars().all()
    n_total = len(nodes)
    n_ready = sum(1 for n in nodes if n.status == "ready")
    n_cordoned = sum(1 for n in nodes if n.status == "cordoned")
    n_offline = sum(1 for n in nodes if n.status in ("offline", "down"))
    n_busy = max(0, n_total - n_ready - n_cordoned - n_offline)

    devs = (await db.execute(select(GpuDevice))).scalars().all()
    vram_total = sum(d.total_mem_mb or 0 for d in devs)
    vram_used = sum(d.used_mem_mb or 0 for d in devs)
    cores_total = sum(d.total_cores or 0 for d in devs)
    cores_used = sum(d.used_cores or 0 for d in devs)
    empty = sum(1 for d in devs if (d.used_mem_mb or 0) == 0 and (d.used_cores or 0) == 0)

    running = (await db.scalar(select(func.count()).select_from(Session).where(Session.status == "running"))) or 0
    queued = (await db.scalar(select(func.count()).select_from(QueueEntry))) or 0

    # Real GPU utilisation is aggregated from Prometheus (the DCGM exporter). On failure, fall back
    # to the allocated core share.
    avg_util = round(cores_used / cores_total * 100, 1) if cores_total else 0.0
    try:
        import httpx

        from app.core.config import settings as _st
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(
                f"{_st.PROMETHEUS_URL}/api/v1/query",
                params={"query": "avg(DCGM_FI_DEV_GPU_UTIL)"},
            )
            res = r.json().get("data", {}).get("result", [])
            if res:
                avg_util = round(float(res[0]["value"][1]), 1)
    except Exception:  # noqa: BLE001 — keep the fallback when Prometheus is unavailable
        pass

    since = datetime.now(UTC) - timedelta(hours=24)
    consumed = (
        await db.scalar(
            select(func.coalesce(func.sum(CreditTransaction.amount), 0)).where(
                CreditTransaction.type == "consume", CreditTransaction.created_at >= since
            )
        )
    ) or 0
    holds = (await db.scalar(select(func.coalesce(func.sum(CreditWallet.reserved), 0)))) or 0

    return {
        "as_of": datetime.now(UTC).isoformat(),
        "nodes": {"total": n_total, "ready": n_ready, "busy": n_busy, "cordoned": n_cordoned, "offline": n_offline},
        "gpu": {
            "device_total": len(devs),
            "vram_total_mb": vram_total,
            "vram_used_mb": vram_used,
            "vram_load_pct": round(vram_used / vram_total * 100, 1) if vram_total else 0.0,
            "avg_utilization_pct": avg_util,
            "empty_gpu_count": empty,
        },
        "sessions": {"running": running, "queued": queued},
        "credit": {"consumed_last_24h": str(abs(float(consumed))), "active_holds": str(float(holds))},
    }


_TWO_DP = Decimal("0.01")


def _money(v: Decimal) -> str:
    """Serialise as a money-style string, rounded to two decimal places."""
    return str(Decimal(v).quantize(_TWO_DP, rounding=ROUND_HALF_UP))


@router.get("/metrics/billing-report", response_model=BillingReport)
async def billing_report(
    scope: str = Query(..., pattern="^(org|group|wallet)$"),
    scope_id: str | None = Query(default=None),
    from_: datetime = Query(..., alias="from"),
    to: datetime = Query(...),
    group_by: str = Query(default="group", pattern="^(group|offering|wallet)$"),
    format: str = Query(default="json"),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Settlement and billing report: the credit ledger (consume, settle, topup) aggregated by a
    group_by key.

    Consumption and top-ups join CreditTransaction to CreditWallet and are attributed to the owner
    (organization, group, or user). gpu_hours is a best-effort sum of the occupancy hours of GPU
    sessions overlapping [from_, to].
    """
    principal.require(action="budget.read")
    if format != "json":
        raise _BadQuery("unsupported format (json only)", {"format": format})
    if to <= from_:
        raise _BadQuery("'to' must be after 'from'", {"from": str(from_), "to": str(to)})

    # Tenant isolation: verify the requested scope and scope_id fall inside the caller's authority.
    # super_admin is unrestricted. Everyone else sees only their own organization (org_admin) or
    # administered group (group_admin and above), and a wallet scope additionally requires authority
    # over that wallet's owner. Omitting scope_id, which aggregates across all tenants, is
    # super_admin only.
    if "super_admin" not in principal.global_roles:
        if not scope_id:
            raise Forbidden("not permitted: billing-report without scope_id is super_admin only")
        if scope == "org":
            if scope_id not in principal.org_admin_orgs:
                raise Forbidden("not permitted: billing-report for this organization")
        elif scope == "group":
            if not rbac_allows(principal, "budget.read", scope_id):
                raise Forbidden("not permitted: billing-report for this group")
        elif scope == "wallet":
            wallet = await db.get(CreditWallet, scope_id)
            if wallet is None:
                raise NotFound("wallet", {"wallet_id": scope_id})
            if wallet.owner_type == "org":
                allowed = wallet.owner_id in principal.org_admin_orgs
            elif wallet.owner_type == "group":
                allowed = rbac_allows(principal, "budget.read", wallet.owner_id)
            else:  # personal wallets are super_admin only
                allowed = False
            if not allowed:
                raise Forbidden("not permitted: billing-report for this wallet")

    # Join transactions to wallets for owner attribution, filtered on created_at in [from_, to].
    txn_q = (
        select(CreditTransaction.type, CreditTransaction.amount, CreditWallet)
        .join(CreditWallet, CreditTransaction.wallet_id == CreditWallet.id)
        .where(
            CreditTransaction.created_at >= from_,
            CreditTransaction.created_at <= to,
        )
    )
    # When scope and scope_id are given, restrict to that owner, or to that wallet.
    if scope_id:
        if scope == "wallet":
            txn_q = txn_q.where(CreditWallet.id == scope_id)
        elif scope == "org":
            txn_q = txn_q.where(CreditWallet.owner_type == "org", CreditWallet.owner_id == scope_id)
        elif scope == "group":
            txn_q = txn_q.where(CreditWallet.owner_type == "group", CreditWallet.owner_id == scope_id)
    rows = (await db.execute(txn_q)).all()

    # The offering is not recorded on a transaction, so group_by=offering is only meaningful over
    # session aggregates. Credit consumption and top-ups can only be attributed per wallet or owner,
    # which is why the offering key carries gpu_hours alone.
    def _wallet_key(w: CreditWallet) -> str:
        if group_by == "wallet":
            return w.id
        # group_by == "group", the default: attribute by owner id (organization, group, or user).
        return w.owner_id

    consumed_by: dict[str, Decimal] = {}
    topup_by: dict[str, Decimal] = {}
    owner_type_by: dict[str, str] = {}
    wallet_owner: dict[str, tuple[str, str]] = {}  # wallet_id -> (owner_type, owner_id)
    for ttype, amount, wallet in rows:
        key = _wallet_key(wallet)
        owner_type_by.setdefault(key, wallet.owner_type)
        wallet_owner[wallet.id] = (wallet.owner_type, wallet.owner_id)
        amt = Decimal(amount or 0)
        if ttype in ("consume", "settle"):
            consumed_by[key] = consumed_by.get(key, Decimal(0)) + abs(amt)
        elif ttype == "topup":
            topup_by[key] = topup_by.get(key, Decimal(0)) + abs(amt)

    # gpu_hours: the occupancy hours of GPU sessions overlapping [from_, to], attributed by the
    # group_by key.
    #   group -> session.group_id, wallet -> session.billing_wallet_id,
    #   offering -> session.offering_id.
    now = datetime.now(UTC)
    sess_q = select(Session).where(
        Session.resource_class == "gpu",
        Session.started_at.is_not(None),
        Session.started_at < to,
    )
    if scope_id:
        if scope == "wallet":
            sess_q = sess_q.where(Session.billing_wallet_id == scope_id)
        elif scope == "group":
            sess_q = sess_q.where(Session.group_id == scope_id)
        # Organization scope: a session has no direct organization column, so the group_id-based
        # filter is skipped and sessions are aggregated over the whole period.
    sessions = (await db.scalars(sess_q)).all()

    gpu_hours_by: dict[str, Decimal] = {}
    for s in sessions:
        start = s.started_at
        if start is None:
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        end = s.terminated_at or now
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
        # [start, end] ∩ [from_, to]
        ov_start = max(start, from_)
        ov_end = min(end, to)
        if ov_end <= ov_start:
            continue
        hours = Decimal((ov_end - ov_start).total_seconds()) / Decimal(3600)
        if group_by == "wallet":
            skey = s.billing_wallet_id
        elif group_by == "offering":
            skey = s.offering_id
        else:  # group
            skey = s.group_id
        if not skey:
            continue
        gpu_hours_by[skey] = gpu_hours_by.get(skey, Decimal(0)) + hours

    # Resolve display names. Under group_by=group the owner ids can mix organizations, groups, and
    # users, so they are looked up per owner_type.
    name_by: dict[str, str] = {}
    if group_by == "offering":
        off_ids = set(gpu_hours_by.keys())
        if off_ids:
            for oid, oname in (
                await db.execute(select(Offering.id, Offering.name).where(Offering.id.in_(off_ids)))
            ).all():
                name_by[oid] = oname
    elif group_by == "wallet":
        # A wallet key is labelled "owner_type · owner_id"; the name comes from the owner lookup.
        oids = {ow[1] for ow in wallet_owner.values()}
        # The wallet key here is wallet.id, so enrich it with the owner label.
        u = {i: n for i, n in (await db.execute(select(User.id, User.name).where(User.id.in_(oids)))).all()} if oids else {}
        g = {i: n for i, n in (await db.execute(select(Project.id, Project.name).where(Project.id.in_(oids)))).all()} if oids else {}
        o = {i: n for i, n in (await db.execute(select(Organization.id, Organization.name).where(Organization.id.in_(oids)))).all()} if oids else {}
        for wid, (otype, oid) in wallet_owner.items():
            nm = ({"user": u, "group": g, "org": o}.get(otype) or {}).get(oid)
            if nm:
                name_by[wid] = nm
    else:  # group: resolve owner_id directly, per owner_type
        uids = {k for k, t in owner_type_by.items() if t == "user"}
        gids = {k for k, t in owner_type_by.items() if t == "group"}
        oids = {k for k, t in owner_type_by.items() if t == "org"}
        if uids:
            for i, n in (await db.execute(select(User.id, User.name).where(User.id.in_(uids)))).all():
                name_by[i] = n
        if gids:
            for i, n in (await db.execute(select(Project.id, Project.name).where(Project.id.in_(gids)))).all():
                name_by[i] = n
        if oids:
            for i, n in (await db.execute(select(Organization.id, Organization.name).where(Organization.id.in_(oids)))).all():
                name_by[i] = n

    all_keys = set(consumed_by) | set(topup_by) | set(gpu_hours_by)
    out_rows: list[BillingReportRow] = []
    total_consumed = Decimal(0)
    total_topup = Decimal(0)
    for key in sorted(all_keys):
        c = consumed_by.get(key)
        t = topup_by.get(key)
        gh = gpu_hours_by.get(key)
        total_consumed += c or Decimal(0)
        total_topup += t or Decimal(0)
        out_rows.append(
            BillingReportRow(
                group=key,
                group_name=name_by.get(key),
                consumed=_money(c) if c is not None else None,
                topup=_money(t) if t is not None else None,
                gpu_hours=str(gh.quantize(_TWO_DP, rounding=ROUND_HALF_UP)) if gh is not None else None,
            )
        )

    totals = {"consumed": _money(total_consumed), "topup": _money(total_topup)}
    return BillingReport(rows=out_rows, totals=totals, currency="C")
