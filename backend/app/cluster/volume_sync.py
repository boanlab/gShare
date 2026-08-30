"""Volume ledger <-> PVC reconciliation, fed by the operator.

The control plane never touches the workload API, so three things about a volume's PVC can only be
learned from, or asked of, the operator:

* how much of the quota is actually used (kubelet volume stats) -> ``StorageVolume.used_gb``;
* that a quota increase must reach the claim (PVC resize -> CSI -> ZFS refquota); a decrease
  never does, since Kubernetes only grows a claim — billing covers that side instead;
* that a volume deleted in the ledger is past its grace window and the PVC — and with it the
  dataset on the storage node — may go.

The operator posts what it sees every few minutes; this answers with a directive per PVC. A PVC no
ledger row explains is never reclaimed from here: that is data with no owner in the books, which an
admin should look at, not an automatic delete.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.internal import (
    OperatorSessionDisk,
    OperatorVolumeObserved,
    OperatorVolumeSync,
    VolumeSyncDirective,
    VolumeSyncResponse,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.db.models import Session, StorageVolume
from app.domain.notification_service import NotificationService

log = get_logger(__name__)

_GIB = 1024**3


def pvc_name_for(volume_id: str) -> str:
    """Mirror of the operator's SanitizeVolumeName: lowercase, '_' -> '-'."""
    return volume_id.lower().replace("_", "-")


class VolumeSync:
    def __init__(self, db: AsyncSession, *, now: datetime | None = None):
        self.db = db
        self.now = now or datetime.now(UTC)

    async def sync(self, report: OperatorVolumeSync) -> VolumeSyncResponse:
        if report.sessions:
            await self._apply_session_disk(report.sessions, cluster_id=report.cluster_id)
        if not report.volumes:
            await self.db.commit()
            return VolumeSyncResponse(volumes=[], orphans=0)

        by_id = {v.volume_id: v for v in report.volumes if v.volume_id}
        names = {v.name for v in report.volumes}
        # Deleted rows are wanted too — they are exactly the ones that may be reclaimed — so the
        # soft-delete filter is not applied here.
        rows = (
            await self.db.execute(
                select(StorageVolume).where(
                    or_(
                        StorageVolume.id.in_(list(by_id)) if by_id else False,
                        func.lower(func.replace(StorageVolume.id, "_", "-")).in_(list(names)),
                    )
                )
            )
        ).scalars().all()
        rows_by_name = {pvc_name_for(r.id): r for r in rows}
        rows_by_id = {r.id: r for r in rows}

        grace = timedelta(hours=float(settings.VOLUME_RECLAIM_GRACE_HOURS))
        out: list[VolumeSyncDirective] = []
        orphans = 0
        for obs in report.volumes:
            row = rows_by_id.get(obs.volume_id or "") or rows_by_name.get(obs.name)
            if row is None:
                orphans += 1
                out.append(VolumeSyncDirective(name=obs.name, volume_id=obs.volume_id))
                continue
            d = VolumeSyncDirective(name=obs.name, volume_id=row.id, quota_gb=int(row.quota_gb))
            if row.deleted_at is not None:
                deleted_at = row.deleted_at
                if deleted_at.tzinfo is None:
                    deleted_at = deleted_at.replace(tzinfo=UTC)
                d.reclaim = self.now - deleted_at >= grace
            elif obs.used_bytes is not None:
                self._apply_usage(row, obs)
            out.append(d)
        await self.db.commit()
        if orphans:
            log.warning("volume_sync: %d PVC(s) without a ledger row; left untouched", orphans)
        return VolumeSyncResponse(volumes=out, orphans=orphans)

    # Scratch-disk pre-warning knobs: warn at 80% of the ephemeral-storage limit (kubelet evicts
    # at 100%), keep the gauge reading for 15 min (refreshed every ~5-min sync tick), and re-warn
    # a given session at most once per 6 h.
    _WARN_FRACTION = 0.80
    _USAGE_TTL_SEC = 900
    _WARN_INTERVAL_SEC = 21600

    async def _apply_session_disk(
        self, entries: list[OperatorSessionDisk], *, cluster_id: str | None = None
    ) -> None:
        """Scratch-disk gauge + pre-warning for live session pods.

        The operator reports each session pod's ephemeral-storage usage against its limit,
        addressed by CR name (the sanitized session id). The reading is stashed in Redis for the
        session-detail gauge, and the owner is warned once per window when usage crosses 80% —
        before kubelet evicts the pod at 100%. "paused" is included because an in-place yield
        keeps the pod (and its ephemeral-storage limit) alive; a cold-paused pod is gone and is
        simply never reported. When the report names its cluster, only that cluster's sessions
        may be addressed; one operator cannot touch another cluster's gauges or warnings.
        """
        names = {e.name for e in entries}
        if not names:
            return
        filters = [
            func.lower(func.replace(Session.id, "_", "-")).in_(list(names)),
            Session.status.in_(("pending", "preparing", "running", "paused")),
        ]
        if cluster_id:
            filters.append(Session.cluster_id == cluster_id)
        rows = (
            await self.db.execute(select(Session).where(*filters))
        ).scalars().all()
        by_name = {r.id.lower().replace("_", "-"): r for r in rows}
        redis = get_redis()
        for e in entries:
            sess = by_name.get(e.name)
            if sess is None or e.ephemeral_limit_bytes <= 0:
                continue
            used, limit = e.ephemeral_used_bytes, e.ephemeral_limit_bytes
            await redis.set(f"sess:diskuse:{sess.id}", f"{used}:{limit}", ex=self._USAGE_TTL_SEC)
            if used / limit < self._WARN_FRACTION:
                continue
            # Warn once per window: SET NX loses on every further sync inside the same window.
            if not await redis.set(f"diskwarn:{sess.id}", "1", nx=True, ex=self._WARN_INTERVAL_SEC):
                continue
            pct = int(used * 100 / limit)
            await NotificationService(self.db).notify(
                [sess.owner_user_id], "session_disk_warning", "Scratch disk almost full",
                f"Session '{sess.name or sess.id}' is using {pct}% of its scratch disk "
                f"({used / _GIB:.1f}/{limit / _GIB:.0f} GiB). The session is terminated if it "
                "exceeds the limit. Move large data to a volume.",
                params={"session_name": sess.name or sess.id, "pct": pct,
                        "used_gib": round(used / _GIB, 1), "limit_gib": round(limit / _GIB, 1)},
                session_id=sess.id,
            )

    @staticmethod
    def _apply_usage(row: StorageVolume, obs: OperatorVolumeObserved) -> None:
        # Nearest GiB, as observed. Usage may legitimately sit ABOVE the quota: a claim never
        # shrinks, so after the owner cuts the quota the dataset still admits writes up to the old
        # refquota — and storage bills max(quota, used), which needs the true figure here.
        row.used_gb = int(round((obs.used_bytes or 0) / _GIB))
