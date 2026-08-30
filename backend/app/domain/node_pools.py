"""Node pools — who may place on which nodes.

An admin dedicates a set of nodes to an organization (a NodePool of kind "dedicated" plus an org
grant); the organization's admin may sub-assign the pool to its groups. Everything else — nodes in
a "shared" pool and nodes with no pool at all — is usable by everyone.

Placement resolves an ordered list of tiers for the requesting session:

    1. pools granted to the session's group
    2. pools granted to the group's organization (not already in tier 1)
    3. shared: every pool of kind "shared" in the cluster, plus ``None`` (unassigned nodes)

The scheduler tries each tier in order and stops at the first that yields a card, so a tenant fills
its own nodes before spilling onto the shared pool. The effective resource policy's
``limits["shared_pool"]`` (bool, default True) may forbid that spill — but only for tenants that
actually hold a dedicated tier; without one they keep the shared tier or could never run anything.

Out of scope (deliberately): CPU-only sessions (they land on ``gshare.io/node-type=cpu`` nodes
regardless of pools), lending idle dedicated cards to the shared pool, and any operator change —
perCardMode already pins the pod to the chosen card.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import Principal
from app.core.errors import Forbidden
from app.db.models import GpuNode, NodePool, NodePoolGrant, Project
from app.domain.policy import resolve_effective_policy

TIER_GROUP = "group"
TIER_ORG = "org"
TIER_SHARED = "shared"


@dataclass
class PoolAccess:
    """The pools one session may place on, ordered by preference."""

    # Ordered, non-empty sets of pool ids; ``None`` stands for unassigned nodes.
    tiers: list[set[str | None]] = field(default_factory=list)
    # For display: {id, name, kind, tier}, in tier order.
    pools: list[dict[str, Any]] = field(default_factory=list)

    def allowed(self) -> set[str | None]:
        out: set[str | None] = set()
        for t in self.tiers:
            out |= t
        return out

    def tier_of(self, pool_id: str | None) -> int | None:
        for i, t in enumerate(self.tiers):
            if pool_id in t:
                return i
        return None


async def resolve_pool_access(
    db: AsyncSession, *, cluster_id: str, user_id: str, group_id: str | None
) -> PoolAccess:
    """Resolve the ordered pool tiers for (user, group) in ``cluster_id``.

    Sessions carry a group; with no group only the shared tier applies. Every query is scoped to
    the cluster and to the caller's own group/org ids — other tenants' grants are never read.
    """
    pools = {
        p.id: p
        for p in (await db.scalars(select(NodePool).where(NodePool.cluster_id == cluster_id))).all()
    }
    org_id: str | None = None
    if group_id:
        project = await db.get(Project, group_id)
        org_id = project.org_id if project is not None else None

    group_pools: set[str | None] = set()
    org_pools: set[str | None] = set()
    conds = []
    if group_id:
        conds.append((NodePoolGrant.scope == "group") & (NodePoolGrant.scope_id == group_id))
    if org_id:
        conds.append((NodePoolGrant.scope == "org") & (NodePoolGrant.scope_id == org_id))
    if pools and conds:
        rows = (
            await db.execute(
                select(NodePoolGrant.pool_id, NodePoolGrant.scope).where(
                    NodePoolGrant.pool_id.in_(list(pools)), or_(*conds)
                )
            )
        ).all()
        for pool_id, scope in rows:
            (group_pools if scope == "group" else org_pools).add(pool_id)
    org_pools -= group_pools
    shared: set[str | None] = {p.id for p in pools.values() if p.kind == "shared"}
    shared.add(None)
    # Shared-pool nodes are shared even when a grant names them; keep them in the shared tier.
    group_pools = {p for p in group_pools if pools[p].kind != "shared"}
    org_pools = {p for p in org_pools if pools[p].kind != "shared"}

    access = PoolAccess()
    if group_pools:
        access.tiers.append(group_pools)
    if org_pools:
        access.tiers.append(org_pools)
    keep_shared = True
    if access.tiers:
        pol = await resolve_effective_policy(db, user_id, group_id)
        if pol is not None and (pol.limits or {}).get("shared_pool") is False:
            keep_shared = False
    if keep_shared:
        access.tiers.append(shared)

    for tier_name, ids_ in (
        (TIER_GROUP, group_pools),
        (TIER_ORG, org_pools),
        (TIER_SHARED, shared if keep_shared else set()),
    ):
        for pid in sorted(ids_, key=lambda x: (x is None, pools[x].name if x else "")):
            if pid is None:
                continue
            p = pools[pid]
            access.pools.append({"id": p.id, "name": p.name, "kind": p.kind, "tier": tier_name})
    return access


async def node_pool_map(db: AsyncSession, node_ids: set[str]) -> dict[str, str | None]:
    """{node_id: pool_id} for ``node_ids`` in one query; unknown nodes map to None (shared)."""
    if not node_ids:
        return {}
    rows = (
        await db.execute(select(GpuNode.id, GpuNode.pool_id).where(GpuNode.id.in_(node_ids)))
    ).all()
    out: dict[str, str | None] = {n: None for n in node_ids}
    for node_id, pool_id in rows:
        out[node_id] = pool_id
    return out


def assert_may_grant(
    principal: Principal,
    pool: NodePool,
    grants: list[NodePoolGrant],
    scope: str,
    scope_id: str,
    group: Project | None = None,
) -> None:
    """Pure permission rule for adding/removing a grant on ``pool``. Raises Forbidden.

    super_admin: anything. org_admin: sub-assignment only — the pool must already carry an org
    grant for an organization they administer, ``scope`` must be "group", and ``group`` (the
    target Project row) must belong to that organization.
    """
    if "super_admin" in (principal.global_roles or set()) or principal.global_role == "super_admin":
        return
    admin_orgs = set(principal.org_admin_orgs or set())
    if not admin_orgs:
        raise Forbidden("not permitted: pool.grant")
    granted_orgs = {g.scope_id for g in grants if g.pool_id == pool.id and g.scope == "org"}
    my_granted = granted_orgs & admin_orgs
    if not my_granted:
        raise Forbidden("pool is not granted to an organization you administer")
    if scope != "group":
        raise Forbidden("organization admins may only sub-assign pools to their groups")
    if group is None or group.id != scope_id or group.org_id not in my_granted:
        raise Forbidden("group is not in an organization this pool is granted to")


async def accessible_devices(db, user_id: str, group_ids: list[str | None], devices):
    """Filter GpuDevice rows to those on nodes the caller may place on.

    The union of the allowed pool sets across the caller's groups, per cluster — the SAME rule the
    scheduler applies at admission, so every availability surface (dashboard, session wizard)
    shows exactly what a create would accept. group_ids may be [None] for a user with no
    membership (super admin browsing): only the shared tier remains.
    """
    if not devices:
        return []
    node_pool = await node_pool_map(db, {d.node_id for d in devices})
    allowed_by_cluster: dict[str, set[str | None]] = {}
    for cid in sorted({d.cluster_id for d in devices}):
        allowed: set[str | None] = set()
        for g in group_ids:
            access = await resolve_pool_access(db, cluster_id=cid, user_id=user_id, group_id=g)
            allowed |= access.allowed()
        allowed_by_cluster[cid] = allowed
    return [d for d in devices if node_pool.get(d.node_id) in allowed_by_cluster.get(d.cluster_id, set())]
