"""Daily-spend aggregation for the wallet chart: sums consume+storage, bucketed in the caller's
timezone, everything else (topup/hold/refund) excluded."""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.api.credits_router import spend_daily
from app.auth.rbac import Principal
from app.core import ids
from app.db.models import CreditTransaction, CreditWallet, User


async def _wallet(db) -> tuple[str, str]:
    uid = ids.new("user")
    w = CreditWallet(id=ids.new("wallet"), owner_type="user", owner_id=uid,
                     balance=Decimal("100"), reserved=Decimal("0"))
    async with db.begin():
        db.add_all([User(id=uid, email=f"{uid}@t.local", name="u"), w])
    return uid, w.id


def _txn(wallet_id: str, type_: str, amount: str, at: datetime) -> CreditTransaction:
    return CreditTransaction(
        id=ids.new("txn"), wallet_id=wallet_id, type=type_, amount=Decimal(amount),
        balance_after=Decimal("0"), idempotency_key=ids.new("txn"), created_at=at,
    )


@pytest.mark.asyncio
async def test_sums_spend_by_day_and_ignores_non_spend(db):
    uid, wid = await _wallet(db)
    async with db.begin():
        db.add_all([
            _txn(wid, "consume", "-1.5", datetime(2026, 8, 10, 3, 0, tzinfo=UTC)),
            _txn(wid, "consume", "-0.5", datetime(2026, 8, 10, 22, 0, tzinfo=UTC)),
            _txn(wid, "storage", "-0.25", datetime(2026, 8, 11, 1, 0, tzinfo=UTC)),
            _txn(wid, "topup", "50", datetime(2026, 8, 10, 4, 0, tzinfo=UTC)),   # not spend
            _txn(wid, "hold", "-10", datetime(2026, 8, 10, 5, 0, tzinfo=UTC)),   # not spend
            _txn(wid, "consume", "-9", datetime(2026, 9, 1, 0, 0, tzinfo=UTC)),  # out of range
        ])
    out = await spend_daily(wid, frm=date(2026, 8, 1), to=date(2026, 8, 31), tz_offset_min=0,
                            principal=Principal(user_id=uid, memberships={}), db=db)
    got = {r.date: r.amount for r in out}
    assert got == {"2026-08-10": 2.0, "2026-08-11": 0.25}


@pytest.mark.asyncio
async def test_buckets_follow_the_callers_timezone(db):
    uid, wid = await _wallet(db)
    # 23:30 UTC on the 10th is 08:30 KST on the 11th.
    async with db.begin():
        db.add(_txn(wid, "consume", "-3", datetime(2026, 8, 10, 23, 30, tzinfo=UTC)))
    out = await spend_daily(wid, frm=date(2026, 8, 1), to=date(2026, 8, 31), tz_offset_min=540,
                            principal=Principal(user_id=uid, memberships={}), db=db)
    assert {r.date: r.amount for r in out} == {"2026-08-11": 3.0}
