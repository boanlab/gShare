"""audit_retention — prune audit_log rows past the retention window. Daily.

The audit log is append-only and hash-chained; with 2000 students every session action lands
here, so an unbounded table degrades within a term. Deletion runs oldest-first in bounded
batches so the sweep never holds a long transaction. Retention is GSHARE_AUDIT_RETENTION_DAYS
(0 disables pruning entirely).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import get_sessionmaker
from app.db.models import AuditLog

log = get_logger(__name__)

_BATCH = 10_000


async def run() -> None:
    days = settings.AUDIT_RETENTION_DAYS
    if days <= 0:
        return
    cutoff = datetime.now(UTC) - timedelta(days=days)
    maker = get_sessionmaker()
    removed = 0
    async with maker() as db:
        while True:
            async with db.begin():
                ids_batch = (
                    await db.scalars(
                        select(AuditLog.id)
                        .where(AuditLog.created_at < cutoff)
                        .order_by(AuditLog.created_at)
                        .limit(_BATCH)
                    )
                ).all()
                if not ids_batch:
                    break
                await db.execute(delete(AuditLog).where(AuditLog.id.in_(ids_batch)))
                removed += len(ids_batch)
        if removed:
            # Re-anchor the hash chain: the surviving oldest row's prev_hash now references a
            # deleted entry, so record it as the accepted chain head — verify_chain trusts the
            # anchor in the latest audit.retention entry instead of GENESIS.
            from app.domain.audit_service import AuditService

            async with db.begin():
                head = (
                    await db.scalars(
                        select(AuditLog)
                        .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
                        .limit(1)
                    )
                ).first()
                await AuditService(db).record(
                    actor="system", action="audit.retention",
                    pruned=removed, cutoff=cutoff.isoformat(),
                    new_anchor_prev_hash=(head.prev_hash if head else None),
                )
    if removed:
        log.info("audit_retention: pruned %d rows older than %d days", removed, days)
