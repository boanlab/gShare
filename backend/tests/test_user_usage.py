"""GET /users/{id}/usage: the admin drawer's live-footprint aggregate.

Self-readable; other plain users are refused; live sessions/GPU slices/volumes/wallet are summed
and terminated or soft-deleted rows stay out of the numbers.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.api.users_router import get_user_usage
from app.auth.rbac import Principal
from app.core import ids
from app.db.models import Allocation, CreditWallet, Session, StorageVolume, User


async def _seed(db):
    uid = ids.new("user")
    other = ids.new("user")
    db.add_all([
        User(id=uid, email="u@t.local", name="U"),
        User(id=other, email="o@t.local", name="O"),
        CreditWallet(id=ids.new("wallet"), owner_type="user", owner_id=uid,
                     balance=Decimal("120"), reserved=Decimal("30")),
    ])
    running = Session(
        id=ids.new("session"), owner_user_id=uid, cluster_id=ids.new("cluster"),
        offering_id=ids.new("offering"), image_id=ids.new("image"), resource_class="gpu",
        mode="fractional", gpu_mem_mb=8000, gpu_cores=50, cpu=4, mem_gb=8, status="running",
    )
    paused = Session(
        id=ids.new("session"), owner_user_id=uid, cluster_id=running.cluster_id,
        offering_id=running.offering_id, image_id=running.image_id, resource_class="gpu",
        mode="fractional", gpu_mem_mb=4000, gpu_cores=25, cpu=2, mem_gb=4, status="paused",
    )
    done = Session(  # terminated: must not count anywhere
        id=ids.new("session"), owner_user_id=uid, cluster_id=running.cluster_id,
        offering_id=running.offering_id, image_id=running.image_id, resource_class="gpu",
        cpu=16, mem_gb=32, status="terminated",
    )
    db.add_all([
        running, paused, done,
        Allocation(id=ids.new("allocation"), session_id=running.id, gpu_uuid="GPU-A",
                   gpu_mem_mb=8000, gpu_cores=50, status="bound"),
        Allocation(id=ids.new("allocation"), session_id=done.id, gpu_uuid="GPU-B",
                   gpu_mem_mb=9999, gpu_cores=99, status="released"),  # released: out
        StorageVolume(id=ids.new("volume"), scope="user", scope_id=uid, type="home",
                      access_mode="RWX", quota_gb=50, used_gb=12),
        StorageVolume(id=ids.new("volume"), scope="user", scope_id=other, type="home",
                      access_mode="RWX", quota_gb=999, used_gb=1),  # someone else's: out
    ])
    await db.commit()
    return uid, other


@pytest.mark.asyncio
async def test_usage_aggregates_only_live_own_resources(db):
    uid, _ = await _seed(db)
    out = await get_user_usage(uid, principal=Principal(user_id=uid), db=db)
    assert out["sessions"] == {"active": 2, "running": 1, "paused": 1, "queued": 0}
    assert out["host"] == {"cpu": 4, "mem_gb": 8}            # paused (cold) holds no host compute
    assert out["gpu"] == {"allocations": 1, "gpu_mem_mb": 8000, "gpu_cores": 50}
    assert out["volumes"] == {"count": 1, "quota_gb": 50, "used_gb": 12}
    assert out["wallet"] == {"balance": 120.0, "reserved": 30.0}


@pytest.mark.asyncio
async def test_usage_is_admin_or_self_only(db):
    uid, other = await _seed(db)
    from app.core.errors import Forbidden
    with pytest.raises(Forbidden):
        await get_user_usage(uid, principal=Principal(user_id=other), db=db)
    root = Principal(user_id=other, global_role="super_admin",
                     global_roles={"super_admin"}, memberships={})
    out = await get_user_usage(uid, principal=root, db=db)
    assert out["sessions"]["active"] == 2
