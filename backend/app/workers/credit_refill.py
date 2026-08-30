"""credit_refill — the monthly automatic credit refill, use-it-or-lose-it.

Invoked hourly but acts **once a month**, made idempotent by a Redis month marker set with SET NX.
When it runs, every wallet with monthly_grant > 0 has its balance reset to the grant, so last
month's unused credits expire. To preserve active holds the balance is set to max(grant, reserved).

The hierarchy ceiling — the siblings' grants summing within the parent's — is enforced when a grant
is set, so resetting each wallet independently is sufficient here. """
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core import ids
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.db.base import get_sessionmaker
from app.db.models import CreditTransaction, CreditWallet, SystemSetting

log = get_logger(__name__)


async def _schedule(db) -> tuple[int, int]:
    rows = dict((await db.execute(
        select(SystemSetting.key, SystemSetting.value).where(
            SystemSetting.key.in_(["credit_refill_day", "credit_refill_hour"])
        )
    )).all())
    return int(rows.get("credit_refill_day", "1")), int(rows.get("credit_refill_hour", "0"))


async def run() -> None:
    # The month tag stays UTC-stable; the fire moment follows the admin-set KST schedule
    # (day 1-28, hour 0-23; defaults 1st 00:00) — before that moment, do nothing, and the
    # month marker is only claimed once the window opens.
    kst = timezone(timedelta(hours=9))
    now_kst = datetime.now(kst)
    tag = f"{now_kst.year}-{now_kst.month:02d}"
    marker = f"credit_refill:done:{tag}"
    redis = get_redis()
    sched_sessionmaker = get_sessionmaker()
    async with sched_sessionmaker() as sdb:
        day, hour = await _schedule(sdb)
    target = now_kst.replace(day=day, hour=hour, minute=0, second=0, microsecond=0)
    if now_kst < target:
        return
    # Failing to claim the marker means this month is already done, so skip. The 40-day expiry is
    # deliberately generous.
    if not await redis.set(marker, "1", nx=True, ex=40 * 24 * 3600):
        return
    log.info("credit_refill: monthly reset start (%s)", tag)

    sessionmaker = get_sessionmaker()
    reset = 0
    # Batched: one transaction locking every wallet would block concurrent holds for the whole
    # sweep on refill day (thousands of student wallets). 500-wallet batches keyed by id keep
    # each FOR UPDATE window short; the per-wallet txn key makes partial re-runs idempotent.
    last_id = ""
    async with sessionmaker() as db:
        while True:
            async with db.begin():
                wallets = (
                    await db.scalars(
                        select(CreditWallet)
                        .where(CreditWallet.monthly_grant > 0, CreditWallet.id > last_id)
                        .order_by(CreditWallet.id)
                        .limit(500)
                        .with_for_update()
                    )
                ).all()
                if not wallets:
                    break
                last_id = wallets[-1].id
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
