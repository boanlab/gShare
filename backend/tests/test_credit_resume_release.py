"""Regression tests for two billing-integrity fixes in CreditEngine.

1. Resume re-bases ``session.started_at`` per run interval, so the running-total ``already`` must be
   scoped to the current interval (``since=started_at``); a lifetime sum would make every resume bill
   nothing until it re-exceeded the prior total (free GPU time).
2. settle must release only the settling session's reservation slice (held − consumed), not the whole
   wallet ``reserved`` — otherwise a concurrent session's hold on the same wallet is silently freed.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core import ids
from app.db.models import CreditTransaction, CreditWallet
from app.domain.credit_engine import CreditEngine

pytestmark = pytest.mark.asyncio


def _consume_row(wallet_id: str, ref: str, amount: str, created_at: datetime, key: str) -> CreditTransaction:
    return CreditTransaction(
        id=ids.new("transaction"), wallet_id=wallet_id, type="consume",
        amount=Decimal(amount), balance_after=Decimal("0"), ref=ref,
        idempotency_key=key, created_at=created_at,
    )


async def test_sum_consumed_interval_scoping(db):
    """``since`` limits the sum to the current run interval (fixes resume free-time)."""
    wallet = CreditWallet(id=ids.new("wallet"), owner_type="user", owner_id=ids.new("user"),
                          balance=Decimal("1000"), reserved=Decimal("0"))
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)   # interval 1 charge
    t2 = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)   # interval 2 charge (after resume)
    resume = datetime(2026, 1, 1, 1, 0, tzinfo=UTC)  # started_at re-based here
    async with db.begin():
        db.add(wallet)
        db.add(_consume_row(wallet.id, "sesX", "-100", t0, "c1"))
        db.add(_consume_row(wallet.id, "sesX", "-60", t2, "c2"))

    engine = CreditEngine(db)
    sess = SimpleNamespace(id="sesX", billing_wallet_id=wallet.id, started_at=resume)

    assert await engine._sum_consumed(sess) == Decimal("160.00")              # lifetime
    assert await engine._sum_consumed(sess, since=resume) == Decimal("60.00")  # current interval only


async def test_settle_releases_only_own_hold(db):
    """Two sessions hold on one wallet; settling A frees only A's slice, leaving B's hold intact."""
    wallet = CreditWallet(id=ids.new("wallet"), owner_type="user", owner_id=ids.new("user"),
                          balance=Decimal("1000"), reserved=Decimal("0"))
    async with db.begin():
        db.add(wallet)

    engine = CreditEngine(db)
    await engine.hold(wallet.id, Decimal("100"), key="hold:sesA")
    await engine.hold(wallet.id, Decimal("80"), key="hold:sesB")
    assert (await db.scalar(select(CreditWallet.reserved).where(CreditWallet.id == wallet.id))) == Decimal("180.00")

    # snapshot 0 → settle skips consume-finalize and only runs _release_hold.
    sesA = SimpleNamespace(id="sesA", billing_wallet_id=wallet.id,
                           credit_per_hour_snapshot=Decimal("0"), started_at=datetime.now(UTC))
    await engine.settle(sesA, key="settle:sesA")

    reserved = await db.scalar(select(CreditWallet.reserved).where(CreditWallet.id == wallet.id))
    assert reserved == Decimal("80.00")  # only sesA's 100 released; sesB's 80 preserved (bug zeroed it)
