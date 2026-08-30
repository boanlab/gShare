"""Quota requests: approval upserts a USER-scope policy carrying only the granted keys, so the
per-field merge keeps everything else inherited; rejection stores the reason.

Deciding is platform-tier only: group admins are out of the quota loop entirely."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.api.policies_router import (
    ResourceRequestCreate,
    _RRReject,
    approve_resource_request,
    create_resource_request,
    list_resource_requests,
    reject_resource_request,
)
from app.auth.rbac import Principal
from app.core import ids
from app.core.errors import Forbidden
from app.db.models import Membership, Organization, Project, ResourcePolicy, User
from app.domain.policy import resolve_effective_policy


def _p(uid, *, super_admin=False, memberships=None):
    return Principal(
        user_id=uid,
        global_role="super_admin" if super_admin else None,
        global_roles=["super_admin"] if super_admin else [],
        memberships=memberships or {},
    )


async def _seed(db):
    org = Organization(id=ids.new("org"), name="O")
    grp = Project(id=ids.new("project"), org_id=org.id, name="G")
    member = User(id=ids.new("user"), email="m@x.kr", name="M")
    admin = User(id=ids.new("user"), email="a@x.kr", name="A")
    db.add_all([
        org, grp, member, admin,
        Membership(id=ids.new("membership"), user_id=member.id, group_id=grp.id, role="member"),
        Membership(id=ids.new("membership"), user_id=admin.id, group_id=grp.id, role="group_admin"),
        # the group default the user-scope grant must NOT fork away from
        ResourcePolicy(id=ids.new("policy"), scope="group", scope_id=grp.id,
                       max_concurrent=3, limits={"cpu": 16, "mem_gb": 64, "storage_gb": 200}),
    ])
    await db.commit()
    return grp, member, admin


@pytest.mark.asyncio
async def test_approve_upserts_user_policy_with_only_granted_keys(db):
    grp, member, admin = await _seed(db)
    r = await create_resource_request(
        ResourceRequestCreate(group_id=grp.id, cpu=32, note="실험 확장"),
        principal=_p(member.id, memberships={grp.id: "member"}), db=db,
    )
    out = await approve_resource_request(r["id"], principal=_p(admin.id, super_admin=True), db=db)
    assert out["status"] == "approved"
    eff = await resolve_effective_policy(db, member.id, grp.id)
    assert eff.limits["cpu"] == 32          # granted key overrides
    assert eff.limits["mem_gb"] == 64       # everything the group granted is carried over
    assert eff.limits["storage_gb"] == 200
    assert eff.max_concurrent == 3

    # The user row must be COMPLETE, not a single-key patch: the policy screen renders rows as-is,
    # and a missing key there reads as "unlimited".
    row = (
        await db.scalars(
            select(ResourcePolicy).where(
                ResourcePolicy.scope == "user", ResourcePolicy.scope_id == member.id
            )
        )
    ).first()
    assert row.limits == {"cpu": 32, "mem_gb": 64, "storage_gb": 200}
    assert row.max_concurrent == 3


@pytest.mark.asyncio
async def test_second_grant_merges_into_existing_user_policy(db):
    grp, member, admin = await _seed(db)
    a = _p(admin.id, super_admin=True)
    m = _p(member.id, memberships={grp.id: "member"})
    r1 = await create_resource_request(ResourceRequestCreate(group_id=grp.id, cpu=32, note="1"), principal=m, db=db)
    await approve_resource_request(r1["id"], principal=a, db=db)
    r2 = await create_resource_request(ResourceRequestCreate(group_id=grp.id, storage_gb=800, note="2"), principal=m, db=db)
    await approve_resource_request(r2["id"], principal=a, db=db)
    eff = await resolve_effective_policy(db, member.id, grp.id)
    assert eff.limits["cpu"] == 32 and eff.limits["storage_gb"] == 800
    assert eff.limits["mem_gb"] == 64      # a later grant does not drop earlier inherited values

    # Deleting the user row falls back to the shared policy — the grant is reversible.
    row = (
        await db.scalars(
            select(ResourcePolicy).where(
                ResourcePolicy.scope == "user", ResourcePolicy.scope_id == member.id
            )
        )
    ).first()
    await db.delete(row)
    await db.commit()
    back = await resolve_effective_policy(db, member.id, grp.id)
    assert back.limits == {"cpu": 16, "mem_gb": 64, "storage_gb": 200}
    assert back.sources["limits.cpu"] == "group"


@pytest.mark.asyncio
async def test_reject_stores_reason_and_group_admin_cannot_decide(db):
    grp, member, admin = await _seed(db)
    m = _p(member.id, memberships={grp.id: "member"})
    r = await create_resource_request(ResourceRequestCreate(group_id=grp.id, mem_gb=128, note="x"), principal=m, db=db)
    # Even the requester's own group admin has no say — only the platform tier decides.
    with pytest.raises(Forbidden):
        await approve_resource_request(r["id"], principal=_p(admin.id, memberships={grp.id: "group_admin"}), db=db)
    with pytest.raises(Forbidden):
        await reject_resource_request(r["id"], _RRReject(reason="no"),
                                      principal=_p(admin.id, memberships={grp.id: "group_admin"}), db=db)
    out = await reject_resource_request(
        r["id"], _RRReject(reason="예산 없음"), principal=_p(admin.id, super_admin=True), db=db,
    )
    assert out["status"] == "rejected" and out["decided_reason"] == "예산 없음"
    mine = await list_resource_requests(box="mine", principal=m, db=db)
    assert mine["data"][0]["decided_reason"] == "예산 없음"


@pytest.mark.asyncio
async def test_gpu_targets_grant_lands_in_user_policy(db):
    grp, member, admin = await _seed(db)
    m = _p(member.id, memberships={grp.id: "member"})
    r = await create_resource_request(
        ResourceRequestCreate(group_id=grp.id, gpu_mem_mb=49152, gpu_cores=200, note="대형 모델"),
        principal=m, db=db,
    )
    await approve_resource_request(r["id"], principal=_p(admin.id, super_admin=True), db=db)
    eff = await resolve_effective_policy(db, member.id, grp.id)
    assert eff.limits["gpu_mem_mb"] == 49152 and eff.limits["gpu_cores"] == 200
    assert eff.limits["cpu"] == 16          # untouched keys still inherited from the group

    # A group admin's incoming box is empty by design; the platform tier sees it.
    ga = _p(admin.id, memberships={grp.id: "group_admin"})
    assert (await list_resource_requests(box="incoming", principal=ga, db=db))["data"] == []
    inc = await list_resource_requests(box="incoming", principal=_p(admin.id, super_admin=True), db=db)
    assert any(row["id"] == r["id"] and row["gpu_mem_mb"] == 49152 for row in inc["data"])
