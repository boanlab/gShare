"""billing_worker — per-minute charge batch, interval 60s.

Bulk-consume sessions where status=running AND credit_per_hour_snapshot>0 (both GPU and CPU;
minute_bucket idempotent). consume start is triggered by the operator's running status event.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.base import get_sessionmaker
from app.db.models import Session
from app.domain.credit_engine import CreditEngine

log = get_logger(__name__)


async def run() -> None:
    """Charge all eligible running sessions for the current minute bucket.

    consume is idempotent on consume:{ses}:{minute_bucket}, so duplicate ticks / operator
    restarts cannot double-charge.
    """
    now = datetime.now(UTC)
    minute_bucket = int(now.timestamp()) // 60  # idempotency key dimension

    sessionmaker = get_sessionmaker()

    # 1 snapshot the eligible session id list in a short read-only session.
    async with sessionmaker() as db:
        result = await db.execute(
            select(Session.id).where(
                Session.status == "running",
                # GPU and CPU alike: every billable session with a rate above zero. Free sessions
                # carry snapshot=0 and are excluded.
                Session.credit_per_hour_snapshot > 0,
            )
        )
        session_ids = [row[0] for row in result.all()]

    charged = 0
    failed = 0
    # 2 charge each session in its OWN transaction (per-wallet FOR UPDATE inside consume).
    # A single failing session must not abort the whole batch.
    for ses_id in session_ids:
        try:
            async with sessionmaker() as db:
                session = await db.get(Session, ses_id)
                if session is None or session.status != "running":
                    continue  # raced with terminate/pause; settle path handles the remainder.
                # db.get autobegins a read txn; close it with commit (NOT rollback, which expires
                # the ORM object and triggers lazy-IO errors) so consume owns a clean top-level
                # transaction. expire_on_commit=False keeps `session` usable.
                await db.commit()
                await CreditEngine(db).consume(session, minute_bucket, now)
                charged += 1
        except Exception:  # noqa: BLE001 — isolate per-session failures
            failed += 1
            log.exception("billing consume failed session=%s bucket=%d", ses_id, minute_bucket)

    if session_ids:
        log.info(
            "billing batch bucket=%d eligible=%d charged=%d failed=%d",
            minute_bucket, len(session_ids), charged, failed,
        )
