"""Node pools: dedicated nodes per organization / group, honoured by placement.

Fleet: pool P (dedicated, granted to org A) holds nodes n1–n2; node n3 is unassigned (shared).
One fractional 16 GB card per node.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.dashboard_router import dashboard_summary
from app.api.schemas.session import SessionCreate
from app.auth.rbac import Principal
from app.core import ids
from app.core.errors import Forbidden, Unserviceable
from app.db.models import (
    Allocation,
    GpuDevice,
    GpuNode,
    Image,
    Membership,
    NodePool,
    NodePoolGrant,
    Offering,
    Organization,
    Project,
    ResourcePolicy,
)
from app.db.models import Session as SessionRow
from app.domain.node_pools import assert_may_grant, resolve_pool_access
from app.domain.scheduler import SchedulerService

pytestmark = pytest.mark.asyncio

MODEL = "A100"
CARD_MB = 16000


class Fleet:
    pass


async def _fleet(db, *, shared_nodes: int = 1) -> Fleet:
    f = Fleet()
    f.cluster_id = ids.new("cluster")
    f.org_a = Organization(id=ids.new("org"), name="org A")
    f.org_b = Organization(id=ids.new("org"), name="org B")
    f.g1 = Project(id=ids.new("group"), org_id=f.org_a.id, name="G1")
    f.g2 = Project(id=ids.new("group"), org_id=f.org_a.id, name="G2")
    f.gb = Project(id=ids.new("group"), org_id=f.org_b.id, name="GB")
    f.user_a = ids.new("user")
    f.user_a2 = ids.new("user")
    f.user_b = ids.new("user")
    f.offering = Offering(
        id=ids.new("offering"), name="A100-frac", resource_class="gpu",
        gpu_model=MODEL, gpu_mem_mb=CARD_MB, gpu_cores=100, credit_per_hour=Decimal("60"),
    )
    f.image = Image(id=ids.new("image"), name="pytorch")
    f.pool = NodePool(id=ids.new("pool"), cluster_id=f.cluster_id, name="P", kind="dedicated")
    f.grant_a = NodePoolGrant(
        id=ids.new("pool_grant"), pool_id=f.pool.id, scope="org", scope_id=f.org_a.id
    )
    rows = [f.org_a, f.org_b, f.g1, f.g2, f.gb, f.offering, f.image, f.pool, f.grant_a]
    f.pool_devs: list[str] = []
    f.shared_devs: list[str] = []
    for i in range(1, 3 + shared_nodes):
        in_pool = i <= 2
        node = GpuNode(
            id=ids.new("node"), cluster_id=f.cluster_id, hostname=f"n{i}", status="ready",
            pool_id=f.pool.id if in_pool else None,
        )
        dev = GpuDevice(
            id=f"dev-{i}", node_id=node.id, cluster_id=f.cluster_id, model=MODEL,
            gpu_uuid=f"GPU-{i}", total_mem_mb=CARD_MB, status="ready", mode="fractional",
        )
        rows += [node, dev]
        (f.pool_devs if in_pool else f.shared_devs).append(dev.id)
    async with db.begin():
        db.add_all(rows)
    return f


def _session(f: Fleet, user_id: str, group: Project, mem_mb: int = 8000) -> SessionRow:
    return SessionRow(
        id=ids.new("session"), owner_user_id=user_id, group_id=group.id,
        cluster_id=f.cluster_id, offering_id=f.offering.id, image_id=f.image.id,
        resource_class="gpu", mode="fractional", gpu_mem_mb=mem_mb, gpu_cores=50,
        status="pending",
    )


def _req(f: Fleet, group: Project, mem_mb: int = 8000) -> SessionCreate:
    return SessionCreate(
        offering_id=f.offering.id, image_id=f.image.id, resource_class="gpu",
        cluster_id=f.cluster_id, group_id=group.id, mode="fractional",
        gpu_mem_mb=mem_mb, gpu_cores=50,
    )


async def _reserve(db, f, user_id, group, mem_mb=8000) -> str | None:
    """reserve_slice for one session; returns the device it landed on (None = no fit)."""
    sess = _session(f, user_id, group, mem_mb)
    async with db.begin():
        db.add(sess)
        ok = await SchedulerService(db).reserve_slice(sess, _req(f, group, mem_mb))
        if not ok:
            return None
        alloc = (
            await db.scalars(select(Allocation).where(Allocation.session_id == sess.id))
        ).first()
        return alloc.device_id


async def _fill(db, dev_id: str) -> None:
    """Occupy a card completely (used = total)."""
    async with db.begin():
        dev = await db.get(GpuDevice, dev_id)
        dev.used_mem_mb = dev.total_mem_mb
        dev.used_cores = dev.total_cores
        db.add(Allocation(
            id=ids.new("allocation"), session_id=ids.new("session"), device_id=dev.id,
            gpu_uuid=dev.gpu_uuid, gpu_mem_mb=dev.total_mem_mb, gpu_cores=dev.total_cores,
            status="bound",
        ))


# 1
async def test_org_b_never_lands_on_dedicated_pool(db):
    f = await _fleet(db)
    await _fill(db, f.shared_devs[0])
    # P is the only room left; org B holds no grant → no placement (caller queues).
    assert await _reserve(db, f, f.user_b, f.gb) is None
    assert (await db.scalar(select(GpuDevice.used_mem_mb).where(GpuDevice.id == "dev-1"))) == 0


# 2
async def test_org_a_prefers_pool_then_falls_back_to_shared(db):
    f = await _fleet(db)
    assert await _reserve(db, f, f.user_a, f.g1) in f.pool_devs
    assert await _reserve(db, f, f.user_a, f.g1, CARD_MB) in f.pool_devs  # second card whole
    assert await _reserve(db, f, f.user_a, f.g1) in f.pool_devs  # packs next to the first
    # P is now full (one card holds a whole-card slice, the other two 8000 halves).
    assert await _reserve(db, f, f.user_a, f.g1) == f.shared_devs[0]


# 3
async def test_shared_pool_policy_blocks_fallback(db):
    f = await _fleet(db)
    async with db.begin():
        db.add(ResourcePolicy(
            id=ids.new("policy"), scope="org", scope_id=f.org_a.id, limits={"shared_pool": False}
        ))
    await _fill(db, f.pool_devs[0])
    await _fill(db, f.pool_devs[1])
    assert await _reserve(db, f, f.user_a, f.g1) is None
    # Org B (no dedicated pool) is unaffected by its absent policy and uses the shared node.
    assert await _reserve(db, f, f.user_b, f.gb) == f.shared_devs[0]


# 4
async def test_group_sub_grant_tier_order(db):
    f = await _fleet(db)
    async with db.begin():
        db.add(NodePoolGrant(
            id=ids.new("pool_grant"), pool_id=f.pool.id, scope="group", scope_id=f.g1.id
        ))
    a1 = await resolve_pool_access(db, cluster_id=f.cluster_id, user_id=f.user_a, group_id=f.g1.id)
    assert a1.tier_of(f.pool.id) == 0 and a1.tier_of(None) == 1
    assert a1.pools[0] == {"id": f.pool.id, "name": "P", "kind": "dedicated", "tier": "group"}
    a2 = await resolve_pool_access(db, cluster_id=f.cluster_id, user_id=f.user_a2, group_id=f.g2.id)
    assert a2.tier_of(f.pool.id) == 0 and a2.pools[0]["tier"] == "org"
    await db.commit()
    # G2 (same org, no group grant) still places on P through the org tier.
    assert await _reserve(db, f, f.user_a2, f.g2) in f.pool_devs
    # No group at all → shared only.
    a0 = await resolve_pool_access(db, cluster_id=f.cluster_id, user_id=f.user_b, group_id=None)
    assert a0.allowed() == {None}


# 5
async def test_unassigned_nodes_are_shared_for_everyone(db):
    f = await _fleet(db)
    assert await _reserve(db, f, f.user_b, f.gb) == f.shared_devs[0]
    ab = await resolve_pool_access(db, cluster_id=f.cluster_id, user_id=f.user_b, group_id=f.gb.id)
    assert ab.allowed() == {None}
    assert f.pool.id not in ab.allowed()


# 6
async def test_unserviceable_when_only_matching_cards_are_dedicated(db):
    f = await _fleet(db, shared_nodes=0)
    sess = _session(f, f.user_b, f.gb)
    with pytest.raises(Unserviceable):
        await SchedulerService(db)._assert_serviceable(sess, _req(f, f.gb))
    # Org A is serviceable on the same fleet.
    await SchedulerService(db)._assert_serviceable(_session(f, f.user_a, f.g1), _req(f, f.g1))


# 7
async def test_dashboard_regions_exclude_dedicated_vram_for_org_b(db):
    f = await _fleet(db)
    async with db.begin():
        db.add_all([
            Membership(id=ids.new("membership"), user_id=f.user_b, group_id=f.gb.id, role="member"),
            Membership(id=ids.new("membership"), user_id=f.user_a, group_id=f.g1.id, role="member"),
        ])
    out_b = await dashboard_summary(principal=Principal(user_id=f.user_b), db=db)
    assert [r["total_mb"] for r in out_b["regions"]] == [CARD_MB]
    assert out_b["pools"] == [{"id": None, "name": "shared", "kind": "shared", "tier": "shared"}]
    # The cluster VRAM KPI is scoped to accessible cards too, not the whole fleet.
    assert out_b["vram"]["total_mb"] == CARD_MB
    out_a = await dashboard_summary(principal=Principal(user_id=f.user_a), db=db)
    assert [r["total_mb"] for r in out_a["regions"]] == [3 * CARD_MB]
    assert out_a["vram"]["total_mb"] == 3 * CARD_MB
    assert [p["name"] for p in out_a["pools"]] == ["P", "shared"]


# 8
async def test_assert_may_grant_sub_assignment_rule():
    org_a, org_b = ids.new("org"), ids.new("org")
    pool = NodePool(id=ids.new("pool"), cluster_id="c", name="P", kind="dedicated")
    other = NodePool(id=ids.new("pool"), cluster_id="c", name="Q", kind="dedicated")
    grants = [NodePoolGrant(id=ids.new("pool_grant"), pool_id=pool.id, scope="org", scope_id=org_a)]
    ga = Project(id=ids.new("group"), org_id=org_a, name="GA")
    gb = Project(id=ids.new("group"), org_id=org_b, name="GB")
    admin_a = Principal(user_id="u", global_roles=set(), org_admin_orgs={org_a})

    assert_may_grant(admin_a, pool, grants, "group", ga.id, ga)  # allowed
    with pytest.raises(Forbidden):
        assert_may_grant(admin_a, pool, grants, "group", gb.id, gb)  # group in org B
    with pytest.raises(Forbidden):
        assert_may_grant(admin_a, other, grants, "group", ga.id, ga)  # pool has no org-A grant
    with pytest.raises(Forbidden):
        assert_may_grant(admin_a, pool, grants, "org", org_b, None)  # org grants are super only
    with pytest.raises(Forbidden):
        assert_may_grant(Principal(user_id="m", memberships={ga.id: "group_admin"}),
                         pool, grants, "group", ga.id, ga)
    su = Principal(user_id="s", global_roles={"super_admin"})
    assert_may_grant(su, other, grants, "org", org_b, None)


# 9 — active preemption honours pools: never yield a card the preemptor cannot borrow.
async def _resident_on(db, f, dev_id: str, user_id: str, group: Project) -> SessionRow:
    """A running exclusive yield-mode resident (priority 0) holding ``dev_id``."""
    resident = SessionRow(
        id=ids.new("session"), owner_user_id=user_id, group_id=group.id,
        cluster_id=f.cluster_id, offering_id=f.offering.id, image_id=f.image.id,
        resource_class="gpu", mode="exclusive", pause_mode="yield", priority=0,
        status="running",
    )
    async with db.begin():
        dev = await db.get(GpuDevice, dev_id)
        dev.mode = "exclusive"
        dev.used_mem_mb = dev.total_mem_mb
        dev.used_cores = dev.total_cores
        db.add(resident)
        db.add(Allocation(
            id=ids.new("allocation"), session_id=resident.id, device_id=dev.id,
            gpu_uuid=dev.gpu_uuid, gpu_mem_mb=dev.total_mem_mb, gpu_cores=dev.total_cores,
            status="bound", kind="resident",
        ))
    return resident


def _preemptor(f, user_id: str, group: Project) -> SessionRow:
    return SessionRow(
        id=ids.new("session"), owner_user_id=user_id, group_id=group.id,
        cluster_id=f.cluster_id, offering_id=f.offering.id, image_id=f.image.id,
        resource_class="gpu", mode="exclusive", priority=1, preemptible=True, status="pending",
    )


async def test_preemption_skips_residents_on_pools_the_requester_cannot_use(db, monkeypatch):
    """Org B (priority 1) outranks org A's resident on dedicated pool P, but may not borrow P's
    card afterwards — so the resident must not be yielded. Org A's own preemptor may."""
    from app.domain import session_service

    f = await _fleet(db, shared_nodes=0)
    resident = await _resident_on(db, f, f.pool_devs[0], f.user_a, f.g1)
    stopped: list[tuple[str, str]] = []

    async def _fake_stop(self, session_id, reason=None, **_):
        stopped.append((session_id, reason))

    monkeypatch.setattr(session_service.SessionService, "stop", _fake_stop)
    svc = SchedulerService(db)

    pre_b = _preemptor(f, f.user_b, f.gb)
    async with db.begin():
        db.add(pre_b)
    req = SessionCreate(
        offering_id=f.offering.id, image_id=f.image.id, resource_class="gpu",
        cluster_id=f.cluster_id, group_id=f.gb.id, mode="exclusive",
    )
    assert await svc._preempt_lower_priority(pre_b, req) is False
    assert stopped == []
    fresh = await db.get(SessionRow, resident.id)
    assert fresh.status == "running"

    pre_a = _preemptor(f, f.user_a2, f.g2)
    async with db.begin():
        db.add(pre_a)
    req_a = req.model_copy(update={"group_id": f.g2.id})
    assert await svc._preempt_lower_priority(pre_a, req_a) is True
    assert stopped == [(resident.id, "preempted")]


@pytest.mark.asyncio
async def test_wizard_availability_hides_dedicated_models(db):
    """/sessions/gpu-availability applies the same pool filter as the dashboard: a model that
    exists only in another org's dedicated pool is not offered."""
    from app.api.sessions_router import gpu_availability

    f = await _fleet(db)
    async with db.begin():
        db.add_all([
            Membership(id=ids.new("membership"), user_id=f.user_b, group_id=f.gb.id, role="member"),
            Membership(id=ids.new("membership"), user_id=f.user_a, group_id=f.g1.id, role="member"),
        ])
    out_b = await gpu_availability(
        principal=Principal(user_id=f.user_b, memberships={f.gb.id: "member"}), db=db)
    assert [m["gpu_model"] for m in out_b["data"]] == ["SHARED-CARD"] or all(
        "DEDICATED" not in m["gpu_model"] for m in out_b["data"]
    )
    out_a = await gpu_availability(
        principal=Principal(user_id=f.user_a, memberships={f.g1.id: "member"}), db=db)
    assert len(out_a["data"]) >= len(out_b["data"])
    # org B sees strictly fewer devices than org A (the dedicated cards are hidden).
    def ndev(o):
        return sum(len(m["devices"]) for m in o["data"])
    assert ndev(out_b) < ndev(out_a)
