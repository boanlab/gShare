"""Regression tests for the pilot-deployment fixes.

- Unserviceable: a request no device class in the cluster can ever serve is rejected 409
  instead of queueing forever (e.g. exclusive against an all-fractional pool).
- Stop-as-cancel: stopping a session that has not started yet (pending/preparing) cancels it
  (terminate + settle) instead of answering 409 invalid transition.
- Image catalogue: registry is PATCHable; DELETE removes an unused image and refuses one with
  session history.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.images_router import ImageUpdate, delete_image, update_image
from app.api.schemas.session import SessionCreate
from app.auth.rbac import Principal
from app.core import ids
from app.core.errors import DomainError, Unserviceable
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
from app.domain.scheduler import SchedulerService
from app.domain.session_service import SessionService


def _seed(db_objs):
    """Common catalogue seed: org/group, funded wallet, offering, image, one fractional device."""
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
    """Fill the card with a real running session + reserved Allocation so the scheduler's
    drift reconciliation (which recomputes used_* from allocations) keeps it full."""
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


def _gpu_req(offering, image, cluster_id, group, wallet, *, mode, mem, cores):
    return SessionCreate(
        offering_id=offering.id, image_id=image.id, resource_class="gpu",
        cluster_id=cluster_id, group_id=group.id, mode=mode,
        gpu_mem_mb=mem, gpu_cores=cores, billing_wallet_id=wallet.id,
    )


@pytest.mark.asyncio
async def test_exclusive_without_matching_pool_is_unserviceable(db, fake_handoff):
    """An exclusive request against a cluster whose only card is fractional gets 409
    `unserviceable` — not a queue entry that could never be dequeued."""
    objs: list = []
    group, user_id, wallet, cluster_id, offering, image, _dev = _seed(objs)
    async with db.begin():
        db.add_all(objs)

    svc = SchedulerService(db)
    svc.handoff = fake_handoff

    req = _gpu_req(offering, image, cluster_id, group, wallet, mode="exclusive", mem=None, cores=None)
    with pytest.raises(Unserviceable) as excinfo:
        await svc.create_session(req, Principal(user_id=user_id), idem="idem-unserviceable")
    assert excinfo.value.code == "unserviceable"
    assert excinfo.value.http == 409

    db.expunge_all()
    assert (await db.scalars(select(QueueEntry))).first() is None
    # The rejected row is kept for audit as `error`, and now carries WHY: the typed error code
    # doubles as status_reason so the console can explain the failure to anyone looking later.
    from app.db.models import Session as SessionRow
    row = (await db.scalars(select(SessionRow).order_by(SessionRow.created_at.desc()))).first()
    assert row is not None and row.status == "error"
    assert row.status_reason == "unserviceable"


@pytest.mark.asyncio
async def test_full_fractional_pool_still_queues(db, fake_handoff):
    """A serviceable-but-currently-full request keeps the existing queue behaviour."""
    objs: list = []
    group, user_id, wallet, cluster_id, offering, image, dev = _seed(objs)
    async with db.begin():
        db.add_all(objs)
        _occupy_fully(db, dev, offering, image, cluster_id)

    svc = SchedulerService(db)
    svc.handoff = fake_handoff

    req = _gpu_req(offering, image, cluster_id, group, wallet, mode="fractional", mem=8000, cores=50)
    out = await svc.create_session(req, Principal(user_id=user_id), idem="idem-queued")
    assert out.status == "pending"
    db.expunge_all()
    assert (await db.scalars(select(QueueEntry))).first() is not None


@pytest.mark.asyncio
async def test_stop_on_pending_session_cancels(db, fake_handoff):
    """stop() on a session that never started terminates it and settles the hold
    instead of raising InvalidStateTransition (409)."""
    objs: list = []
    group, user_id, wallet, cluster_id, offering, image, dev = _seed(objs)
    async with db.begin():
        db.add_all(objs)
        _occupy_fully(db, dev, offering, image, cluster_id)

    svc = SchedulerService(db)
    svc.handoff = fake_handoff
    req = _gpu_req(offering, image, cluster_id, group, wallet, mode="fractional", mem=8000, cores=50)
    out = await svc.create_session(req, Principal(user_id=user_id), idem="idem-cancel")
    assert out.status == "pending"
    wallet_id = wallet.id
    db.expunge_all()
    assert (await db.get(CreditWallet, wallet_id)).reserved > Decimal("0")  # hold taken

    await SessionService(db).stop(out.id)

    db.expunge_all()
    sess = await db.get(SessionRow, out.id)
    assert sess.status == "terminated"
    # The hold was released on settle.
    assert (await db.get(CreditWallet, wallet_id)).reserved == Decimal("0.00")


@pytest.mark.asyncio
async def test_image_registry_patch_and_delete(db):
    admin = Principal(user_id=ids.new("user"), global_roles=["super_admin"])
    img = Image(id=ids.new("image"), name="temp", registry="a/b:1")
    async with db.begin():
        db.add(img)
    image_id = img.id

    out = await update_image(image_id, ImageUpdate(registry="a/b:2"), principal=admin, db=db)
    assert out["registry"] == "a/b:2"

    await delete_image(image_id, principal=admin, db=db)
    db.expunge_all()
    assert await db.get(Image, image_id) is None


@pytest.mark.asyncio
async def test_image_delete_refused_with_session_history(db):
    admin = Principal(user_id=ids.new("user"), global_roles=["super_admin"])
    img = Image(id=ids.new("image"), name="used", registry="a/b:1")
    offering = Offering(
        id=ids.new("offering"), name="cpu", resource_class="cpu", credit_per_hour=Decimal("0"),
    )
    async with db.begin():
        db.add_all([img, offering])
    sess = SessionRow(
        id=ids.new("session"), owner_user_id=ids.new("user"), cluster_id=ids.new("cluster"),
        offering_id=offering.id, image_id=img.id, resource_class="cpu", status="terminated",
    )
    async with db.begin():
        db.add(sess)

    with pytest.raises(DomainError) as excinfo:
        await delete_image(img.id, principal=admin, db=db)
    assert excinfo.value.http == 409
    db.expunge_all()
    assert await db.get(Image, img.id) is not None
