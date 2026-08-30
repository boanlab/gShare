"""Node pool admin API: RBAC actions, the org_admin sub-assignment rule, node→pool assignment,
delete-under-live-sessions refusal, and the pool fields on node reads.

Router functions are called directly with a Principal + the in-memory db (the pattern the rest of
the suite uses for admin routers); no ASGI app is stood up.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.api.infra_router import (
    _PoolInUse,
    _Validation,
    create_node_pool,
    delete_node_pool,
    get_node,
    grant_node_pool,
    list_node_pools,
    list_nodes,
    revoke_node_pool_grant,
    set_node_pool,
    update_node_pool,
    visible_pool_ids,
)
from app.api.schemas.node_pool import NodePoolSet, PoolCreate, PoolGrantCreate, PoolUpdate
from app.auth.rbac import Principal, rbac_allows
from app.core import ids
from app.core.errors import DomainError, Forbidden, NotFound
from app.db.models import (
    Allocation,
    AuditLog,
    Cluster,
    GpuDevice,
    GpuNode,
    Image,
    NodePool,
    NodePoolGrant,
    Offering,
    Organization,
    Project,
)
from app.db.models import Session as SessionRow

pytestmark = pytest.mark.asyncio


class World:
    pass


async def _world(db) -> World:
    w = World()
    w.cluster = Cluster(
        id=ids.new("cluster"), name="c1", api_server="https://k8s", runtime="k3s",
        kubeconfig_secret_ref="s", status="ready",
    )
    w.cluster2 = Cluster(
        id=ids.new("cluster"), name="c2", api_server="https://k8s2", runtime="k3s",
        kubeconfig_secret_ref="s", status="ready",
    )
    w.org_a = Organization(id=ids.new("org"), name="org A")
    w.org_b = Organization(id=ids.new("org"), name="org B")
    w.g1 = Project(id=ids.new("group"), org_id=w.org_a.id, name="G1")
    w.gb = Project(id=ids.new("group"), org_id=w.org_b.id, name="GB")
    w.pool = NodePool(id=ids.new("pool"), cluster_id=w.cluster.id, name="P", kind="dedicated")
    w.other = NodePool(id=ids.new("pool"), cluster_id=w.cluster.id, name="Q", kind="dedicated")
    w.grant_a = NodePoolGrant(
        id=ids.new("pool_grant"), pool_id=w.pool.id, scope="org", scope_id=w.org_a.id
    )
    w.node = GpuNode(
        id=ids.new("node"), cluster_id=w.cluster.id, hostname="n1", status="ready",
        pool_id=w.pool.id,
    )
    w.free_node = GpuNode(id=ids.new("node"), cluster_id=w.cluster.id, hostname="n2", status="ready")
    w.dev = GpuDevice(
        id="dev-1", node_id=w.node.id, cluster_id=w.cluster.id, model="A100", gpu_uuid="GPU-1",
        total_mem_mb=16000, status="ready", mode="fractional",
    )
    async with db.begin():
        db.add_all([
            w.cluster, w.cluster2, w.org_a, w.org_b, w.g1, w.gb, w.pool, w.other, w.grant_a,
            w.node, w.free_node, w.dev,
        ])
    w.su = Principal(user_id="su", global_role="super_admin", global_roles={"super_admin"})
    w.admin_a = Principal(
        user_id="adm-a", memberships={w.g1.id: "org_admin"}, org_admin_orgs={w.org_a.id}
    )
    w.admin_b = Principal(
        user_id="adm-b", memberships={w.gb.id: "org_admin"}, org_admin_orgs={w.org_b.id}
    )
    w.gadmin = Principal(user_id="ga", memberships={w.g1.id: "group_admin"})
    return w


async def _audit(db, action: str) -> list[AuditLog]:
    return list((await db.scalars(select(AuditLog).where(AuditLog.action == action))).all())


# ── RBAC matrix ──
async def test_pool_actions_in_rbac_table():
    su = Principal(user_id="s", global_roles={"super_admin"})
    org_admin = Principal(user_id="o", memberships={"g": "org_admin"}, org_admin_orgs={"o1"})
    group_admin = Principal(user_id="g", memberships={"g": "group_admin"})
    member = Principal(user_id="m", memberships={"g": "member"})
    for action in ("pool.read", "pool.manage", "pool.grant"):
        assert rbac_allows(su, action)
        assert not rbac_allows(group_admin, action)
        assert not rbac_allows(member, action)
    assert rbac_allows(org_admin, "pool.read")
    assert rbac_allows(org_admin, "pool.grant")
    assert not rbac_allows(org_admin, "pool.manage")


# ── visibility helper ──
async def test_visible_pool_ids_scopes_org_admin_to_granted_pools():
    org_a, org_b = "orgA", "orgB"
    grants = [
        NodePoolGrant(id="g1", pool_id="P", scope="org", scope_id=org_a),
        NodePoolGrant(id="g2", pool_id="Q", scope="group", scope_id="grpB"),
        NodePoolGrant(id="g3", pool_id="R", scope="group", scope_id="grpA"),
    ]
    group_orgs = {"grpA": org_a, "grpB": org_b}
    admin_a = Principal(user_id="a", org_admin_orgs={org_a})
    assert visible_pool_ids(admin_a, grants, group_orgs) == {"P", "R"}
    assert visible_pool_ids(Principal(user_id="s", global_roles={"super_admin"}), grants, group_orgs) is None
    assert visible_pool_ids(Principal(user_id="n"), grants, group_orgs) == set()


# ── pools CRUD ──
async def test_pool_crud_super_admin(db):
    w = await _world(db)
    created = await create_node_pool(
        PoolCreate(cluster_id=w.cluster.id, name="R", kind="shared", description="lab R"),
        principal=w.su, db=db,
    )
    assert created["kind"] == "shared" and created["cluster_name"] == "c1"
    assert created["node_count"] == 0 and created["grants"] == []
    audits = await _audit(db, "pool.create")
    assert audits[-1].detail["changes"]["name"] == {"from": None, "to": "R"}

    # duplicate name in the same cluster → typed conflict
    with pytest.raises(DomainError) as ei:
        await create_node_pool(PoolCreate(cluster_id=w.cluster.id, name="R"), principal=w.su, db=db)
    assert ei.value.code == "conflict" and ei.value.http == 409
    # unknown cluster
    with pytest.raises(NotFound):
        await create_node_pool(PoolCreate(cluster_id="nope", name="Z"), principal=w.su, db=db)

    updated = await update_node_pool(
        created["id"], PoolUpdate(name="R2", kind="dedicated"), principal=w.su, db=db
    )
    assert updated["name"] == "R2" and updated["kind"] == "dedicated"
    ch = (await _audit(db, "pool.update"))[-1].detail["changes"]
    assert ch == {"name": {"from": "R", "to": "R2"}, "kind": {"from": "shared", "to": "dedicated"}}

    listed = await list_node_pools(cluster_id=w.cluster.id, principal=w.su, db=db)
    names = {p["name"]: p for p in listed["data"]}
    assert set(names) == {"P", "Q", "R2"} and listed["total"] == 3
    assert names["P"]["node_count"] == 1
    assert names["P"]["nodes"][0]["hostname"] == "n1" and names["P"]["nodes"][0]["device_count"] == 1
    assert names["P"]["grants"][0]["scope"] == "org" and names["P"]["grants"][0]["name"] == "org A"

    # org_admin / group_admin cannot manage pools
    with pytest.raises(Forbidden):
        await create_node_pool(PoolCreate(cluster_id=w.cluster.id, name="X"), principal=w.admin_a, db=db)
    with pytest.raises(Forbidden):
        await update_node_pool(w.pool.id, PoolUpdate(name="X"), principal=w.admin_a, db=db)
    with pytest.raises(Forbidden):
        await list_node_pools(cluster_id=None, principal=w.gadmin, db=db)


async def test_list_pools_org_admin_sees_only_granted(db):
    w = await _world(db)
    out = await list_node_pools(cluster_id=None, principal=w.admin_a, db=db)
    assert [p["name"] for p in out["data"]] == ["P"]
    out_b = await list_node_pools(cluster_id=None, principal=w.admin_b, db=db)
    assert out_b["data"] == [] and out_b["total"] == 0


# ── grants: sub-assignment rule ──
async def test_grant_sub_assignment_rule(db):
    w = await _world(db)
    # org_admin of A grants P to a group in A
    g = await grant_node_pool(
        w.pool.id, PoolGrantCreate(scope="group", scope_id=w.g1.id), principal=w.admin_a, db=db
    )
    assert g["scope"] == "group" and g["scope_id"] == w.g1.id and g["name"] == "G1"
    ch = (await _audit(db, "pool.grant"))[-1].detail["changes"]
    assert ch["scope_id"] == {"from": None, "to": w.g1.id}
    # duplicate → conflict
    with pytest.raises(DomainError) as ei:
        await grant_node_pool(
            w.pool.id, PoolGrantCreate(scope="group", scope_id=w.g1.id), principal=w.su, db=db
        )
    assert ei.value.code == "conflict"
    # group in org B → Forbidden
    with pytest.raises(Forbidden):
        await grant_node_pool(
            w.pool.id, PoolGrantCreate(scope="group", scope_id=w.gb.id), principal=w.admin_a, db=db
        )
    # pool without an org-A grant → Forbidden
    with pytest.raises(Forbidden):
        await grant_node_pool(
            w.other.id, PoolGrantCreate(scope="group", scope_id=w.g1.id), principal=w.admin_a, db=db
        )
    # org-scope grants are super_admin only
    with pytest.raises(Forbidden):
        await grant_node_pool(
            w.pool.id, PoolGrantCreate(scope="org", scope_id=w.org_a.id), principal=w.admin_a, db=db
        )
    # org_admin of B holds no grant on P at all
    with pytest.raises(Forbidden):
        await grant_node_pool(
            w.pool.id, PoolGrantCreate(scope="group", scope_id=w.gb.id), principal=w.admin_b, db=db
        )
    # a non-existent group yields Forbidden for org_admin (no existence leak) …
    with pytest.raises(Forbidden):
        await grant_node_pool(
            w.pool.id, PoolGrantCreate(scope="group", scope_id="grp_missing"), principal=w.admin_a, db=db
        )
    # … and NotFound for super_admin
    with pytest.raises(NotFound):
        await grant_node_pool(
            w.pool.id, PoolGrantCreate(scope="group", scope_id="grp_missing"), principal=w.su, db=db
        )
    with pytest.raises(NotFound):
        await grant_node_pool(
            w.other.id, PoolGrantCreate(scope="org", scope_id="org_missing"), principal=w.su, db=db
        )
    # super_admin may grant anything that exists
    g2 = await grant_node_pool(
        w.other.id, PoolGrantCreate(scope="org", scope_id=w.org_b.id), principal=w.su, db=db
    )
    assert g2["name"] == "org B"


async def test_revoke_follows_the_same_rule(db):
    w = await _world(db)
    g = await grant_node_pool(
        w.pool.id, PoolGrantCreate(scope="group", scope_id=w.g1.id), principal=w.su, db=db
    )
    # org_admin of A may not remove the org grant itself
    with pytest.raises(Forbidden):
        await revoke_node_pool_grant(w.pool.id, w.grant_a.id, principal=w.admin_a, db=db)
    # org_admin of B may not touch grants on P
    with pytest.raises(Forbidden):
        await revoke_node_pool_grant(w.pool.id, g["id"], principal=w.admin_b, db=db)
    # grant id must belong to the pool
    with pytest.raises(NotFound):
        await revoke_node_pool_grant(w.other.id, g["id"], principal=w.su, db=db)
    # org_admin of A removes its own group grant
    await revoke_node_pool_grant(w.pool.id, g["id"], principal=w.admin_a, db=db)
    assert await db.get(NodePoolGrant, g["id"]) is None
    ch = (await _audit(db, "pool.revoke"))[-1].detail["changes"]
    assert ch["scope_id"] == {"from": w.g1.id, "to": None}


# ── node → pool ──
async def test_set_node_pool_and_node_reads(db):
    w = await _world(db)
    # cross-cluster pool → validation error
    foreign = NodePool(id=ids.new("pool"), cluster_id=w.cluster2.id, name="F", kind="dedicated")
    db.add(foreign)
    await db.commit()
    with pytest.raises(_Validation):
        await set_node_pool(w.free_node.id, NodePoolSet(pool_id=foreign.id), principal=w.su, db=db)
    with pytest.raises(NotFound):
        await set_node_pool(w.free_node.id, NodePoolSet(pool_id="npl_missing"), principal=w.su, db=db)
    with pytest.raises(Forbidden):
        await set_node_pool(w.free_node.id, NodePoolSet(pool_id=w.pool.id), principal=w.admin_a, db=db)

    out = await set_node_pool(w.free_node.id, NodePoolSet(pool_id=w.pool.id), principal=w.su, db=db)
    assert out["pool_id"] == w.pool.id and out["pool_name"] == "P"
    ch = (await _audit(db, "node.set_pool"))[-1].detail["changes"]
    assert ch == {"pool_id": {"from": None, "to": w.pool.id}, "pool_name": {"from": None, "to": "P"}}

    rows = {n["hostname"]: n for n in (await list_nodes(status=None, region=None, principal=w.su, db=db))["data"]}
    assert rows["n1"]["pool_name"] == "P" and rows["n2"]["pool_id"] == w.pool.id
    one = await get_node(w.node.id, principal=w.su, db=db)
    assert one["pool_id"] == w.pool.id and one["pool_name"] == "P"

    # back to shared
    out = await set_node_pool(w.free_node.id, NodePoolSet(pool_id=None), principal=w.su, db=db)
    assert out["pool_id"] is None and out["pool_name"] is None
    one = await get_node(w.free_node.id, principal=w.su, db=db)
    assert one["pool_id"] is None and one["pool_name"] is None


# ── delete ──
async def test_delete_pool_refused_under_live_sessions_then_clears_nodes(db):
    w = await _world(db)
    offering = Offering(id=ids.new("offering"), name="o", resource_class="gpu", gpu_model="A100")
    image = Image(id=ids.new("image"), name="img")
    sess = SessionRow(
        id=ids.new("session"), owner_user_id="u", group_id=w.g1.id, cluster_id=w.cluster.id,
        offering_id=offering.id, image_id=image.id, resource_class="gpu", mode="fractional",
        gpu_mem_mb=8000, gpu_cores=50, status="running",
    )
    alloc = Allocation(
        id=ids.new("allocation"), session_id=sess.id, device_id=w.dev.id, gpu_uuid="GPU-1",
        gpu_mem_mb=8000, gpu_cores=50, status="bound",
    )
    db.add_all([offering, image, sess, alloc])
    await db.commit()

    with pytest.raises(_PoolInUse) as ei:
        await delete_node_pool(w.pool.id, principal=w.su, db=db)
    assert ei.value.code == "pool_in_use" and ei.value.details["sessions"] == [sess.id]
    with pytest.raises(Forbidden):
        await delete_node_pool(w.pool.id, principal=w.admin_a, db=db)

    sess.status = "terminated"
    db.add(sess)
    await db.commit()
    await delete_node_pool(w.pool.id, principal=w.su, db=db)
    assert await db.get(NodePool, w.pool.id) is None
    assert await db.get(NodePoolGrant, w.grant_a.id) is None
    assert (await db.scalar(select(GpuNode.pool_id).where(GpuNode.id == w.node.id))) is None
    ch = (await _audit(db, "pool.delete"))[-1].detail["changes"]
    assert ch["nodes"]["from"] == [w.node.id] and ch["name"] == {"from": "P", "to": None}
    with pytest.raises(NotFound):
        await delete_node_pool(w.pool.id, principal=w.su, db=db)
