"""Queue unification tests: one ranking authority over PG (no Redis queue).

Covers the two bugs the unification removed:
- B1: session priority was dropped on enqueue (hardcoded 0), so it never affected queue order.
- B2: two modules scored one Redis ZSET with incompatible schemes and different members, so the
  displayed position and the dequeue order could diverge and PATCH/DELETE corrupted the queue.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.schemas.session import SessionCreate
from app.auth.rbac import Principal
from app.core import ids
from app.db.models import (
    Allocation,
    CreditWallet,
    GpuDevice,
    Image,
    Offering,
    Project,
    QueueEntry,
)
from app.db.models import Session as SessionRow
from app.domain import queue_ranking
from app.domain.scheduler import SchedulerService
from app.workers import queue_ticker


def _entry(session_id: str, *, priority: int = 0, waited_min: float = 0.0) -> QueueEntry:
    return QueueEntry(
        id=ids.new("queue"), session_id=session_id, session_req={}, priority=priority,
        enqueued_at=datetime.now(UTC) - timedelta(minutes=waited_min),
    )


def test_priority_band_dominates_aging():
    """One priority step outranks any wait time; within a band the queue is FIFO."""
    old_low = _entry("s1", priority=0, waited_min=100000)
    fresh_high = _entry("s2", priority=1, waited_min=0)
    assert queue_ranking.score(fresh_high) > queue_ranking.score(old_low)

    older = _entry("s3", priority=0, waited_min=30)
    newer = _entry("s4", priority=0, waited_min=5)
    assert queue_ranking.score(older) > queue_ranking.score(newer)


def test_aging_is_capped():
    a = _entry("s1", waited_min=queue_ranking.AGING_CAP_MIN + 500)
    b = _entry("s2", waited_min=queue_ranking.AGING_CAP_MIN)
    assert queue_ranking.score(a) == pytest.approx(queue_ranking.score(b))


@pytest.mark.asyncio
async def test_rank_orders_desc_with_fifo_tiebreak(db):
    e_low = _entry("s-low", priority=0, waited_min=10)
    e_high = _entry("s-high", priority=5, waited_min=1)
    e_old = _entry("s-old", priority=0, waited_min=60)
    async with db.begin():
        db.add_all([e_low, e_high, e_old])
    ranked = await queue_ranking.rank(db)
    assert [e.session_id for e, _ in ranked] == ["s-high", "s-old", "s-low"]


def _seed(db_objs):
    org_id = ids.new("org")
    group = Project(id=ids.new("group"), org_id=org_id, name="p")
    user_id = ids.new("user")
    wallet = CreditWallet(
        id=ids.new("wallet"), owner_type="user", owner_id=user_id,
        balance=Decimal("1000"), reserved=Decimal("0"),
    )
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
    db_objs.extend([group, wallet, offering, image, device])
    return group, user_id, wallet, cluster_id, offering, image, device


def _occupy_fully(db, dev, offering, image, cluster_id):
    resident = SessionRow(
        id=ids.new("session"), owner_user_id=ids.new("user"), cluster_id=cluster_id,
        offering_id=offering.id, image_id=image.id, resource_class="gpu",
        mode="fractional", status="running", gpu_mem_mb=dev.total_mem_mb, gpu_cores=100,
    )
    alloc = Allocation(
        id=ids.new("allocation"), session_id=resident.id, device_id=dev.id,
        gpu_uuid=dev.gpu_uuid, gpu_mem_mb=dev.total_mem_mb, gpu_cores=100, status="reserved",
    )
    dev.used_mem_mb = dev.total_mem_mb
    dev.used_cores = 100
    db.add_all([resident, alloc])
    return resident, alloc


@pytest.mark.asyncio
async def test_enqueue_preserves_session_priority(db, fake_handoff):
    """B1 regression: a priority-5 session queues in the priority-5 band, not band 0."""
    objs: list = []
    group, user_id, wallet, cluster_id, offering, image, dev = _seed(objs)
    async with db.begin():
        db.add_all(objs)
        _occupy_fully(db, dev, offering, image, cluster_id)

    svc = SchedulerService(db)
    svc.handoff = fake_handoff
    req = SessionCreate(
        offering_id=offering.id, image_id=image.id, resource_class="gpu",
        cluster_id=cluster_id, group_id=group.id, mode="fractional",
        gpu_mem_mb=8000, gpu_cores=50, billing_wallet_id=wallet.id, priority=5,
    )
    # group_admin so _clamp_priority keeps the priority (members are clamped to 0).
    principal = Principal(user_id=user_id, memberships={group.id: "group_admin"})
    out = await svc.create_session(req, principal, idem="idem-prio")
    assert out.status == "pending"

    db.expunge_all()
    entry = (await db.scalars(select(QueueEntry).where(QueueEntry.session_id == out.id))).first()
    assert entry is not None
    assert entry.priority == 5


@pytest.mark.asyncio
async def test_ticker_admits_in_rank_order_when_capacity_returns(db, fake_handoff, monkeypatch):
    """B2 regression: dequeue order == displayed rank order, from the same module."""
    objs: list = []
    group, user_id, wallet, cluster_id, offering, image, dev = _seed(objs)
    async with db.begin():
        db.add_all(objs)
        resident, alloc = _occupy_fully(db, dev, offering, image, cluster_id)

    svc = SchedulerService(db)
    svc.handoff = fake_handoff
    principal_admin = Principal(user_id=user_id, memberships={group.id: "group_admin"})

    def req(prio):
        return SessionCreate(
            offering_id=offering.id, image_id=image.id, resource_class="gpu",
            cluster_id=cluster_id, group_id=group.id, mode="fractional",
            gpu_mem_mb=4000, gpu_cores=25, billing_wallet_id=wallet.id, priority=prio,
        )

    low = await svc.create_session(req(0), principal_admin, idem="idem-low")
    high = await svc.create_session(req(3), principal_admin, idem="idem-high")
    assert low.status == "pending" and high.status == "pending"

    # Free the card: end the resident's allocation so reconciliation sees a free device.
    async with db.begin():
        alloc_row = await db.get(Allocation, alloc.id)
        alloc_row.status = "released"
        alloc_row.ended_at = datetime.now(UTC)

    # The ticker runs with its own sessionmaker; bind it to this test's session factory.
    class _Maker:
        def __call__(self):
            return _Ctx()

    class _Ctx:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(queue_ticker, "get_sessionmaker", lambda: _Maker())
    # Route the freshly constructed SchedulerService at the fake handoff too.
    monkeypatch.setattr(
        queue_ticker, "SchedulerService",
        lambda s: (lambda svc2: (setattr(svc2, "handoff", fake_handoff), svc2)[1])(SchedulerService(s)),
    )

    await queue_ticker.run()

    db.expunge_all()
    remaining = (await db.scalars(select(QueueEntry))).all()
    sess_high = await db.get(SessionRow, high.id)
    sess_low = await db.get(SessionRow, low.id)
    # Both fit after the card freed (4000MB each), admitted high-priority first; queue drained.
    assert remaining == []
    assert sess_high is not None and sess_low is not None
    admitted_ids = [s.id for s, _req, _spec in fake_handoff.calls]
    assert admitted_ids.index(high.id) < admitted_ids.index(low.id)


def test_usage_penalties_lower_score_but_are_bounded():
    """The heavy-user penalty is real but bounded: a user with zero usage eventually outranks
    any non-priority heavy user (max penalty < aging cap)."""
    from app.core.config import settings

    fresh = _entry("light", priority=0, waited_min=queue_ranking.AGING_CAP_MIN)
    heavy = _entry("heavy", priority=0, waited_min=queue_ranking.AGING_CAP_MIN)
    s_light = queue_ranking.score(fresh, active_sessions=0, recent_gpu_hours=0.0)
    s_heavy = queue_ranking.score(heavy, active_sessions=10, recent_gpu_hours=24.0)
    assert s_light > s_heavy
    max_penalty = settings.QUEUE_ACTIVE_PENALTY * 10 + settings.QUEUE_RECENT_HOURS_PENALTY * 24
    assert max_penalty < queue_ranking.AGING_CAP_MIN
    # And penalties never invert an admin priority band.
    prio = _entry("prio", priority=1, waited_min=0)
    s_prio = queue_ranking.score(prio, active_sessions=10, recent_gpu_hours=24.0)
    assert s_prio > s_light


@pytest.mark.asyncio
async def test_rank_penalizes_owner_with_active_session(db):
    """Two equal-wait entries: the one whose owner already runs a session ranks lower."""
    uid_busy, uid_idle = ids.new("user"), ids.new("user")
    offering = Offering(id=ids.new("offering"), name="o", resource_class="gpu",
                        gpu_model="A100", gpu_mem_mb=16000, credit_per_hour=Decimal("60"))
    image = Image(id=ids.new("image"), name="img")
    cluster_id = ids.new("cluster")
    running = SessionRow(
        id=ids.new("session"), owner_user_id=uid_busy, cluster_id=cluster_id,
        offering_id=offering.id, image_id=image.id, resource_class="gpu",
        mode="fractional", status="running",
    )
    q_busy = SessionRow(
        id=ids.new("session"), owner_user_id=uid_busy, cluster_id=cluster_id,
        offering_id=offering.id, image_id=image.id, resource_class="gpu",
        mode="fractional", status="pending",
    )
    q_idle = SessionRow(
        id=ids.new("session"), owner_user_id=uid_idle, cluster_id=cluster_id,
        offering_id=offering.id, image_id=image.id, resource_class="gpu",
        mode="fractional", status="pending",
    )
    now = datetime.now(UTC)
    e_busy = QueueEntry(id=ids.new("queue"), session_id=q_busy.id, session_req={}, priority=0,
                        enqueued_at=now - timedelta(minutes=10))
    e_idle = QueueEntry(id=ids.new("queue"), session_id=q_idle.id, session_req={}, priority=0,
                        enqueued_at=now - timedelta(minutes=10))
    async with db.begin():
        db.add_all([offering, image, running, q_busy, q_idle, e_busy, e_idle])

    ranked = await queue_ranking.rank(db)
    assert [e.session_id for e, _ in ranked] == [q_idle.id, q_busy.id]
