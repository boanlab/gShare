"""credit_refill — the monthly automatic credit refill, use-it-or-lose-it.

Invoked hourly but acts **once a month**, made idempotent by a Redis month marker set with SET NX.
When it runs, every wallet with monthly_grant > 0 has its balance reset to the grant, so last
month's unused credits expire. To preserve active holds the balance is set to max(grant, reserved).

The hierarchy ceiling — the siblings' grants summing within the parent's — is enforced when a grant
is set, so resetting each wallet independently is sufficient here. """
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.core import ids
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.db.base import get_sessionmaker
from app.db.models import CreditTransaction, CreditWallet

log = get_logger(__name__)


async def run() -> None:
    now = datetime.now(UTC)
    tag = f"{now.year}-{now.month:02d}"
    marker = f"credit_refill:done:{tag}"
    redis = get_redis()
    # Failing to claim the marker means this month is already done, so skip. The 40-day expiry is
    # deliberately generous.
    if not await redis.set(marker, "1", nx=True, ex=40 * 24 * 3600):
        return
    log.info("credit_refill: monthly reset start (%s)", tag)

    sessionmaker = get_sessionmaker()
    reset = 0
    async with sessionmaker() as db:
        async with db.begin():
            wallets = (
                await db.scalars(
                    select(CreditWallet).where(CreditWallet.monthly_grant > 0).with_for_update()
                )
            ).all()
            for w in wallets:
                target = w.monthly_grant if w.monthly_grant >= w.reserved else w.reserved
                if target == w.balance:
                    continue
                delta = target - w.balance
                w.balance = target
                w.version = w.version + 1
                db.add(
                    CreditTransaction(
                        id=ids.new("transaction"),
                        wallet_id=w.id,
                        type="adjust",
                        amount=delta,
                        balance_after=w.balance,
                        ref="monthly_refill",
                        idempotency_key=f"refill:{w.id}:{tag}",
                    )
                )
                reset += 1
    log.info("credit_refill: reset %d wallet(s) for %s", reset, tag)
