"""Regression tests for cross-tenant read isolation on list/get/report endpoints.

These three endpoints previously called the unscoped guard ``principal.require(action=...)``
(group_id=None), which passes for ANY membership of sufficient rank — so a member/admin of one
tenant could enumerate or read every other tenant's resource policies, budgets, and billing
financials. Each test asserts a non-super admin of org/group A sees only A and is denied B.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.api.budgets_router import list_budgets
from app.api.deps import Pagination
from app.api.infra_router import billing_report
from app.api.policies_router import get_policy, list_policies
from app.auth.rbac import Principal
from app.core.errors import Forbidden
from app.db.models import Budget, CreditWallet, ResourcePolicy

pytestmark = pytest.mark.asyncio


def _org_admin(org_id: str, user_id: str = "u_admin") -> Principal:
    return Principal(user_id=user_id, global_roles=set(), org_admin_orgs={org_id})


def _group_admin(group_id: str, user_id: str = "u_gadmin") -> Principal:
    return Principal(user_id=user_id, global_roles=set(), memberships={group_id: "group_admin"})


def _super() -> Principal:
    return Principal(user_id="u_super", global_roles={"super_admin"})


# --------------------------------------------------------------------------- policies

async def _seed_policies(db) -> None:
    db.add_all([
        ResourcePolicy(id="pol_orgA", scope="org", scope_id="org_A", max_concurrent=1, limits={}),
        ResourcePolicy(id="pol_orgB", scope="org", scope_id="org_B", max_concurrent=1, limits={}),
        ResourcePolicy(id="pol_grpA", scope="group", scope_id="grp_A", max_concurrent=1, limits={}),
        ResourcePolicy(id="pol_grpB", scope="group", scope_id="grp_B", max_concurrent=1, limits={}),
        ResourcePolicy(id="pol_global", scope="global", scope_id="*", max_concurrent=1, limits={}),
        ResourcePolicy(id="pol_user", scope="user", scope_id="u_x", max_concurrent=1, limits={}),
    ])
    await db.flush()


async def test_list_policies_org_admin_scoped(db):
    await _seed_policies(db)
    res = await list_policies(page=Pagination(1, 50), scope=None, scope_id=None, principal=_org_admin("org_A"), db=db)
    ids = {r["id"] for r in res["data"]}
    assert ids == {"pol_orgA"}, ids
    assert res["pagination"]["total"] == 1


async def test_list_policies_group_admin_scoped(db):
    await _seed_policies(db)
    res = await list_policies(page=Pagination(1, 50), scope=None, scope_id=None, principal=_group_admin("grp_A"), db=db)
    ids = {r["id"] for r in res["data"]}
    assert ids == {"pol_grpA"}, ids


async def test_list_policies_super_sees_all(db):
    await _seed_policies(db)
    res = await list_policies(page=Pagination(1, 50), scope=None, scope_id=None, principal=_super(), db=db)
    assert len(res["data"]) == 6


async def test_get_policy_cross_tenant_denied(db):
    await _seed_policies(db)
    # org_A admin may read its own org policy ...
    own = await get_policy("pol_orgA", principal=_org_admin("org_A"), db=db)
    assert own["id"] == "pol_orgA"
    # ... but is forbidden the other tenant's policy (previously leaked).
    with pytest.raises(Forbidden):
        await get_policy("pol_orgB", principal=_org_admin("org_A"), db=db)
    # ... and global/user-scoped policies are super_admin only.
    with pytest.raises(Forbidden):
        await get_policy("pol_global", principal=_org_admin("org_A"), db=db)


# --------------------------------------------------------------------------- budgets

async def _seed_budgets(db) -> None:
    ps = datetime(2026, 6, 1, tzinfo=UTC)
    db.add_all([
        Budget(id="bdg_orgA", scope="org", scope_id="org_A", period_start=ps, limit_credit=Decimal(100)),
        Budget(id="bdg_orgB", scope="org", scope_id="org_B", period_start=ps, limit_credit=Decimal(100)),
        Budget(id="bdg_grpA", scope="group", scope_id="grp_A", period_start=ps, limit_credit=Decimal(100)),
        Budget(id="bdg_grpB", scope="group", scope_id="grp_B", period_start=ps, limit_credit=Decimal(100)),
    ])
    await db.flush()


async def test_list_budgets_org_admin_scoped(db):
    await _seed_budgets(db)
    rows = await list_budgets(page=Pagination(1, 50), scope=None, scope_id=None, action=None, principal=_org_admin("org_A"), db=db)
    assert {r.id for r in rows} == {"bdg_orgA"}


async def test_list_budgets_group_admin_scoped(db):
    await _seed_budgets(db)
    # group_admin of grp_A — list requires group_admin (matrix); sees only grp_A budget.
    rows = await list_budgets(page=Pagination(1, 50), scope=None, scope_id=None, action=None, principal=_group_admin("grp_A"), db=db)
    assert {r.id for r in rows} == {"bdg_grpA"}


async def test_list_budgets_super_sees_all(db):
    await _seed_budgets(db)
    rows = await list_budgets(page=Pagination(1, 50), scope=None, scope_id=None, action=None, principal=_super(), db=db)
    assert len(rows) == 4


# --------------------------------------------------------------------------- billing report

async def _seed_wallets(db) -> None:
    db.add_all([
        CreditWallet(id="wal_orgA", owner_type="org", owner_id="org_A", balance=Decimal(0)),
        CreditWallet(id="wal_orgB", owner_type="org", owner_id="org_B", balance=Decimal(0)),
        CreditWallet(id="wal_grpB", owner_type="group", owner_id="grp_B", balance=Decimal(0)),
    ])
    await db.flush()


def _window():
    frm = datetime(2026, 6, 1, tzinfo=UTC)
    to = datetime(2026, 6, 30, tzinfo=UTC)
    return frm, to


async def _report(db, principal, scope, scope_id):
    frm, to = _window()
    return await billing_report(
        scope=scope, scope_id=scope_id, from_=frm, to=to,
        group_by="group", format="json", principal=principal, db=db,
    )


async def test_billing_report_own_org_allowed(db):
    await _seed_wallets(db)
    rep = await _report(db, _org_admin("org_A"), "org", "org_A")
    assert rep is not None


async def test_billing_report_cross_org_denied(db):
    await _seed_wallets(db)
    with pytest.raises(Forbidden):
        await _report(db, _org_admin("org_A"), "org", "org_B")


async def test_billing_report_cross_group_denied(db):
    await _seed_wallets(db)
    with pytest.raises(Forbidden):
        await _report(db, _group_admin("grp_A"), "group", "grp_B")


async def test_billing_report_wallet_owner_authorized(db):
    await _seed_wallets(db)
    # org_A admin may pull its own org wallet ...
    assert await _report(db, _org_admin("org_A"), "wallet", "wal_orgA") is not None
    # ... but not another tenant's wallet (previously leaked financials).
    with pytest.raises(Forbidden):
        await _report(db, _org_admin("org_A"), "wallet", "wal_grpB")


async def test_billing_report_no_scope_id_super_only(db):
    await _seed_wallets(db)
    # whole-platform aggregate must be super_admin only.
    with pytest.raises(Forbidden):
        await _report(db, _org_admin("org_A"), "org", None)
    # super_admin is unrestricted.
    assert await _report(db, _super(), "org", None) is not None


# --------------------------------------------------------------------------- node pools

async def test_dedicated_pool_cards_invisible_to_other_org_in_placement(db):
    """Org B never places on (or even counts) the cards an admin dedicated to org A."""
    from app.db.models import GpuDevice, GpuNode, NodePool, NodePoolGrant, Project
    from app.domain.node_pools import resolve_pool_access

    cluster_id = "clu_pool"
    pool = NodePool(id="npl_A", cluster_id=cluster_id, name="A-only", kind="dedicated")
    db.add_all([
        Project(id="grp_A2", org_id="org_A", name="A2"),
        Project(id="grp_B2", org_id="org_B", name="B2"),
        pool,
        NodePoolGrant(id="pgr_A", pool_id=pool.id, scope="org", scope_id="org_A"),
        GpuNode(id="nod_A", cluster_id=cluster_id, hostname="a", status="ready", pool_id=pool.id),
        GpuDevice(id="dev_A", node_id="nod_A", cluster_id=cluster_id, model="A100",
                  gpu_uuid="GPU-A", total_mem_mb=16000, status="ready", mode="fractional"),
    ])
    await db.flush()
    access_b = await resolve_pool_access(db, cluster_id=cluster_id, user_id="u_b", group_id="grp_B2")
    assert pool.id not in access_b.allowed()
    assert access_b.allowed() == {None}
    access_a = await resolve_pool_access(db, cluster_id=cluster_id, user_id="u_a", group_id="grp_A2")
    assert access_a.tier_of(pool.id) == 0


async def test_list_node_pools_hides_other_tenants_grants(db):
    """A pool granted to both A and B: A's org_admin sees the pool but only A's grants."""
    from app.api.infra_router import list_node_pools
    from app.db.models import Cluster, NodePool, NodePoolGrant, Organization, Project

    db.add_all([
        Cluster(id="clu_share", name="c", api_server="https://x", runtime="k8s", kubeconfig_secret_ref="s"),
        Organization(id="org_A", name="A"),
        Organization(id="org_B", name="B"),
        Project(id="grp_A3", org_id="org_A", name="A3"),
        Project(id="grp_B3", org_id="org_B", name="B3"),
        NodePool(id="npl_S", cluster_id="clu_share", name="shared-by-two", kind="dedicated"),
        NodePoolGrant(id="pgr_oA", pool_id="npl_S", scope="org", scope_id="org_A"),
        NodePoolGrant(id="pgr_oB", pool_id="npl_S", scope="org", scope_id="org_B"),
        NodePoolGrant(id="pgr_gA", pool_id="npl_S", scope="group", scope_id="grp_A3"),
        NodePoolGrant(id="pgr_gB", pool_id="npl_S", scope="group", scope_id="grp_B3"),
    ])
    await db.flush()
    res = await list_node_pools(cluster_id="clu_share", principal=_org_admin("org_A"), db=db)
    assert res["total"] == 1
    seen = {(g["scope"], g["scope_id"]) for g in res["data"][0]["grants"]}
    assert seen == {("org", "org_A"), ("group", "grp_A3")}
    names = {g["name"] for g in res["data"][0]["grants"]}
    assert "B" not in names and "B3" not in names
    # super_admin still sees every grant.
    res = await list_node_pools(cluster_id="clu_share", principal=_super(), db=db)
    assert len(res["data"][0]["grants"]) == 4


async def test_policy_shared_pool_limit_round_trips(db):
    """limits.shared_pool survives create, PATCH and GET (the console's fallback switch)."""
    from app.api.policies_router import PolicyCreate, PolicyUpdate, create_policy, update_policy

    body = PolicyCreate(
        scope="org", scope_id="org_A", max_concurrent=1, max_queued=1, max_runtime_min=60,
        idle_timeout_sec=600, limits={"cpu": 4, "shared_pool": False},
    )
    created = await create_policy(body, principal=_super(), db=db)
    assert created["limits"]["shared_pool"] is False
    got = await get_policy(created["id"], principal=_org_admin("org_A"), db=db)
    assert got["limits"] == {"cpu": 4, "shared_pool": False}
    upd = await update_policy(
        created["id"], PolicyUpdate(limits={"shared_pool": True}), principal=_super(), db=db
    )
    assert upd["limits"] == {"cpu": 4, "shared_pool": True}
    upd = await update_policy(
        created["id"], PolicyUpdate(limits={"shared_pool": False}), principal=_super(), db=db
    )
    assert upd["limits"]["shared_pool"] is False
