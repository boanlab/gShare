"""Mixed-fleet placement: a session priced for one GPU model never lands on another model.

The fleet plan runs RTX 4090, RTX PRO 5000, and RTX PRO 6000 side by side; the reservation must
honour the offering's gpu_model even when a bigger card of another model has free capacity.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.schemas.session import SessionCreate
from app.auth.rbac import Principal
from app.core import ids
from app.db.models import Allocation, CreditWallet, GpuDevice, Image, Offering, Project
from app.domain.scheduler import SchedulerService


@pytest.mark.asyncio
async def test_reservation_respects_offering_model(db, fake_handoff):
    org_id = ids.new("org")
    group = Project(id=ids.new("group"), org_id=org_id, name="p")
    user_id = ids.new("user")
    wallet = CreditWallet(
        id=ids.new("wallet"), owner_type="user", owner_id=user_id,
        balance=Decimal("1000"), reserved=Decimal("0"),
    )
    cluster_id = ids.new("cluster")
    off_4090 = Offering(
        id=ids.new("offering"), name="RTX 4090", resource_class="gpu",
        gpu_model="NVIDIA GeForce RTX 4090", gpu_mem_mb=24564, gpu_cores=100,
        credit_per_hour=Decimal("100"),
    )
    image = Image(id=ids.new("image"), name="pytorch")
    dev_4090 = GpuDevice(
        id="GPU-4090", node_id=ids.new("node"), cluster_id=cluster_id,
        model="NVIDIA GeForce RTX 4090", gpu_uuid="GPU-4090",
        total_mem_mb=24564, status="ready", mode="fractional",
    )
    # A bigger, completely FREE card of another model — must never serve the 4090 offering.
    dev_pro = GpuDevice(
        id="GPU-pro6000", node_id=ids.new("node"), cluster_id=cluster_id,
        model="NVIDIA RTX PRO 6000 Blackwell", gpu_uuid="GPU-pro6000",
        total_mem_mb=98304, status="ready", mode="fractional",
    )
    async with db.begin():
        db.add_all([group, wallet, off_4090, image, dev_4090, dev_pro])

    svc = SchedulerService(db)
    svc.handoff = fake_handoff

    def req(mem, cores, idem):
        return SessionCreate(
            offering_id=off_4090.id, image_id=image.id, resource_class="gpu",
            cluster_id=cluster_id, group_id=group.id, mode="fractional",
            gpu_mem_mb=mem, gpu_cores=cores, billing_wallet_id=wallet.id,
        ), idem

    # 1. Placement goes to the 4090.
    r1, idem1 = req(12282, 50, "mix-1")
    out1 = await svc.create_session(r1, Principal(user_id=user_id), idem=idem1)
    alloc = (await db.scalars(select(Allocation).where(Allocation.session_id == out1.id))).first()
    assert alloc is not None and alloc.gpu_uuid == "GPU-4090"
    await db.commit()   # close the autobegun read tx before the next create_session

    # 2. The 4090 is now too full for a second half-card; the PRO card stays untouched and the
    #    session QUEUES instead of landing on the wrong model.
    r2, idem2 = req(24564, 100, "mix-2")
    out2 = await svc.create_session(r2, Principal(user_id=user_id), idem=idem2)
    assert out2.status == "pending"
    alloc2 = (await db.scalars(select(Allocation).where(Allocation.session_id == out2.id))).first()
    assert alloc2 is None
    db.expunge_all()
    pro = await db.get(GpuDevice, "GPU-pro6000")
    assert pro.used_mem_mb == 0 and pro.used_cores == 0
