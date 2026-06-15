"""Internal operator audit callback: POST /internal/audit/operator.

Operator privileged actions (cordon/drain/pod-delete/force) are reported here and written to the
hash-chained audit_log. RS256 internal JWT required; Python is the only DB writer.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.internal import OperatorAuditEvent
from app.auth.internal_jwt import require_internal_jwt
from app.db.base import get_db
from app.domain.audit_service import AuditService

router = APIRouter(tags=["internal"])


@router.post("/internal/audit/operator", status_code=status.HTTP_202_ACCEPTED)
async def audit_operator(
    ev: OperatorAuditEvent,
    _claims: dict = Depends(require_internal_jwt),   # aud=gshare-internal
    db: AsyncSession = Depends(get_db),
):
    await AuditService(db).record_operator_action(ev)
    return {"accepted": True}
