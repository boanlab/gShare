"""Operator-fed volume reconciliation: usage lands on the ledger, quota growth is handed back,
deleted volumes are reclaimed only after the grace window, and unexplained PVCs are never touched."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.schemas.internal import (
    OperatorSessionDisk,
    OperatorVolumeObserved,
    OperatorVolumeSync,
)
from app.cluster.volume_sync import VolumeSync, pvc_name_for
from app.core import ids
from app.core.config import settings
from app.db.models import Image, Notification, Offering, StorageVolume
from app.db.models import Session as SessionRow

GIB = 1024**3


async def _volume(db, *, quota_gb=10, deleted_at=None):
    vol = StorageVolume(
        id=ids.new("volume"), scope="user", scope_id=ids.new("user"), type="home",
        name="home", access_mode="RWO", quota_gb=quota_gb, used_gb=0, deleted_at=deleted_at,
    )
    async with db.begin():
        db.add(vol)
    return vol


def _obs(vol, **kw):
    return OperatorVolumeObserved(name=pvc_name_for(vol.id), volume_id=vol.id, **kw)


@pytest.mark.asyncio
async def test_usage_is_recorded_as_observed_even_above_quota(db):
    vol = await _volume(db, quota_gb=10)
    resp = await VolumeSync(db).sync(OperatorVolumeSync(volumes=[
        _obs(vol, capacity_gb=10, used_bytes=int(3.4 * GIB), mounted=True),
    ]))
    db.expunge_all()
    got = await db.get(StorageVolume, vol.id)
    assert got.used_gb == 3
    assert resp.volumes[0].quota_gb == 10 and resp.volumes[0].reclaim is False

    # After the owner shrinks the quota the claim keeps its old size, so usage can exceed the
    # quota. It is recorded as observed — storage billing charges max(quota, used) from it.
    await VolumeSync(db).sync(OperatorVolumeSync(volumes=[
        _obs(vol, capacity_gb=12, used_bytes=11 * GIB, mounted=True),
    ]))
    db.expunge_all()
    assert (await db.get(StorageVolume, vol.id)).used_gb == 11


@pytest.mark.asyncio
async def test_unmounted_claim_keeps_last_known_usage(db):
    vol = await _volume(db, quota_gb=10)
    async with db.begin():
        (await db.get(StorageVolume, vol.id)).used_gb = 4
    await VolumeSync(db).sync(OperatorVolumeSync(volumes=[_obs(vol, capacity_gb=10)]))
    db.expunge_all()
    assert (await db.get(StorageVolume, vol.id)).used_gb == 4


@pytest.mark.asyncio
async def test_pvc_without_volume_id_matches_by_sanitized_name(db):
    """Claims created before the id annotation existed are still matched."""
    vol = await _volume(db, quota_gb=5)
    resp = await VolumeSync(db).sync(OperatorVolumeSync(volumes=[
        OperatorVolumeObserved(name=pvc_name_for(vol.id), capacity_gb=5, used_bytes=GIB, mounted=True),
    ]))
    assert resp.volumes[0].volume_id == vol.id and resp.volumes[0].quota_gb == 5
    assert resp.orphans == 0


@pytest.mark.asyncio
async def test_approved_quota_growth_is_handed_back(db):
    vol = await _volume(db, quota_gb=20)
    resp = await VolumeSync(db).sync(OperatorVolumeSync(volumes=[_obs(vol, capacity_gb=10)]))
    assert resp.volumes[0].quota_gb == 20      # the operator grows the 10Gi claim to 20Gi


@pytest.mark.asyncio
async def test_deleted_volume_reclaimed_only_after_grace(db, monkeypatch):
    monkeypatch.setattr(settings, "VOLUME_RECLAIM_GRACE_HOURS", 24)
    now = datetime.now(UTC)
    fresh = await _volume(db, deleted_at=now - timedelta(hours=1))
    stale = await _volume(db, deleted_at=now - timedelta(hours=25))
    resp = await VolumeSync(db, now=now).sync(OperatorVolumeSync(volumes=[_obs(fresh), _obs(stale)]))
    by = {d.volume_id: d for d in resp.volumes}
    assert by[fresh.id].reclaim is False
    assert by[stale.id].reclaim is True


@pytest.mark.asyncio
async def test_unknown_pvc_is_left_alone_and_counted(db):
    resp = await VolumeSync(db).sync(OperatorVolumeSync(volumes=[
        OperatorVolumeObserved(name="vol-doesnotexist", capacity_gb=1),
    ]))
    assert resp.orphans == 1
    assert resp.volumes[0].reclaim is False and resp.volumes[0].quota_gb is None


@pytest.mark.asyncio
async def test_empty_report_is_a_noop(db):
    resp = await VolumeSync(db).sync(OperatorVolumeSync(volumes=[]))
    assert resp.volumes == [] and resp.orphans == 0


# ── scratch-disk gauge + pre-warning (the report's optional "sessions" list) ──

async def _live_session(db, *, status="running"):
    offering = Offering(id=ids.new("offering"), name="o", resource_class="gpu",
                        gpu_model="A100", gpu_mem_mb=16000, credit_per_hour=Decimal("60"))
    image = Image(id=ids.new("image"), name="img")
    sess = SessionRow(
        id=ids.new("session"), owner_user_id=ids.new("user"), cluster_id=ids.new("cluster"),
        offering_id=offering.id, image_id=image.id, resource_class="gpu",
        mode="fractional", status=status, gpu_mem_mb=4000, gpu_cores=25, disk_gb=20,
    )
    async with db.begin():
        db.add_all([offering, image, sess])
    return sess


def _cr_name(sess) -> str:
    """The operator addresses the session by CR name: the sanitized session id."""
    return sess.id.lower().replace("_", "-")


def _disk(sess, used_gib: float, limit_gib: int = 20) -> OperatorSessionDisk:
    return OperatorSessionDisk(
        name=_cr_name(sess),
        ephemeral_used_bytes=int(used_gib * GIB),
        ephemeral_limit_bytes=limit_gib * GIB,
    )


async def _disk_warnings(db, sess):
    return (await db.scalars(select(Notification).where(
        Notification.user_id == sess.owner_user_id,
        Notification.type == "session_disk_warning",
    ))).all()


@pytest.mark.asyncio
async def test_disk_usage_over_threshold_warns_once_per_window(db, fake_redis):
    """85% usage stashes the gauge, warns the owner once, and a second sync inside the 6h
    window refreshes the gauge without a second notification. Matching is by sanitized id."""
    sess = await _live_session(db)
    await VolumeSync(db).sync(OperatorVolumeSync(volumes=[], sessions=[_disk(sess, 17.0)]))

    assert await fake_redis.get(f"sess:diskuse:{sess.id}") == f"{17 * GIB}:{20 * GIB}"
    assert await fake_redis.get(f"diskwarn:{sess.id}") is not None
    notifs = await _disk_warnings(db, sess)
    assert len(notifs) == 1
    assert "85%" in str(notifs[0].payload)
    assert "scratch disk" in str(notifs[0].payload).lower()

    await VolumeSync(db).sync(OperatorVolumeSync(volumes=[], sessions=[_disk(sess, 18.0)]))
    assert await fake_redis.get(f"sess:diskuse:{sess.id}") == f"{18 * GIB}:{20 * GIB}"
    assert len(await _disk_warnings(db, sess)) == 1


@pytest.mark.asyncio
async def test_disk_usage_below_threshold_records_gauge_only(db, fake_redis):
    sess = await _live_session(db)
    await VolumeSync(db).sync(OperatorVolumeSync(volumes=[], sessions=[_disk(sess, 10.0)]))  # 50%
    assert await fake_redis.get(f"sess:diskuse:{sess.id}") == f"{10 * GIB}:{20 * GIB}"
    assert await fake_redis.get(f"diskwarn:{sess.id}") is None
    assert await _disk_warnings(db, sess) == []


@pytest.mark.asyncio
async def test_terminated_session_disk_report_is_ignored(db, fake_redis):
    sess = await _live_session(db, status="terminated")
    await VolumeSync(db).sync(OperatorVolumeSync(volumes=[], sessions=[_disk(sess, 19.0)]))
    assert await fake_redis.get(f"sess:diskuse:{sess.id}") is None
    assert await fake_redis.get(f"diskwarn:{sess.id}") is None
    assert await _disk_warnings(db, sess) == []


@pytest.mark.asyncio
async def test_yield_paused_session_still_gets_gauge_and_warning(db, fake_redis):
    """An in-place yield keeps the pod (and its ephemeral-storage limit) alive, so a paused
    session that the kubelet still reports is gauged and warned like a running one."""
    sess = await _live_session(db, status="paused")
    await VolumeSync(db).sync(OperatorVolumeSync(volumes=[], sessions=[_disk(sess, 17.0)]))
    assert await fake_redis.get(f"sess:diskuse:{sess.id}") == f"{17 * GIB}:{20 * GIB}"
    assert len(await _disk_warnings(db, sess)) == 1


@pytest.mark.asyncio
async def test_session_disk_report_is_scoped_to_the_reporting_cluster(db, fake_redis):
    """A report naming its cluster can only address that cluster's sessions; an entry for a
    session in another cluster is dropped. A report without a cluster_id (old operator) still
    applies."""
    sess = await _live_session(db)
    await VolumeSync(db).sync(OperatorVolumeSync(
        volumes=[], sessions=[_disk(sess, 17.0)], cluster_id="clu_someothercluster",
    ))
    assert await fake_redis.get(f"sess:diskuse:{sess.id}") is None
    assert await _disk_warnings(db, sess) == []

    await VolumeSync(db).sync(OperatorVolumeSync(
        volumes=[], sessions=[_disk(sess, 17.0)], cluster_id=sess.cluster_id,
    ))
    assert await fake_redis.get(f"sess:diskuse:{sess.id}") == f"{17 * GIB}:{20 * GIB}"
    assert len(await _disk_warnings(db, sess)) == 1
