"""Infra/monitor endpoints for the admin console.
GET /nodes, /gpu-devices, /metrics/cluster — aggregate the ledger inventory + sessions + credit.
Node/device mutations (cordon, drain, register, card mode) and node pools (/node-pools,
/nodes/{id}/pool) live here too. Real values from GpuNode/GpuDevice/Allocation/Session/QueueEntry."""
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
from app.api.schemas.node_pool import (
    NodePoolSet,
    PoolCreate,
    PoolGrantCreate,
    PoolList,
    PoolRead,
    PoolUpdate,
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
    NodePool,
    NodePoolGrant,
    Offering,
    Organization,
    Project,
    QueueEntry,
    Session,
    StorageVolume,
    User,
)
from app.domain.audit_service import AuditService
from app.domain.node_pools import assert_may_grant


class _Conflict(DomainError):
    code, http = "conflict", 409


router = APIRouter(tags=["infra"])


class _Validation(DomainError):
    code, http = "validation_failed", 422


class _BadQuery(DomainError):
    code, http = "invalid_query_parameter", 400


class _PoolInUse(DomainError):
    # Deleting a pool whose nodes still carry live sessions must be deliberate: cordon first.
    code, http = "pool_in_use", 409


async def _pool_names(db: AsyncSession, pool_ids: set[str | None]) -> dict[str, str]:
    ids_ = {p for p in pool_ids if p}
    if not ids_:
        return {}
    return dict((await db.execute(select(NodePool.id, NodePool.name).where(NodePool.id.in_(ids_)))).all())


def _mode_summary(devices) -> tuple[str, dict[str, int]]:
    """Per-mode device counts for a node, plus a single display label.

    Cards on one node can now sit in different pools, so the old single-string collapse is only a
    display hint: one mode shows that mode, several show "mixed", none "-". mode_counts carries
    the real distribution.
    """
    counts: dict[str, int] = {}
    for d in devices or []:
        if d.mode:
            counts[d.mode] = counts.get(d.mode, 0) + 1
    if not counts:
        return "-", counts
    if len(counts) == 1:
        return next(iter(counts)), counts
    return "mixed", counts


async def _node_allocations(db: AsyncSession, node_ids: list[str]) -> dict[str, dict[str, int]]:
    """Host compute promised per node: sum of cpu/mem_gb/disk_gb over sessions holding a live
    resident allocation on the node's cards — the same attribution the scheduler's headroom gate
    uses. CPU-class sessions have no allocation (the k8s scheduler places them), so they are not
    attributed to a node here."""
    if not node_ids:
        return {}
    rows = (
        await db.execute(
            select(
                GpuDevice.node_id,
                func.coalesce(func.sum(Session.cpu), 0),
                func.coalesce(func.sum(Session.mem_gb), 0),
                func.coalesce(func.sum(Session.disk_gb), 0),
            )
            .select_from(Allocation)
            .join(Session, Session.id == Allocation.session_id)
            .join(GpuDevice, GpuDevice.id == Allocation.device_id)
            .where(
                Allocation.ended_at.is_(None),
                Allocation.kind == "resident",
                GpuDevice.node_id.in_(node_ids),
            )
            .group_by(GpuDevice.node_id)
        )
    ).all()
    return {
        r[0]: {"alloc_cpu": int(r[1] or 0), "alloc_mem_gb": int(r[2] or 0), "alloc_disk_gb": int(r[3] or 0)}
        for r in rows
    }


def _node_out(
    n: GpuNode, devices: list[GpuDevice] | None = None, pool_name: str | None = None
) -> dict:
    """Serialise a node in the same shape as a list item; shared by the cordon, drain, register and
    set-pool responses."""
    nd = devices or []
    gpu_mode, mode_counts = _mode_summary(nd)
    return {
        "id": n.id,
        "hostname": n.hostname,
        "status": n.status,
        "cpu": n.cpu or 0,
        "mem_gb": n.mem or 0,
        "disk_gb": n.disk or 0,
        "role": n.role,
        "region": n.region or "-",
        "gpu_mode": gpu_mode,
        "mode_counts": mode_counts,
        "device_count": len(nd),
        "heartbeat_at": ((n.last_seen_at or n.updated_at).isoformat() if (n.last_seen_at or n.updated_at) else None),
        "pool_id": n.pool_id,
        "pool_name": pool_name,
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
    if region:
        stmt = stmt.where(GpuNode.region == region)
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
    allocs = await _node_allocations(db, [n.id for n in nodes])
    # Running sessions per node, keyed by the operator-reported node_hostname.
    sess_counts = dict((await db.execute(
        select(Session.node_hostname, func.count())
        .where(Session.status == "running", Session.node_hostname.is_not(None))
        .group_by(Session.node_hostname)
    )).all())
    pool_names = await _pool_names(db, {n.pool_id for n in nodes})
    out = []
    for n in nodes:
        nd = by_node.get(n.id, [])
        gpu_mode, mode_counts = _mode_summary(nd)
        out.append({
            "id": n.id,
            "hostname": n.hostname,
            "cluster_id": n.cluster_id,
            "cluster_name": cluster_names.get(n.cluster_id) if n.cluster_id else None,
            "status": n.status,
            "cpu": n.cpu or 0,
            "mem_gb": n.mem or 0,
            "disk_gb": n.disk or 0,
            "role": n.role,
            "region": n.region or "-",
            "gpu_mode": gpu_mode,
            "mode_counts": mode_counts,
            "device_count": len(nd),
            **allocs.get(n.id, {"alloc_cpu": 0, "alloc_mem_gb": 0, "alloc_disk_gb": 0}),
            "running_sessions": sess_counts.get(n.hostname, 0),
            "heartbeat_at": ((n.last_seen_at or n.updated_at).isoformat() if (n.last_seen_at or n.updated_at) else None),
            "pool_id": n.pool_id,
            "pool_name": pool_names.get(n.pool_id) if n.pool_id else None,
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
    cname = None
    if n.cluster_id:
        cname = await db.scalar(select(Cluster.name).where(Cluster.id == n.cluster_id))
    gpu_mode, mode_counts = _mode_summary(nd)
    allocs = await _node_allocations(db, [n.id])
    pool_names = await _pool_names(db, {n.pool_id})
    return {
        "id": n.id,
        "hostname": n.hostname,
        "cluster_id": n.cluster_id,
        "cluster_name": cname,
        "status": n.status,
        "cpu": n.cpu or 0,
        "mem_gb": n.mem or 0,
        "disk_gb": n.disk or 0,
        "role": n.role,
        "region": n.region or "-",
        "gpu_mode": gpu_mode,
        "mode_counts": mode_counts,
        "device_count": len(nd),
        **allocs.get(n.id, {"alloc_cpu": 0, "alloc_mem_gb": 0, "alloc_disk_gb": 0}),
        "heartbeat_at": ((n.last_seen_at or n.updated_at).isoformat() if (n.last_seen_at or n.updated_at) else None),
        "pool_id": n.pool_id,
        "pool_name": pool_names.get(n.pool_id) if n.pool_id else None,
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
        hostname=body.hostname, cluster_id=cluster_id,
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

    Both modes cordon FIRST (no new placements can land back), then act on every session holding
    a live allocation on the node's devices:
      - reschedule: pause → resume each running/preparing session. Resume re-runs placement,
        which excludes cordoned nodes — the session comes back on another eligible card (same
        GPU model, enough VRAM/cores, pool access). No capacity ⇒ the session stays PAUSED
        ("parked"): work preserved, owner can resume later.
      - force_terminate: terminate each affected session (settled like an admin stop).
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
    await db.flush()
    await db.commit()   # publish the cordon before touching sessions: resumes must not land here

    from app.domain.session_service import SessionService  # lazy: avoids an import cycle

    svc = SessionService(db)
    rescheduled: list[str] = []
    parked: list[str] = []
    terminated: list[str] = []
    failed: list[str] = []
    for sid in affected:
        sess = await db.get(Session, sid)
        if sess is None:
            continue
        try:
            if body.mode == "force_terminate":
                await svc.terminate(sid, forced=True, reason="admin_stopped")
                terminated.append(sid)
            else:
                if sess.status not in ("running", "preparing"):
                    continue   # queued rows re-place elsewhere on their own once we are cordoned
                # Drain must VACATE the card: a yield-pause keeps the allocation (and would
                # resume onto the same node), so force a cold pause for this stop.
                sess.pause_mode = "cold"
                await db.commit()
                await svc.stop(sid, reason="admin_stopped")
                try:
                    await svc.start(sid)
                    rescheduled.append(sid)
                except Exception:  # noqa: BLE001 — no room elsewhere: parked paused, work preserved
                    parked.append(sid)
        except Exception:  # noqa: BLE001 — one stuck session must not abort the whole drain
            failed.append(sid)
    await AuditService(db).record(
        actor=principal.user_id, action="node.drain", target=node_id, result="ok",
        mode=body.mode, reason=body.reason, affected=len(affected),
        rescheduled=len(rescheduled), parked=len(parked),
        terminated=len(terminated), failed=len(failed),
    )
    await db.commit()
    return {
        "node_id": node_id,
        "status": "cordoned",
        "drain_id": f"drain_{node_id}",
        "mode": body.mode,
        "affected_sessions": affected,
        "rescheduled": rescheduled,
        "parked": parked,
        "terminated": terminated,
        "failed": failed,
    }


# ── Node pools ──────────────────────────────────────────────────────────────────────────────────
# Dedicated nodes per organization / group, honoured by placement (app.domain.node_pools). Pools and
# node assignment are super_admin only; an org_admin sees the pools granted to their organization
# and may sub-assign them to groups in that organization. CPU-only sessions and lending idle
# dedicated cards to the shared pool are out of scope here.

_LIVE_SESSION_STATUSES = ("pending", "preparing", "running", "paused")


def _is_super(principal: Principal) -> bool:
    return "super_admin" in principal.global_roles


def _grant_visible_to(grant: NodePoolGrant, admin_orgs: set[str], group_orgs: dict[str, str]) -> bool:
    """Whether one grant falls inside an org_admin's authority: an org grant for an administered
    organization, or a group grant for a group in one."""
    if grant.scope == "org":
        return grant.scope_id in admin_orgs
    return group_orgs.get(grant.scope_id) in admin_orgs


def visible_pool_ids(
    principal: Principal, grants: list[NodePoolGrant], group_orgs: dict[str, str]
) -> set[str] | None:
    """Pool ids an org_admin may read (None = unrestricted, super_admin). ``group_orgs`` maps
    group id → org id for the groups referenced by ``grants``."""
    if _is_super(principal):
        return None
    admin_orgs = set(principal.org_admin_orgs)
    return {g.pool_id for g in grants if _grant_visible_to(g, admin_orgs, group_orgs)}


async def _group_orgs(db: AsyncSession, group_ids: set[str]) -> dict[str, str]:
    if not group_ids:
        return {}
    return dict((await db.execute(select(Project.id, Project.org_id).where(Project.id.in_(group_ids)))).all())


async def _pool_reads(db: AsyncSession, pools: list[NodePool]) -> list[dict]:
    """Serialise pools with their nodes and grants (batched: one query per relation)."""
    if not pools:
        return []
    pool_ids = [p.id for p in pools]
    nodes = (await db.scalars(select(GpuNode).where(GpuNode.pool_id.in_(pool_ids)))).all()
    node_ids = [n.id for n in nodes]
    dev_counts: dict[str, int] = {}
    if node_ids:
        dev_counts = dict(
            (
                await db.execute(
                    select(GpuDevice.node_id, func.count())
                    .where(GpuDevice.node_id.in_(node_ids))
                    .group_by(GpuDevice.node_id)
                )
            ).all()
        )
    grants = (
        await db.scalars(
            select(NodePoolGrant).where(NodePoolGrant.pool_id.in_(pool_ids)).order_by(NodePoolGrant.created_at)
        )
    ).all()
    org_ids = {g.scope_id for g in grants if g.scope == "org"}
    grp_ids = {g.scope_id for g in grants if g.scope == "group"}
    names: dict[tuple[str, str], str] = {}
    if org_ids:
        for i, n in (await db.execute(select(Organization.id, Organization.name).where(Organization.id.in_(org_ids)))).all():
            names[("org", i)] = n
    if grp_ids:
        for i, n in (await db.execute(select(Project.id, Project.name).where(Project.id.in_(grp_ids)))).all():
            names[("group", i)] = n
    cluster_names = dict(
        (await db.execute(select(Cluster.id, Cluster.name).where(Cluster.id.in_({p.cluster_id for p in pools})))).all()
    )
    nodes_by_pool: dict[str, list[dict]] = {}
    for n in sorted(nodes, key=lambda x: x.hostname):
        nodes_by_pool.setdefault(n.pool_id, []).append(
            {"id": n.id, "hostname": n.hostname, "status": n.status, "device_count": dev_counts.get(n.id, 0)}
        )
    grants_by_pool: dict[str, list[dict]] = {}
    for g in grants:
        grants_by_pool.setdefault(g.pool_id, []).append({
            "id": g.id,
            "scope": g.scope,
            "scope_id": g.scope_id,
            "name": names.get((g.scope, g.scope_id)),
            "created_at": g.created_at.isoformat() if g.created_at else None,
        })
    out = []
    for p in pools:
        pn = nodes_by_pool.get(p.id, [])
        out.append({
            "id": p.id,
            "cluster_id": p.cluster_id,
            "cluster_name": cluster_names.get(p.cluster_id),
            "name": p.name,
            "description": p.description,
            "kind": p.kind,
            "node_count": len(pn),
            "nodes": pn,
            "grants": grants_by_pool.get(p.id, []),
        })
    return out


async def _pool_read(db: AsyncSession, pool: NodePool) -> dict:
    return (await _pool_reads(db, [pool]))[0]


def _restrict_grants(reads: list[dict], principal: Principal, group_orgs: dict[str, str]) -> list[dict]:
    """Tenant isolation for an org_admin: a pool shared by several organizations must not reveal
    the other tenants' org/group ids and names — keep only the grants inside their authority."""
    admin_orgs = set(principal.org_admin_orgs)

    def _visible(g: dict) -> bool:
        if g["scope"] == "org":
            return g["scope_id"] in admin_orgs
        return group_orgs.get(g["scope_id"]) in admin_orgs

    for r in reads:
        r["grants"] = [g for g in r["grants"] if _visible(g)]
    return reads


async def _load_pool(db: AsyncSession, pool_id: str) -> NodePool:
    pool = await db.get(NodePool, pool_id)
    if pool is None:
        raise NotFound("node pool", {"pool_id": pool_id})
    return pool


async def _pool_grants(db: AsyncSession, pool_id: str) -> list[NodePoolGrant]:
    return list((await db.scalars(select(NodePoolGrant).where(NodePoolGrant.pool_id == pool_id))).all())


async def _assert_pool_name_free(db: AsyncSession, cluster_id: str, name: str, exclude: str | None = None) -> None:
    stmt = select(NodePool.id).where(NodePool.cluster_id == cluster_id, NodePool.name == name)
    if exclude:
        stmt = stmt.where(NodePool.id != exclude)
    if await db.scalar(stmt) is not None:
        raise _Conflict("a pool with this name already exists in the cluster", {"cluster_id": cluster_id, "name": name})


async def _live_sessions_on_pool(db: AsyncSession, pool_id: str) -> list[str]:
    """Sessions in a live status holding an open allocation on a device of a node in the pool."""
    rows = (
        await db.scalars(
            select(Allocation.session_id)
            .join(Session, Session.id == Allocation.session_id)
            .join(GpuDevice, GpuDevice.id == Allocation.device_id)
            .join(GpuNode, GpuNode.id == GpuDevice.node_id)
            .where(
                GpuNode.pool_id == pool_id,
                Allocation.ended_at.is_(None),
                Session.status.in_(_LIVE_SESSION_STATUSES),
            )
            .distinct()
        )
    ).all()
    return list(rows)


@router.get("/node-pools", response_model=PoolList)
async def list_node_pools(
    cluster_id: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """List node pools. An org_admin sees only the pools carrying a grant for one of their
    organizations (or a group in them)."""
    principal.require(action="pool.read")
    stmt = select(NodePool).order_by(NodePool.name)
    if cluster_id:
        stmt = stmt.where(NodePool.cluster_id == cluster_id)
    pools = list((await db.scalars(stmt)).all())
    if not _is_super(principal):
        pool_ids = [p.id for p in pools]
        grants = (
            list((await db.scalars(select(NodePoolGrant).where(NodePoolGrant.pool_id.in_(pool_ids)))).all())
            if pool_ids else []
        )
        group_orgs = await _group_orgs(db, {g.scope_id for g in grants if g.scope == "group"})
        visible = visible_pool_ids(principal, grants, group_orgs) or set()
        pools = [p for p in pools if p.id in visible]
    out = await _pool_reads(db, pools)
    if not _is_super(principal):
        out = _restrict_grants(out, principal, group_orgs)
    return {"data": out, "total": len(out)}


@router.post("/node-pools", status_code=status.HTTP_201_CREATED, response_model=PoolRead)
async def create_node_pool(
    body: PoolCreate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    principal.require(action="pool.manage")
    cluster = await db.get(Cluster, body.cluster_id)
    if cluster is None or cluster.deleted_at is not None:
        raise NotFound("cluster", {"cluster_id": body.cluster_id})
    await _assert_pool_name_free(db, body.cluster_id, body.name)
    pool = NodePool(
        id=ids.new("pool"), cluster_id=body.cluster_id, name=body.name,
        description=body.description, kind=body.kind,
    )
    db.add(pool)
    await db.flush()
    await AuditService(db).record(
        actor=principal.user_id, action="pool.create", target=pool.id, result="ok",
        cluster_id=body.cluster_id,
        changes={
            "name": {"from": None, "to": pool.name},
            "kind": {"from": None, "to": pool.kind},
            "description": {"from": None, "to": pool.description},
        },
    )
    await db.commit()
    await db.refresh(pool)
    return await _pool_read(db, pool)


@router.patch("/node-pools/{pool_id}", response_model=PoolRead)
async def update_node_pool(
    pool_id: str,
    body: PoolUpdate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    principal.require(action="pool.manage")
    pool = await _load_pool(db, pool_id)
    changes: dict[str, dict] = {}
    fields = body.model_dump(exclude_unset=True)
    if "name" in fields and fields["name"] is not None and fields["name"] != pool.name:
        await _assert_pool_name_free(db, pool.cluster_id, fields["name"], exclude=pool.id)
    for f in ("name", "description", "kind"):
        if f not in fields:
            continue
        new = fields[f]
        if f != "description" and new is None:
            continue
        old = getattr(pool, f)
        if new != old:
            changes[f] = {"from": old, "to": new}
            setattr(pool, f, new)
    if changes:
        await AuditService(db).record(
            actor=principal.user_id, action="pool.update", target=pool.id, result="ok",
            cluster_id=pool.cluster_id, changes=changes,
        )
        await db.commit()
        await db.refresh(pool)
    return await _pool_read(db, pool)


@router.delete("/node-pools/{pool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node_pool(
    pool_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Delete a pool: its nodes become shared (pool_id NULL) and its grants go with it. Refused
    while any live session holds an allocation on one of its nodes — cordon and drain first."""
    principal.require(action="pool.manage")
    pool = await _load_pool(db, pool_id)
    live = await _live_sessions_on_pool(db, pool.id)
    if live:
        raise _PoolInUse(
            "pool has nodes with live sessions; cordon and terminate them first",
            {"pool_id": pool.id, "sessions": live},
        )
    nodes = list((await db.scalars(select(GpuNode).where(GpuNode.pool_id == pool.id))).all())
    grants = await _pool_grants(db, pool.id)
    # Explicit rather than relying on FK ondelete so SQLite in tests and Postgres behave alike.
    for n in nodes:
        n.pool_id = None
    for g in grants:
        await db.delete(g)
    await db.delete(pool)
    await AuditService(db).record(
        actor=principal.user_id, action="pool.delete", target=pool.id, result="ok",
        cluster_id=pool.cluster_id,
        changes={
            "name": {"from": pool.name, "to": None},
            "kind": {"from": pool.kind, "to": None},
            "nodes": {"from": [n.id for n in nodes], "to": []},
            "grants": {"from": [g.id for g in grants], "to": []},
        },
    )
    await db.commit()
    return None


@router.put("/nodes/{node_id}/pool")
async def set_node_pool(
    node_id: str,
    body: NodePoolSet,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Move a node into a pool (or back to shared with pool_id null). The pool must be in the
    node's cluster. Sessions already on the node are left alone; only new placements change."""
    principal.require(action="pool.manage")
    node = await _load_node(db, node_id)
    pool_name: str | None = None
    if body.pool_id is not None:
        pool = await _load_pool(db, body.pool_id)
        if pool.cluster_id != node.cluster_id:
            raise _Validation(
                "pool belongs to a different cluster than the node",
                {"node_id": node.id, "node_cluster_id": node.cluster_id, "pool_cluster_id": pool.cluster_id},
            )
        pool_name = pool.name
    old = node.pool_id
    devs = await _node_devices(db, node_id)
    if old != body.pool_id:
        old_name = (await _pool_names(db, {old})).get(old) if old else None
        node.pool_id = body.pool_id
        await AuditService(db).record(
            actor=principal.user_id, action="node.set_pool", target=node.id, result="ok",
            cluster_id=node.cluster_id,
            changes={
                "pool_id": {"from": old, "to": body.pool_id},
                "pool_name": {"from": old_name, "to": pool_name},
            },
        )
        await db.commit()
        await db.refresh(node)
    return _node_out(node, devs, pool_name)


async def _grant_target_group(db: AsyncSession, scope: str, scope_id: str) -> Project | None:
    if scope != "group":
        return None
    return await db.scalar(select(Project).where(Project.id == scope_id, Project.deleted_at.is_(None)))


@router.post("/node-pools/{pool_id}/grants", status_code=status.HTTP_201_CREATED)
async def grant_node_pool(
    pool_id: str,
    body: PoolGrantCreate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Grant a pool to an organization or group. super_admin: anything (the target must exist).
    org_admin: only sub-assignment of a pool already granted to their organization, to a group in
    that organization (domain.node_pools.assert_may_grant)."""
    principal.require(action="pool.grant")
    pool = await _load_pool(db, pool_id)
    grants = await _pool_grants(db, pool.id)
    group = await _grant_target_group(db, body.scope, body.scope_id)
    # Permission first so an org_admin learns nothing about foreign pools or groups.
    assert_may_grant(principal, pool, grants, body.scope, body.scope_id, group)
    if body.scope == "group":
        if group is None:
            raise NotFound("group", {"group_id": body.scope_id})
        name = group.name
    else:
        org = await db.scalar(
            select(Organization).where(Organization.id == body.scope_id, Organization.deleted_at.is_(None))
        )
        if org is None:
            raise NotFound("organization", {"org_id": body.scope_id})
        name = org.name
    if any(g.scope == body.scope and g.scope_id == body.scope_id for g in grants):
        raise _Conflict("grant already exists", {"pool_id": pool.id, "scope": body.scope, "scope_id": body.scope_id})
    grant = NodePoolGrant(
        id=ids.new("pool_grant"), pool_id=pool.id, scope=body.scope, scope_id=body.scope_id,
        created_by=principal.user_id,
    )
    db.add(grant)
    await db.flush()
    await AuditService(db).record(
        actor=principal.user_id, action="pool.grant", target=pool.id, result="ok",
        cluster_id=pool.cluster_id, grant_id=grant.id,
        changes={
            "scope": {"from": None, "to": body.scope},
            "scope_id": {"from": None, "to": body.scope_id},
            "name": {"from": None, "to": name},
        },
    )
    await db.commit()
    await db.refresh(grant)
    return {
        "id": grant.id,
        "pool_id": grant.pool_id,
        "scope": grant.scope,
        "scope_id": grant.scope_id,
        "name": name,
        "created_at": grant.created_at.isoformat() if grant.created_at else None,
    }


@router.delete("/node-pools/{pool_id}/grants/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_node_pool_grant(
    pool_id: str,
    grant_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Revoke a grant. Same rule as granting: an org_admin may only remove group grants inside an
    organization the pool is granted to."""
    principal.require(action="pool.grant")
    pool = await _load_pool(db, pool_id)
    grants = await _pool_grants(db, pool.id)
    grant = next((g for g in grants if g.id == grant_id), None)
    if grant is None:
        raise NotFound("node pool grant", {"pool_id": pool.id, "grant_id": grant_id})
    group = await _grant_target_group(db, grant.scope, grant.scope_id)
    assert_may_grant(principal, pool, grants, grant.scope, grant.scope_id, group)
    await db.delete(grant)
    await AuditService(db).record(
        actor=principal.user_id, action="pool.revoke", target=pool.id, result="ok",
        cluster_id=pool.cluster_id, grant_id=grant.id,
        changes={
            "scope": {"from": grant.scope, "to": None},
            "scope_id": {"from": grant.scope_id, "to": None},
        },
    )
    await db.commit()
    return None


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
            "desired_mode": d.desired_mode,
            "mode_state": d.mode_state,
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


class GpuDeviceModeSet(BaseModel):
    # fractional | exclusive | mig; null clears the target (follow observed).
    desired_mode: str | None = Field(default=None, pattern="^(fractional|exclusive|mig)$")


@router.put("/gpu-devices/{device_id}/mode")
async def set_gpu_device_mode(
    device_id: str,
    body: GpuDeviceModeSet,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Set a card's target pool (per-CARD, not per-node). super_admin, same tier as cordon.

    fractional↔exclusive is a metadata change applied as soon as the card is empty; ↔mig also
    needs the node-side geometry change, executed by the drain state machine (the card stops
    accepting placements immediately and flips when its last allocation ends).
    """
    principal.require(action="node.cordon")
    dev = await db.get(GpuDevice, device_id)
    if dev is None:
        raise NotFound("gpu device", {"device_id": device_id})
    if dev.lend_state:
        raise _Conflict(
            "card is yielded or lent; resolve the lend state before changing its pool",
            {"device_id": device_id, "lend_state": dev.lend_state},
        )
    old = {"desired_mode": dev.desired_mode, "mode": dev.mode, "mode_state": dev.mode_state}
    dev.desired_mode = body.desired_mode
    if body.desired_mode is None or body.desired_mode == dev.mode:
        dev.mode_state = "ready"
    else:
        # Stop new placements now; the pool poller applies the change once the card is empty.
        dev.mode_state = "draining"
    await AuditService(db).record(
        actor=principal.user_id, action="gpu_device.set_mode", target=device_id, result="ok",
        # Per-field {field: {from, to}} — the shape the console's audit renderer reads. A single
        # {"from": …, "to": …} envelope rendered as empty dashes.
        changes={
            "desired_mode": {"from": old["desired_mode"], "to": dev.desired_mode},
            "mode_state": {"from": old["mode_state"], "to": dev.mode_state},
        },
        gpu_model=dev.model,
    )
    await db.commit()
    return {
        "id": dev.id, "mode": dev.mode, "desired_mode": dev.desired_mode,
        "mode_state": dev.mode_state,
    }


class PoolTargetsBody(BaseModel):
    # Number of cards that should sit in the MIG pool for this cluster; the rest stay hami-core.
    mig_cards: int = Field(ge=0)


@router.put("/gpu-pools/{cluster_id}")
async def set_pool_targets(
    cluster_id: str,
    body: PoolTargetsBody,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Set the cluster's MIG/hami-core pool split by TARGET COUNT — the console's coarse control
    (simpler and safer than dragging individual cards). The emptiest eligible cards are chosen;
    each transition drains first and runs through the GpuModeChange machinery.
    """
    principal.require(action="node.cordon")
    devs = (
        await db.scalars(
            select(GpuDevice)
            .where(GpuDevice.cluster_id == cluster_id, GpuDevice.status == "ready")
            .with_for_update()
        )
    ).all()
    if not devs:
        raise NotFound("no ready devices in cluster", {"cluster_id": cluster_id})

    def _target_pool(d: GpuDevice) -> str:
        return d.desired_mode or d.mode or "fractional"

    mig_now = [d for d in devs if _target_pool(d) == "mig"]
    core_now = [d for d in devs if _target_pool(d) != "mig"]
    if body.mig_cards > len(devs):
        raise _Conflict("target exceeds card count", {"cards": len(devs), "target": body.mig_cards})

    moved: list[str] = []
    if body.mig_cards > len(mig_now):
        # Emptiest hami-core cards first, skipping lent/yielded ones.
        candidates = sorted(
            (d for d in core_now if not d.lend_state),
            key=lambda d: (d.used_mem_mb, d.used_cores),
        )
        for d in candidates[: body.mig_cards - len(mig_now)]:
            d.desired_mode = "mig"
            d.mode_state = "draining" if d.mode != "mig" else "ready"
            moved.append(d.id)
    elif body.mig_cards < len(mig_now):
        candidates = sorted(
            (d for d in mig_now if not d.lend_state),
            key=lambda d: (d.used_mem_mb, d.used_cores),
        )
        for d in candidates[: len(mig_now) - body.mig_cards]:
            d.desired_mode = "fractional"
            d.mode_state = "draining" if d.mode == "mig" else "ready"
            moved.append(d.id)

    await AuditService(db).record(
        actor=principal.user_id, action="gpu_pool.set_targets", target=cluster_id, result="ok",
        mig_cards=body.mig_cards, moved=moved,
    )
    await db.commit()
    return {
        "cluster_id": cluster_id,
        "mig_cards": body.mig_cards,
        "moved": moved,
        "transitioning": sum(1 for d in devs if d.mode_state != "ready"),
    }


@router.get("/metrics/cluster", response_model=ClusterMetrics)
async def metrics_cluster(
    region: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    principal.require(action="node.read")
    node_stmt = select(GpuNode)
    if region:
        node_stmt = node_stmt.where(GpuNode.region == region)
    nodes = (await db.execute(node_stmt)).scalars().all()
    n_total = len(nodes)
    n_cordoned = sum(1 for n in nodes if n.status == "cordoned")
    n_offline = sum(1 for n in nodes if n.status in ("offline", "down"))

    devs = (await db.execute(select(GpuDevice))).scalars().all()
    # Node status models health (ready|cordoned|offline), never load, so the old residual formula
    # for "busy" was structurally 0. Derive it from the ledger instead: a ready node whose devices
    # hold any live VRAM/core reservation counts as busy, the rest stay ready.
    loaded_node_ids = {d.node_id for d in devs if (d.used_mem_mb or 0) > 0 or (d.used_cores or 0) > 0}
    n_busy = sum(1 for n in nodes if n.status == "ready" and n.id in loaded_node_ids)
    n_ready = sum(1 for n in nodes if n.status == "ready") - n_busy
    vram_total = sum(d.total_mem_mb or 0 for d in devs)
    vram_used = sum(d.used_mem_mb or 0 for d in devs)
    cores_total = sum(d.total_cores or 0 for d in devs)
    cores_used = sum(d.used_cores or 0 for d in devs)
    empty = sum(1 for d in devs if (d.used_mem_mb or 0) == 0 and (d.used_cores or 0) == 0)

    running = (await db.scalar(select(func.count()).select_from(Session).where(Session.status == "running"))) or 0
    queued = (await db.scalar(select(func.count()).select_from(QueueEntry))) or 0

    # Fleet host compute: node capacity vs what active sessions hold (cpu/mem/disk are
    # quota-governed rather than billed, so this is the number administrators watch).
    comp = (
        await db.execute(
            select(
                func.coalesce(func.sum(Session.cpu), 0),
                func.coalesce(func.sum(Session.mem_gb), 0),
                func.coalesce(func.sum(Session.disk_gb), 0),
            ).where(Session.status.in_(("pending", "preparing", "running")))
        )
    ).one()
    # Sessions can only land on gpu/cpu nodes: master and the storage server were inflating the
    # host totals (a 7 TB storage disk is volume capacity, not session scratch space).
    placeable = [n for n in nodes if (n.role or "") in ("gpu", "cpu") or (not n.role and n.id in {d.node_id for d in devs})]
    compute = {
        "cpu": {"used": int(comp[0]), "total": sum(n.cpu or 0 for n in placeable)},
        "mem_gb": {"used": int(comp[1]), "total": sum(n.mem or 0 for n in placeable)},
        "disk_gb": {"used": int(comp[2]), "total": sum(n.disk or 0 for n in placeable)},
    }
    # The storage server, separately: its disk backs volumes (ZFS pool), so "used" is the
    # provisioned volume quota — the same allocation basis the volume-creation gate checks.
    storage_nodes = [n for n in nodes if n.role == "storage"]
    vol_alloc = int(await db.scalar(
        select(func.coalesce(func.sum(StorageVolume.quota_gb), 0)).where(StorageVolume.deleted_at.is_(None))
    ) or 0)
    storage = {
        "disk_gb": {"used": vol_alloc, "total": sum(n.disk or 0 for n in storage_nodes)},
        "node_count": len(storage_nodes),
    } if storage_nodes else None

    # ALLOCATION based, deliberately: this dashboard answers "how much of the fleet is handed out",
    # the same basis as vram_load_pct beside it. Measured utilisation (DCGM) is a different question
    # — an allocated card can sit at 0% — and lives on the monitoring page, where it is labelled as
    # measured. Mixing the two bases in one row made 17% flip to 0% with no visible cause.
    avg_util = round(cores_used / cores_total * 100, 1) if cores_total else 0.0

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
        "compute": compute,
        "storage": storage,
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
