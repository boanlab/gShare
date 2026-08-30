"""Internal volume reconciliation: the operator reports every session-volume PVC it sees and is
told, per claim, the quota to grow to and whether the claim may be reclaimed.

POST /internal/volumes/sync — RS256 internal JWT (aud=gshare-internal). Python stays the only DB
writer; the operator stays the only thing touching PVCs.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.internal import OperatorVolumeSync, VolumeSyncResponse
from app.auth.internal_jwt import require_internal_jwt
from app.cluster.volume_sync import VolumeSync
from app.db.base import get_db

router = APIRouter(tags=["internal"])


@router.post("/internal/volumes/sync", response_model=VolumeSyncResponse)
async def sync_volumes(
    report: OperatorVolumeSync,
    _claims: dict = Depends(require_internal_jwt),   # aud=gshare-internal
    db: AsyncSession = Depends(get_db),
) -> VolumeSyncResponse:
    return await VolumeSync(db).sync(report)
