"""Per-group welcome credit: minted once into the member's personal wallet, idempotently."""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core import ids
from app.db.models import CreditTransaction, CreditWallet, Organization, Project
from app.domain.welcome_credit import grant_welcome_credit


@pytest.mark.asyncio
async def test_grant_once_and_idempotent(db):
    org = Organization(id=ids.new("org"), name="SW융합대학")
    grp = Project(id=ids.new("group"), org_id=org.id, name="컴퓨터공학과",
                  default_member_credit=Decimal("300"))
    user_id = ids.new("user")
    wallet = CreditWallet(id=ids.new("wallet"), owner_type="user", owner_id=user_id,
                          balance=Decimal("0"), reserved=Decimal("0"))
    async with db.begin():
        db.add_all([org, grp, wallet])

    async with db.begin():
        granted = await grant_welcome_credit(db, user_id, grp)
    assert granted == Decimal("300")

    db.expunge_all()
    w = await db.get(CreditWallet, wallet.id)
    assert w.balance == Decimal("300")
    txns = (await db.scalars(select(CreditTransaction).where(CreditTransaction.wallet_id == wallet.id))).all()
    assert len(txns) == 1 and txns[0].type == "topup" and txns[0].ref == f"welcome:{grp.id}"
    await db.commit()

    # Re-adding the user to the group must not double-pay.
    async with db.begin():
        again = await grant_welcome_credit(db, user_id, grp)
    assert again is None
    db.expunge_all()
    w = await db.get(CreditWallet, wallet.id)
    assert w.balance == Decimal("300")


@pytest.mark.asyncio
async def test_zero_setting_grants_nothing(db):
    org = Organization(id=ids.new("org"), name="o")
    grp = Project(id=ids.new("group"), org_id=org.id, name="g")  # default 0
    user_id = ids.new("user")
    wallet = CreditWallet(id=ids.new("wallet"), owner_type="user", owner_id=user_id,
                          balance=Decimal("0"), reserved=Decimal("0"))
    async with db.begin():
        db.add_all([org, grp, wallet])
    async with db.begin():
        assert await grant_welcome_credit(db, user_id, grp) is None
