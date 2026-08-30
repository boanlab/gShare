"""Per-field policy merge + per-user cap scoping tests."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.api.schemas.session import SessionCreate
from app.auth.rbac import Principal
from app.core import ids
from app.core.errors import QuotaExceeded
from app.db.models import (
    CreditWallet,
    GpuDevice,
    Image,
    Offering,
    Organization,
    Project,
    ResourcePolicy,
)
from app.domain.policy import resolve_effective_policy
from app.domain.scheduler import SchedulerService


@pytest.mark.asyncio
async def test_per_field_merge_user_overrides_only_what_it_sets(db):
    """A user policy that only lowers max_concurrent keeps the group's runtime/idle limits
    (the old first-row-wins resolution silently dropped them)."""
    org = Organization(id=ids.new("org"), name="o")
    group = Project(id=ids.new("group"), org_id=org.id, name="g")
    user_id = ids.new("user")
    async with db.begin():
        db.add_all([
            org, group,
            ResourcePolicy(
                id=ids.new("policy"), scope="user", scope_id=user_id,
                max_concurrent=1, limits={},
            ),
            ResourcePolicy(
                id=ids.new("policy"), scope="group", scope_id=group.id,
                max_concurrent=4, max_runtime=600, idle_timeout=900,
                limits={"gpu_mem_mb": 50000},
            ),
            ResourcePolicy(
                id=ids.new("policy"), scope="global", scope_id="*",
                max_concurrent=3, max_queued=5, max_runtime=1440, idle_timeout=3600,
                limits={"gpu_mem_mb": 98304, "cpu": 64},
            ),
        ])

    pol = await resolve_effective_policy(db, user_id, group.id)
    assert pol is not None
    assert pol.max_concurrent == 1          # from user
    assert pol.max_runtime == 600           # from group (user did not set it)
    assert pol.idle_timeout == 900          # from group
    assert pol.max_queued == 5              # from global (nothing more specific)
    assert pol.limits["gpu_mem_mb"] == 50000  # group beats global per key
    assert pol.limits["cpu"] == 64            # global fills the gap
    assert pol.sources["max_concurrent"] == "user"
    assert pol.sources["max_queued"] == "global"


@pytest.mark.asyncio
async def test_resolution_without_any_policy_returns_none(db):
    assert await resolve_effective_policy(db, ids.new("user"), None) is None


@pytest.mark.asyncio
async def test_max_concurrent_is_per_user_not_per_group(db, fake_handoff):
    """Two users in one group each get their own max_concurrent=1 (B3 regression: the count
    used to pivot to the whole group whenever group_id was set)."""
    org_id = ids.new("org")
    group = Project(id=ids.new("group"), org_id=org_id, name="p")
    cluster_id = ids.new("cluster")
    offering = Offering(
        id=ids.new("offering"), name="A100-frac", resource_class="gpu",
        gpu_model="A100", gpu_mem_mb=16000, gpu_cores=100, credit_per_hour=Decimal("60"),
    )
    image = Image(id=ids.new("image"), name="pytorch")
    device = GpuDevice(
        id=ids.new("device"), node_id=ids.new("node"), cluster_id=cluster_id,
        model="A100", gpu_uuid=ids.new("device"), total_mem_mb=16000, status="ready",
        mode="fractional",
    )
    users = [ids.new("user"), ids.new("user")]
    wallets = {
        uid: CreditWallet(
            id=ids.new("wallet"), owner_type="user", owner_id=uid,
            balance=Decimal("1000"), reserved=Decimal("0"),
        )
        for uid in users
    }
    policy = ResourcePolicy(
        id=ids.new("policy"), scope="group", scope_id=group.id, max_concurrent=1, limits={},
    )
    async with db.begin():
        db.add_all([group, offering, image, device, policy, *wallets.values()])

    svc = SchedulerService(db)
    svc.handoff = fake_handoff

    def req(uid):
        return SessionCreate(
            offering_id=offering.id, image_id=image.id, resource_class="gpu",
            cluster_id=cluster_id, group_id=group.id, mode="fractional",
            gpu_mem_mb=4000, gpu_cores=25, billing_wallet_id=wallets[uid].id,
        )

    # First user takes their one allowed session.
    out1 = await svc.create_session(req(users[0]), Principal(user_id=users[0]), idem="u1-s1")
    assert out1.status != "error"
    # The SECOND user must still be allowed their own session.
    out2 = await svc.create_session(req(users[1]), Principal(user_id=users[1]), idem="u2-s1")
    assert out2.status != "error"
    # And the first user's SECOND session trips their per-user cap.
    with pytest.raises(QuotaExceeded):
        await svc.create_session(req(users[0]), Principal(user_id=users[0]), idem="u1-s2")
