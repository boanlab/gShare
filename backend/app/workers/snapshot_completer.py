"""snapshot_completer — completes volume snapshots.

Single-node and simulated environments have no CSI snapshot completion callback, so a snapshot left in
``creating`` is moved to ``ready`` after a short grace period and its owner is notified. In a real
deployment the CSI driver or the operator drives that transition and this worker is only a backstop.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.base import get_sessionmaker
from app.db.models import StorageVolume, VolumeSnapshot
from app.domain.notification_service import NotificationService

log = get_logger(__name__)

GRACE_SEC = 10  # after this long, a simulated snapshot is treated as complete


async def run() -> None:
    sm = get_sessionmaker()
    cutoff = datetime.now(UTC) - timedelta(seconds=GRACE_SEC)
    async with sm() as db:
        async with db.begin():
            snaps = (
                await db.scalars(
                    select(VolumeSnapshot).where(
                        VolumeSnapshot.status == "creating",
                        VolumeSnapshot.created_at <= cutoff,
                    )
                )
            ).all()
            if not snaps:
                return
            notifier = NotificationService(db)
            for s in snaps:
                s.status = "ready"
                if s.size_bytes is None:
                    s.size_bytes = 0
                vol = await db.get(StorageVolume, s.volume_id)
                owner = vol.owner_id if vol else None
                if owner:
                    await notifier.notify(
                        [owner], "volume_snapshot_ready", "Snapshot ready",
                        "Your volume snapshot is ready.",
                        volume_id=s.volume_id, snapshot_id=s.id,
                    )
    log.info("snapshot_completer: %d snapshot(s) -> ready", len(snaps))
