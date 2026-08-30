"""Host CPU/RAM admission: a card only counts as a candidate when its node still has host
headroom for the session's compute request — otherwise the session queues instead of the pod
sitting Pending on the k8s scheduler; a request no node can EVER hold is refused up front."""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.schemas.session import SessionCreate
from app.auth.rbac import Principal
from app.core import ids
from app.db.models import Allocation, CreditWallet, GpuDevice, GpuNode, Image, Offering, Project
from app.domain.scheduler import SchedulerService

MODEL = "NVIDIA RTX PRO 5000 Blackwell"


async def _user(db):
    """A user with their own personal wallet (billing must target the requester's wallet)."""
    user_id = ids.new("user")
    wallet = CreditWallet(
        id=ids.new("wallet"), owner_type="user", owner_id=user_id,
        balance=Decimal("1000"), reserved=Decimal("0"),
    )
    async with db.begin():
        db.add(wallet)
    return user_id, wallet


async def _fleet(db, *nodes):
    """Build a cluster of (mem_gb, cpu) nodes, one fractional 48G card each. Returns the ids."""
    org_id = ids.new("org")
    group = Project(id=ids.new("group"), org_id=org_id, name="p")
    user_id = ids.new("user")
    wallet = CreditWallet(
        id=ids.new("wallet"), owner_type="user", owner_id=user_id,
        balance=Decimal("1000"), reserved=Decimal("0"),
    )
    cluster_id = ids.new("cluster")
    offering = Offering(
        id=ids.new("offering"), name="PRO 5000", resource_class="gpu",
        gpu_model=MODEL, gpu_mem_mb=49152, gpu_cores=100, credit_per_hour=Decimal("100"),
        cpu=4, mem_gb=8, disk_gb=50,
    )
    image = Image(id=ids.new("image"), name="pytorch")
    rows = [group, wallet, offering, image]
    devs = []
    for i, (mem, cpu) in enumerate(nodes):
        node = GpuNode(id=ids.new("node"), cluster_id=cluster_id, hostname=f"n{i}",
                       status="ready", cpu=cpu, mem=mem)
        dev = GpuDevice(
            id=f"dev-{i}", node_id=node.id, cluster_id=cluster_id, model=MODEL,
            gpu_uuid=f"GPU-{i}", total_mem_mb=49152, status="ready", mode="fractional",
        )
        rows += [node, dev]
        devs.append(dev.id)
    async with db.begin():
        db.add_all(rows)
    return cluster_id, offering, image, group, wallet, user_id, devs


def _req(cluster_id, offering, image, group, wallet, *, mem_gb, cpu=2, disk_gb=20):
    return SessionCreate(
        offering_id=offering.id, image_id=image.id, resource_class="gpu",
        cluster_id=cluster_id, group_id=group.id, mode="fractional",
        gpu_mem_mb=6144, gpu_cores=13, cpu=cpu, mem_gb=mem_gb, disk_gb=disk_gb,
        billing_wallet_id=wallet.id,
    )


@pytest.mark.asyncio
async def test_queues_when_node_ram_is_full(db, fake_handoff):
    """VRAM fits but host RAM does not: the session queues instead of going Pending forever."""
    cluster_id, offering, image, group, wallet, user_id, _ = await _fleet(db, (31, 32))
    svc = SchedulerService(db)
    svc.handoff = fake_handoff

    # First session takes 16 of the node's 31 GiB (2 GiB is held back as the system reserve).
    out1 = await svc.create_session(
        _req(cluster_id, offering, image, group, wallet, mem_gb=16),
        Principal(user_id=user_id), idem="hh-1")
    assert (await db.scalars(select(Allocation).where(Allocation.session_id == out1.id))).first() is not None
    await db.commit()

    # 31 - 2(reserve) - 16 = 13 < 16: no host RAM for a second 16 GiB session -> queued,
    # holding no reservation.
    user2, wallet2 = await _user(db)
    out2 = await svc.create_session(
        _req(cluster_id, offering, image, group, wallet2, mem_gb=16),
        Principal(user_id=user2), idem="hh-2")
    assert out2.status == "pending"
    assert (await db.scalars(select(Allocation).where(Allocation.session_id == out2.id))).first() is None
    await db.commit()

    # A modest 4 GiB session still fits alongside.
    user3, wallet3 = await _user(db)
    out3 = await svc.create_session(
        _req(cluster_id, offering, image, group, wallet3, mem_gb=4),
        Principal(user_id=user3), idem="hh-3")
    assert (await db.scalars(select(Allocation).where(Allocation.session_id == out3.id))).first() is not None


@pytest.mark.asyncio
async def test_placement_moves_to_the_node_with_headroom(db, fake_handoff):
    """With one node full on RAM, the reservation lands on the other node's card."""
    cluster_id, offering, image, group, wallet, user_id, devs = await _fleet(db, (8, 32), (31, 32))
    svc = SchedulerService(db)
    svc.handoff = fake_handoff

    out = await svc.create_session(
        _req(cluster_id, offering, image, group, wallet, mem_gb=16),
        Principal(user_id=user_id), idem="hh-move")
    alloc = (await db.scalars(select(Allocation).where(Allocation.session_id == out.id))).first()
    assert alloc is not None and alloc.device_id == devs[1]   # the 8 GiB node cannot host 16 GiB


@pytest.mark.asyncio
async def test_oversized_request_is_refused_up_front(db, fake_handoff):
    """More RAM than any node minus the system reserve can EVER hold answers no_capacity
    (node_too_small) up front instead of queueing forever. 30 GiB clears the raw 31 GiB
    capacity but not capacity-minus-reserve."""
    from app.core.errors import NoCapacity

    cluster_id, offering, image, group, wallet, user_id, _ = await _fleet(db, (31, 32))
    svc = SchedulerService(db)
    svc.handoff = fake_handoff

    with pytest.raises(NoCapacity) as exc:
        await svc.create_session(
            _req(cluster_id, offering, image, group, wallet, mem_gb=30),
            Principal(user_id=user_id), idem="hh-big")
    assert exc.value.details.get("reason") == "node_too_small"
