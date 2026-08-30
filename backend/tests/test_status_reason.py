"""status_reason propagation: who paused/ended the session, and why, reaches the owner."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.schemas.internal import OperatorStatusEvent
from app.cluster.status_sync import StatusSync, _map_operator_reason
from app.core import ids
from app.db.models import Allocation, GpuDevice, Image, Notification, Offering
from app.db.models import Session as SessionRow


def test_operator_message_mapping():
    assert _map_operator_reason("idle-reaped") == "idle"
    assert _map_operator_reason("max-runtime-exceeded") == "max_runtime"
    assert _map_operator_reason(
        "Evicted: Pod ephemeral local storage usage exceeds the total limit of containers 20Gi."
    ) == "disk_exceeded"
    assert _map_operator_reason(None) is None
    assert _map_operator_reason("something else") is None


async def _running_session(db):
    offering = Offering(id=ids.new("offering"), name="o", resource_class="gpu",
                        gpu_model="A100", gpu_mem_mb=16000, credit_per_hour=Decimal("60"))
    image = Image(id=ids.new("image"), name="img")
    sess = SessionRow(
        id=ids.new("session"), owner_user_id=ids.new("user"), cluster_id=ids.new("cluster"),
        offering_id=offering.id, image_id=image.id, resource_class="gpu",
        mode="fractional", status="running", gpu_mem_mb=4000, gpu_cores=25,
    )
    async with db.begin():
        db.add_all([offering, image, sess])
    return sess


@pytest.mark.asyncio
async def test_operator_pause_sets_idle_reason_and_notifies(db):
    """An operator-initiated pause (idle reaper) marks the session idle and tells the owner why
    — even from an older operator that sends no message."""
    sess = await _running_session(db)
    sync = StatusSync(db)
    await sync.on_status(sess.id, OperatorStatusEvent(phase="paused", ts=datetime.now(UTC)))

    db.expunge_all()
    got = await db.get(SessionRow, sess.id)
    assert got.status == "paused"
    assert got.status_reason == "idle"
    notif = (await db.scalars(select(Notification).where(Notification.user_id == sess.owner_user_id))).all()
    assert any("idle" in str(n.payload).lower() for n in notif)


async def _card_with_live_alloc(db, sess, *, mem=6144, cores=13, used_mem=24576, used_cores=50):
    dev = GpuDevice(
        id=ids.new("device"), node_id=ids.new("node"), cluster_id=sess.cluster_id,
        model="A100", gpu_uuid=ids.new("dev"), total_mem_mb=49152, total_cores=100,
        used_mem_mb=used_mem, used_cores=used_cores, status="ready", mode="fractional",
    )
    alloc = Allocation(
        id=ids.new("allocation"), session_id=sess.id, device_id=dev.id, gpu_uuid=dev.gpu_uuid,
        gpu_mem_mb=mem, gpu_cores=cores, status="bound", kind="resident",
    )
    async with db.begin():
        db.add_all([dev, alloc])
        sess = await db.get(SessionRow, sess.id)
        sess.bound_gpu_uuid = dev.gpu_uuid
    return dev, alloc


@pytest.mark.asyncio
async def test_terminated_event_releases_device_usage_once(db):
    """The terminated callback returns exactly the allocation's booking to its device."""
    sess = await _running_session(db)
    dev, _ = await _card_with_live_alloc(db, sess, mem=6144, cores=13, used_mem=24576, used_cores=50)
    await StatusSync(db).on_status(sess.id, OperatorStatusEvent(phase="terminated", ts=datetime.now(UTC)))

    db.expunge_all()
    fresh = await db.get(GpuDevice, dev.id)
    assert fresh.used_mem_mb == 24576 - 6144
    assert fresh.used_cores == 50 - 13


@pytest.mark.asyncio
async def test_terminated_event_after_backend_release_is_a_full_noop(db):
    """When the backend already settled the allocation (user stop/terminate), the operator's
    terminated callback must not subtract device usage a second time."""
    sess = await _running_session(db)
    dev, alloc = await _card_with_live_alloc(db, sess, mem=6144, cores=13, used_mem=18432, used_cores=37)
    async with db.begin():  # backend release already happened: alloc closed, usage returned
        alloc = await db.get(Allocation, alloc.id)
        alloc.status = "released"
        alloc.ended_at = datetime.now(UTC)
    await StatusSync(db).on_status(sess.id, OperatorStatusEvent(phase="terminated", ts=datetime.now(UTC)))

    db.expunge_all()
    fresh = await db.get(GpuDevice, dev.id)
    assert fresh.used_mem_mb == 18432   # unchanged — no double release
    assert fresh.used_cores == 37


@pytest.mark.asyncio
async def test_operator_terminate_maps_max_runtime(db):
    sess = await _running_session(db)
    sync = StatusSync(db)
    await sync.on_status(sess.id, OperatorStatusEvent(phase="terminated", message="max-runtime-exceeded", ts=datetime.now(UTC)))

    db.expunge_all()
    got = await db.get(SessionRow, sess.id)
    assert got.status == "terminated"
    assert got.status_reason == "max_runtime"
    notif = (await db.scalars(select(Notification).where(Notification.user_id == sess.owner_user_id))).all()
    assert any("runtime limit" in str(n.payload).lower() for n in notif)


@pytest.mark.asyncio
async def test_eviction_maps_disk_exceeded_and_notifies_scratch_disk(db):
    """A kubelet ephemeral-storage eviction reaches the owner as a specific scratch-disk
    notification, not the generic 'hit an error', and lands as status_reason=disk_exceeded."""
    sess = await _running_session(db)
    await StatusSync(db).on_status(sess.id, OperatorStatusEvent(
        phase="error",
        message="Evicted: Pod ephemeral local storage usage exceeds the total limit of "
                "containers 20Gi.",
        ts=datetime.now(UTC),
    ))

    db.expunge_all()
    got = await db.get(SessionRow, sess.id)
    assert got.status == "error"
    assert got.status_reason == "disk_exceeded"
    notif = (await db.scalars(
        select(Notification).where(Notification.user_id == sess.owner_user_id)
    )).all()
    assert any("scratch disk" in str(n.payload).lower() or "scratch-disk" in str(n.payload).lower()
               for n in notif)


@pytest.mark.asyncio
async def test_graceful_eviction_arrives_terminated_and_still_explains_the_disk(db):
    """A gracefully-evicted pod ends Succeeded -> phase 'terminated'; the reason and the
    scratch-disk notification must not depend on the error path."""
    sess = await _running_session(db)
    sync = StatusSync(db)
    await sync.on_status(sess.id, OperatorStatusEvent(
        phase="terminated",
        message="Evicted: Pod ephemeral local storage usage exceeds the total limit of containers 2Gi.",
        ts=datetime.now(UTC),
    ))
    db.expunge_all()
    got = await db.get(SessionRow, sess.id)
    assert got.status == "terminated"
    assert got.status_reason == "disk_exceeded"
    notif = (await db.scalars(select(Notification).where(Notification.user_id == sess.owner_user_id))).all()
    assert any("scratch" in str(n.payload).lower() for n in notif)
