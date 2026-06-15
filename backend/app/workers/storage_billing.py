"""storage_billing — per-minute charge for retained storage capacity, interval =
BILLING_INTERVAL_SEC.

Unlike compute (billed only while a session runs), a StorageVolume bills continuously by the
capacity it HOLDS (quota_gb): owed_per_minute = quota_gb * STORAGE_CREDIT_PER_GB_HOUR / 60, charged
to the volume's scope wallet (user|project). Idempotent on storage:{vol}:{minute_bucket}, so
duplicate ticks / restarts cannot double-charge. Rate 0 (config) disables storage billing.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import get_sessionmaker
from app.db.models import CreditWallet, StorageVolume
from app.domain.credit_engine import CreditEngine

log = get_logger(__name__)


async def run() -> None:
    """Charge every billable volume (non-deleted, quota_gb>0) for the current minute bucket."""
    rate = Decimal(str(settings.STORAGE_CREDIT_PER_GB_HOUR))
    if rate <= 0:
        return  # storage billing disabled

    now = datetime.now(UTC)
    minute_bucket = int(now.timestamp()) // 60  # idempotency key dimension

    sessionmaker = get_sessionmaker()

    # 1 snapshot billable volumes in a short read-only session.
    async with sessionmaker() as db:
        rows = (
            await db.execute(
                select(
                    StorageVolume.id,
                    StorageVolume.scope,
                    StorageVolume.scope_id,
                    StorageVolume.quota_gb,
                ).where(
                    StorageVolume.deleted_at.is_(None),
                    StorageVolume.quota_gb > 0,
                )
            )
        ).all()

    charged = exhausted = failed = 0
    # 2 charge each volume in its OWN transaction (per-wallet FOR UPDATE inside meter).
    # A single failing/broke volume must not abort the whole batch.
    for vol_id, scope, scope_id, quota_gb in rows:
        try:
            async with sessionmaker() as db:
                # scope(user|project)+scope_id -> owning wallet (CreditWallet.owner_type/owner_id).
                wallet_id = await db.scalar(
                    select(CreditWallet.id).where(
                        CreditWallet.owner_type == scope,
                        CreditWallet.owner_id == scope_id,
                    )
                )
                if wallet_id is None:
                    continue  # no wallet for this scope → nothing to charge
                # close the autobegun read txn so meter owns a clean top-level transaction
                # (expire_on_commit=False keeps the ids usable).
                await db.commit()
                owed = Decimal(quota_gb) * rate / Decimal(60)  # per-minute slice
                key = f"storage:{vol_id}:{minute_bucket}"
                ok = await CreditEngine(db).meter(wallet_id, owed, ref=vol_id, key=key, type="storage")
                charged += 1
                if not ok:
                    exhausted += 1
        except Exception:  # noqa: BLE001 — isolate per-volume failures
            failed += 1
            log.exception("storage billing failed volume=%s bucket=%d", vol_id, minute_bucket)

    if rows:
        log.info(
            "storage billing bucket=%d volumes=%d charged=%d exhausted=%d failed=%d",
            minute_bucket, len(rows), charged, exhausted, failed,
        )
