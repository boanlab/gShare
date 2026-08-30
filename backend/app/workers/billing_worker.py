"""billing_worker — per-minute charge batch, interval 60s.

Bulk-consume sessions where status=running AND credit_per_hour_snapshot>0 (both GPU and CPU;
minute_bucket idempotent). consume start is triggered by the operator's running status event.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.core.logging import get_logger
from app.core.redis import get_redis
from app.db.base import get_sessionmaker
from app.db.models import CreditWallet, Session
from app.domain.credit_engine import CreditEngine
from app.domain.notification_service import NotificationService
from app.domain.webhook_outbox import emit_webhook_safe

log = get_logger(__name__)


async def _warn_low_balance(db, session: Session, now: datetime) -> None:
    """Warn the owner once per day when the wallet covers less than ~2 hours of burn.

    Runs after consume so the balance is current. Best-effort: a failed warning never fails the
    billing batch.
    """
    try:
        if not session.billing_wallet_id or not session.credit_per_hour_snapshot:
            return
        wallet = await db.get(CreditWallet, session.billing_wallet_id)
        if wallet is None:
            return
        available = wallet.balance - wallet.reserved
        threshold = session.credit_per_hour_snapshot * 2
        if available <= 0 or available > threshold:
            return
        marker = f"lowbal:{wallet.id}:{now.date().isoformat()}"
        if not await get_redis().set(marker, "1", nx=True, ex=2 * 24 * 3600):
            return  # already warned today
        async with db.begin():
            await emit_webhook_safe(db, "wallet.low_balance", {
                "wallet_id": session.billing_wallet_id,
                "user_id": session.owner_user_id,
                "session_id": session.id,
                "available": str(available),
            })
            await NotificationService(db).notify(
                [session.owner_user_id], "low_balance", "Credits running low",
                f"Your wallet covers roughly {float(available / session.credit_per_hour_snapshot):.1f} "
                "more hours at the current burn rate. Top up to avoid an automatic pause.",
                params={"hours": round(float(available / session.credit_per_hour_snapshot), 1)},
                reason="low_balance",
            )
    except Exception:  # noqa: BLE001 — advisory only
        log.exception("low-balance warning failed session=%s", session.id)


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
                await _warn_low_balance(db, session, now)
        except Exception:  # noqa: BLE001 — isolate per-session failures
            failed += 1
            log.exception("billing consume failed session=%s bucket=%d", ses_id, minute_bucket)

    if session_ids:
        log.info(
            "billing batch bucket=%d eligible=%d charged=%d failed=%d",
            minute_bucket, len(session_ids), charged, failed,
        )
