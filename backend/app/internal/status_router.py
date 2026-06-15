"""Internal status callback: POST /internal/sessions/{id}/status.

Operator reports phase transitions; StatusSync reflects them into session state + ledger
(running->consume / terminated->settle). Idempotent. RS256 internal JWT required.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.internal import OperatorStatusEvent
from app.auth.internal_jwt import require_internal_jwt
from app.cluster.status_sync import StatusSync
from app.db.base import get_db

router = APIRouter(tags=["internal"])


@router.post("/internal/sessions/{session_id}/status", status_code=status.HTTP_202_ACCEPTED)
async def report_status(
    session_id: str,
    ev: OperatorStatusEvent,
    _claims: dict = Depends(require_internal_jwt),   # aud=gshare-internal
    db: AsyncSession = Depends(get_db),
):
    await StatusSync(db).on_status(session_id, ev)   # idempotent; Python is the only DB writer
    return {"accepted": True}
