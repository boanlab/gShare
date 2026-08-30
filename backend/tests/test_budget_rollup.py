"""Budget rollup attribution: spend lands on personal wallets but must roll up by SESSION scope
(txn.ref == session id → session.group_id), not by wallet ownership."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.core import ids
from app.db.models import (
    Budget,
    CreditTransaction,
    CreditWallet,
    Organization,
    Project,
)
from app.db.models import Session as SessionRow
from app.workers.budget_rollup import _period_window, _scope_group_ids, _spent_in_period


async def _seed(db):
    org = Organization(id=ids.new("org"), name="SW융합대학", timezone="Asia/Seoul")
    grp = Project(id=ids.new("group"), org_id=org.id, name="컴퓨터공학과")
    other = Project(id=ids.new("group"), org_id=ids.new("org"), name="다른조직학과")
    user_id = ids.new("user")
    wallet = CreditWallet(id=ids.new("wallet"), owner_type="user", owner_id=user_id,
                          balance=Decimal("100"), reserved=Decimal("0"))
    sess = SessionRow(
        id=ids.new("session"), owner_user_id=user_id, group_id=grp.id,
        cluster_id=ids.new("cluster"), offering_id="off", image_id="img",
        resource_class="gpu", mode="fractional", status="running",
    )
    outside = SessionRow(
        id=ids.new("session"), owner_user_id=user_id, group_id=other.id,
        cluster_id=ids.new("cluster"), offering_id="off", image_id="img",
        resource_class="gpu", mode="fractional", status="running",
    )
    txns = [
        # consume rows are stored NEGATIVE; both hit the same personal wallet.
        CreditTransaction(id=ids.new("txn"), wallet_id=wallet.id, type="consume",
                          amount=Decimal("-10"), balance_after=Decimal("90"),
                          ref=sess.id, idempotency_key="c1"),
        CreditTransaction(id=ids.new("txn"), wallet_id=wallet.id, type="refund",
                          amount=Decimal("2"), balance_after=Decimal("92"),
                          ref=sess.id, idempotency_key="r1"),
        # Spend from a session in ANOTHER org's group must not roll up.
        CreditTransaction(id=ids.new("txn"), wallet_id=wallet.id, type="consume",
                          amount=Decimal("-50"), balance_after=Decimal("42"),
                          ref=outside.id, idempotency_key="c2"),
    ]
    async with db.begin():
        db.add_all([org, grp, other, wallet, sess, outside, *txns])
    return org, grp


@pytest.mark.asyncio
async def test_spend_rolls_up_by_session_scope(db):
    org, grp = await _seed(db)
    budget = Budget(id=ids.new("budget"), scope="group", scope_id=grp.id,
                    period_start=datetime(2020, 1, 1, tzinfo=UTC), period="monthly",
                    limit_credit=Decimal("100"), spent_credit=Decimal("0"), action="alert")
    async with db.begin():
        db.add(budget)

    start, end = _period_window(budget, "Asia/Seoul")
    group_ids = await _scope_group_ids(db, budget)
    spent = await _spent_in_period(db, group_ids, start, end)
    # 10 consumed − 2 refunded = 8; the other org's 50 is excluded.
    assert spent == Decimal("8.00")


@pytest.mark.asyncio
async def test_org_budget_covers_all_its_groups(db):
    org, grp = await _seed(db)
    budget = Budget(id=ids.new("budget"), scope="org", scope_id=org.id,
                    period_start=datetime(2020, 1, 1, tzinfo=UTC), period="monthly",
                    limit_credit=Decimal("100"), spent_credit=Decimal("0"), action="alert")
    async with db.begin():
        db.add(budget)

    group_ids = await _scope_group_ids(db, budget)
    assert group_ids == [grp.id]
    start, end = _period_window(budget, "Asia/Seoul")
    assert await _spent_in_period(db, group_ids, start, end) == Decimal("8.00")
