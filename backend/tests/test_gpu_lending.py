"""In-place GPU yield lending accounting.

Asserts the borrow ledger primitives against the in-memory SQLite ``db`` fixture:
- a preemptible borrower is placed on a yielded card without double-counting device.used_*,
- the drift reconcile excludes borrow allocations,
- releasing a borrow returns the card to the lendable pool,
- borrow placement is refused on a non-yielded (active) card.
(docs/paper/manuscript, §Design)
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.schemas.session import SessionCreate
from app.auth.rbac import Principal
from app.core import ids
from app.core.config import settings
from app.db.models import Allocation, GpuDevice, Image, Offering, Session
from app.domain.scheduler import SchedulerService
from app.domain.session_service import SessionService

CLUSTER = "clu_lend"


async def _setup_yielded_card(db, *, lend_state: str = "yielded"):
    """An exclusive card fully held by a yielded owner (used_*=full), plus a preemptible
    borrower."""
    dev = GpuDevice(
        id=ids.new("device"), node_id=ids.new("node"), cluster_id=CLUSTER,
        model="RTX4090", gpu_uuid=ids.new("dev"), total_mem_mb=24000, total_cores=100,
        used_mem_mb=24000, used_cores=100, status="ready", mode="exclusive",
        lend_state=lend_state,
    )
    owner = Session(
        id=ids.new("session"), owner_user_id="usr_owner", cluster_id=CLUSTER,
        cluster_mode="single", resource_class="gpu", mode="exclusive",
        offering_id="off_x", image_id="img_x", pause_mode="yield", status="paused",
    )
    owner_alloc = Allocation(
        id=ids.new("allocation"), session_id=owner.id, device_id=dev.id, gpu_uuid=dev.gpu_uuid,
        gpu_mem_mb=24000, gpu_cores=100, status="reserved", kind="resident",
    )
    borrower = Session(
        id=ids.new("session"), owner_user_id="usr_borrow", cluster_id=CLUSTER,
        cluster_mode="single", resource_class="gpu", mode="exclusive",
        offering_id="off_x", image_id="img_x", preemptible=True, status="pending",
    )
    async with db.begin():
        db.add_all([dev, owner, owner_alloc, borrower])
    return dev, owner, borrower


def _req() -> SessionCreate:
    return SessionCreate(
        offering_id="off_x", image_id="img_x", resource_class="gpu",
        cluster_id=CLUSTER, mode="exclusive",
    )


@pytest.mark.asyncio
async def test_borrow_placed_on_yielded_card_without_double_count(db):
    dev, owner, borrower = await _setup_yielded_card(db)
    sched = SchedulerService(db)
    async with db.begin():
        placed = await sched.reserve_spot_slice(borrower, _req())
    assert placed is True

    fresh = await db.get(GpuDevice, dev.id)
    assert fresh.lend_state == "lent"
    # The physical card is free, so used_* must not be double counted: the owner's 24000 stands.
    assert fresh.used_mem_mb == 24000 and fresh.used_cores == 100

    borrow = (
        await db.scalars(
            select(Allocation).where(
                Allocation.session_id == borrower.id, Allocation.ended_at.is_(None)
            )
        )
    ).first()
    assert borrow is not None and borrow.kind == "spot"


@pytest.mark.asyncio
async def test_reconcile_excludes_borrow(db):
    dev, owner, borrower = await _setup_yielded_card(db)
    sched = SchedulerService(db)
    async with db.begin():
        await sched.reserve_spot_slice(borrower, _req())
    # Reconciliation excludes borrows, so used_* reflects only the owner's 24000, not 48000.
    async with db.begin():
        d = await db.get(GpuDevice, dev.id, with_for_update=True)
        await sched._reconcile_device_usage([d])
        assert d.used_mem_mb == 24000 and d.used_cores == 100


@pytest.mark.asyncio
async def test_borrow_release_returns_card_to_lendable(db):
    dev, owner, borrower = await _setup_yielded_card(db)
    sched = SchedulerService(db)
    async with db.begin():
        await sched.reserve_spot_slice(borrower, _req())
    # Releasing the borrow returns the card to 'yielded' — the owner is still yielding — and leaves
    # used_* untouched.
    async with db.begin():
        await SessionService(db)._release_allocation(borrower.id, datetime.now(UTC))
    fresh = await db.get(GpuDevice, dev.id)
    assert fresh.lend_state == "yielded"
    assert fresh.used_mem_mb == 24000 and fresh.used_cores == 100


@pytest.mark.asyncio
async def test_no_borrow_on_active_card(db):
    dev, owner, borrower = await _setup_yielded_card(db, lend_state="")
    sched = SchedulerService(db)
    async with db.begin():
        placed = await sched.reserve_spot_slice(borrower, _req())
    assert placed is False
    fresh = await db.get(GpuDevice, dev.id)
    assert fresh.lend_state == ""


def _frac_borrower(mem, cores):
    return Session(
        id=ids.new("session"), owner_user_id="usr_fb", cluster_id=CLUSTER,
        cluster_mode="single", resource_class="gpu", mode="fractional",
        offering_id="off_x", image_id="img_x", preemptible=True,
        gpu_mem_mb=mem, gpu_cores=cores, status="pending",
    )


def _req_frac(mem, cores):
    return SessionCreate(
        offering_id="off_x", image_id="img_x", resource_class="gpu", cluster_id=CLUSTER,
        mode="fractional", gpu_mem_mb=mem, gpu_cores=cores, preemptible=True,
    )


@pytest.mark.asyncio
async def test_fractional_multi_borrow_packs_yielded_card(db):
    """Several fractional borrowers pack into one yielded card's remaining borrow capacity; HAMi
    does the actual placement."""
    dev, owner, _b = await _setup_yielded_card(db)  # 24000 mem / 100 cores, yielded
    sched = SchedulerService(db)
    dev_id = dev.id

    async def place(mem, cores):
        async with db.begin():
            b = _frac_borrower(mem, cores)
            db.add(b)
        async with db.begin():
            return await sched.reserve_spot_slice(b, _req_frac(mem, cores))

    assert await place(8000, 30) is True     # 8000/24000
    assert await place(8000, 30) is True      # 16000/24000
    assert await place(12000, 30) is False    # 8000 left, 12000 requested: rejected
    assert await place(6000, 30) is True       # 8000 left, 6000 requested: allowed

    fresh = await db.get(GpuDevice, dev_id)
    assert fresh.lend_state == "lent"
    # Owner occupancy is ignored because the card is physically free: a borrow never raises used_*.
    assert fresh.used_mem_mb == 24000 and fresh.used_cores == 100
    borrows = (await db.scalars(select(Allocation).where(
        Allocation.device_id == dev_id, Allocation.kind == "spot",
        Allocation.ended_at.is_(None)))).all()
    assert len(borrows) == 3 and sum(b.gpu_mem_mb for b in borrows) == 22000


@pytest.mark.asyncio
async def test_reclaim_defers_to_higher_priority_borrower(db):
    """Priority-aware reclaim: a higher-priority borrower is not preempted, so reclaim returns
    False."""
    dev, owner, borrower = await _setup_yielded_card(db)  # the owner keeps the default priority of 0
    async with db.begin():
        b = await db.get(Session, borrower.id)
        b.priority = 5  # the borrower outranks the owner, the case that must not be preempted back
    sched = SchedulerService(db)
    async with db.begin():
        await sched.reserve_spot_slice(borrower, _req())  # lend_state=lent

    reclaimed = await SessionService(db)._reclaim_spot(owner.id)
    assert reclaimed is False                           # a higher-ranked borrower defers the reclaim

    fresh = await db.get(GpuDevice, dev.id)
    assert fresh.lend_state == "lent"                   # nothing preempted; the loan stands
    b = (await db.scalars(select(Allocation).where(
        Allocation.session_id == borrower.id, Allocation.ended_at.is_(None)))).first()
    assert b is not None                                # the borrower survives


@pytest.mark.asyncio
async def test_demote_releases_card_promotes_borrower_and_goes_cold(db):
    """Demotion after the yield reservation expires: the owner's allocation is released, the
    borrower is promoted from borrow to resident, lend_state clears, and pause_mode becomes
    cold."""
    dev, owner, borrower = await _setup_yielded_card(db)
    dev_id, owner_id, borrower_id = dev.id, owner.id, borrower.id  # demote's rollback expires the objects, so capture the ids first
    sched = SchedulerService(db)
    async with db.begin():
        await sched.reserve_spot_slice(borrower, _req())   # lend_state=lent, borrow alloc

    svc = SessionService(db)

    captured = {}

    async def _capture(sess, paused, *, graceful_demote=None):  # the handoff patches a custom resource and needs a cluster, so it is mocked
        captured["graceful"] = graceful_demote

    svc.handoff.set_paused = _capture
    await svc.demote(owner_id)

    fresh = await db.get(GpuDevice, dev_id)
    assert fresh.lend_state == ""
    assert fresh.used_mem_mb == 24000              # the promoted borrower now occupies the card normally (_setup's total_mem_mb)
    assert captured["graceful"] is False           # the card is lent, so the demotion is plain cold: no restore while a borrower uses it

    b = (await db.scalars(select(Allocation).where(
        Allocation.session_id == borrower_id, Allocation.ended_at.is_(None)))).first()
    assert b is not None and b.kind == "resident"     # promoted from borrow to resident

    o = (await db.scalars(select(Allocation).where(
        Allocation.session_id == owner_id, Allocation.ended_at.is_(None)))).first()
    assert o is None                                # the owner's allocation is released

    osess = await db.get(Session, owner_id)
    assert osess.pause_mode == "cold"               # the lossless priority claim is gone


@pytest.mark.asyncio
async def test_demote_not_lent_is_graceful(db):
    """Demoting a yielded but unlent card: it is empty, so a restore is possible and the demotion is
    graceful — the operator toggles VRAM back and lets the job write a fresh checkpoint."""
    dev, owner, _borrower = await _setup_yielded_card(db)
    dev_id, owner_id = dev.id, owner.id             # captured first, since demote's rollback expires the objects
    svc = SessionService(db)

    captured = {}

    async def _capture(sess, paused, *, graceful_demote=None):
        captured["graceful"] = graceful_demote

    svc.handoff.set_paused = _capture
    await svc.demote(owner_id)                      # no borrow allocation, so the card is not lent

    assert captured["graceful"] is True            # empty card, so the demotion is graceful
    fresh = await db.get(GpuDevice, dev_id)
    assert fresh.lend_state == "" and fresh.used_mem_mb == 0   # the card is empty; there was no borrower to promote
    osess = await db.get(Session, owner_id)
    assert osess.pause_mode == "cold"


@pytest.mark.asyncio
async def test_preemptible_session_priced_at_spot_discount(db):
    """A preemptible exclusive session snapshots the normal rate multiplied by SPOT_DISCOUNT."""
    cluster_id = ids.new("cluster")
    offering = Offering(
        id=ids.new("offering"), name="excl", resource_class="gpu",
        gpu_model="A100", gpu_mem_mb=81920, gpu_cores=100, credit_per_hour=Decimal("400"),
    )
    image = Image(id=ids.new("image"), name="pytorch")
    async with db.begin():
        db.add_all([offering, image])
    svc = SchedulerService(db)
    base = dict(offering_id=offering.id, image_id=image.id, resource_class="gpu",
                cluster_id=cluster_id, mode="exclusive")

    spot = await svc._persist_pending(
        SessionCreate(**base, preemptible=True), Principal(user_id=ids.new("user")))
    normal = await svc._persist_pending(
        SessionCreate(**base, preemptible=False), Principal(user_id=ids.new("user")))

    assert normal.credit_per_hour_snapshot == Decimal("400")
    assert normal.preemptible is False
    expected = (Decimal("400") * Decimal(str(settings.SPOT_DISCOUNT))).quantize(Decimal("0.01"))
    assert spot.credit_per_hour_snapshot == expected   # 400 * 0.3 = 120.00
    assert spot.preemptible is True


def test_ram_pressure_demotes_lowest_priority_oldest_first():
    """SP1 (host-RAM pressure): per-node yielded VRAM exceeding the budget demotes the lowest-
    priority, then oldest, yields until committed ≤ budget.
    (grace_enforcer.select_ram_pressure_victims)"""
    from app.workers.grace_enforcer import select_ram_pressure_victims
    # 3 yields × 6000 MiB = 18000 committed, on a node.
    items = [
        ("hi",     10, 100.0, 6000),  # high priority — protected
        ("lo_new",  1, 200.0, 6000),  # low priority, newer
        ("lo_old",  1,  50.0, 6000),  # low priority, oldest → first victim
    ]
    # budget 12000 → free 6000 → demote exactly one: lowest-prio & oldest.
    assert select_ram_pressure_victims(items, 12000) == ["lo_old"]
    # budget 6000 → free 12000 → demote both low-prio (oldest first), keep high-prio.
    assert select_ram_pressure_victims(items, 6000) == ["lo_old", "lo_new"]
    # no pressure (budget ≥ committed) → no demotion.
    assert select_ram_pressure_victims(items, 18000) == []
    # budget 0 (feature disabled) → no demotion.
    assert select_ram_pressure_victims(items, 0) == []
