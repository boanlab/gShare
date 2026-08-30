"""Bulk credit operations: group-wide allocate, group-wide monthly grant, membership grant."""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.credits_router import bulk_allocate, bulk_monthly_grant
from app.api.schemas.credit import BulkAllocateBody, BulkMonthlyGrantBody
from app.auth.rbac import Principal
from app.core import ids
from app.core.errors import InsufficientCredit
from app.db.models import CreditTransaction, CreditWallet, Membership, Organization, Project, User


async def _cohort(db, n=3, group_balance="1000", group_grant="0"):
    org = Organization(id=ids.new("org"), name="o")
    group = Project(id=ids.new("group"), org_id=org.id, name="cs500")
    gw = CreditWallet(
        id=ids.new("wallet"), owner_type="group", owner_id=group.id,
        balance=Decimal(group_balance), reserved=Decimal("0"), monthly_grant=Decimal(group_grant),
    )
    users, wallets, members = [], [], []
    user_rows: list[User] = []
    for _ in range(n):
        uid = ids.new("user")
        users.append(uid)
        user_rows.append(User(id=uid, email=f"{uid}@t.local", name=uid[-6:]))
        wallets.append(CreditWallet(
            id=ids.new("wallet"), owner_type="user", owner_id=uid,
            balance=Decimal("0"), reserved=Decimal("0"),
        ))
        members.append(Membership(id=ids.new("membership"), user_id=uid, group_id=group.id, role="member"))
    async with db.begin():
        db.add_all([org, group, gw, *user_rows, *wallets, *members])
    return group, gw, users, wallets


def _admin(group_id: str) -> Principal:
    return Principal(user_id=ids.new("user"), memberships={group_id: "group_admin"})


@pytest.mark.asyncio
async def test_bulk_allocate_funds_every_member_and_conserves_total(db):
    group, gw, users, wallets = await _cohort(db, n=3, group_balance="1000")
    out = await bulk_allocate(
        BulkAllocateBody(group_id=group.id, amount=Decimal("100")),
        principal=_admin(group.id), idem="term-2026-1", db=db,
    )
    assert out["granted"] == 3 and out["replayed"] == 0
    db.expunge_all()
    src = await db.get(CreditWallet, gw.id)
    assert src.balance == Decimal("700")            # 1000 - 3x100: sum conserved
    for w in wallets:
        assert (await db.get(CreditWallet, w.id)).balance == Decimal("100")


@pytest.mark.asyncio
async def test_bulk_allocate_replay_is_idempotent(db):
    group, gw, _users, wallets = await _cohort(db, n=2, group_balance="500")
    admin = _admin(group.id)
    await bulk_allocate(BulkAllocateBody(group_id=group.id, amount=Decimal("50")),
                        principal=admin, idem="batch-x", db=db)
    out2 = await bulk_allocate(BulkAllocateBody(group_id=group.id, amount=Decimal("50")),
                               principal=admin, idem="batch-x", db=db)
    assert out2["granted"] == 0 and out2["replayed"] == 2
    db.expunge_all()
    assert (await db.get(CreditWallet, gw.id)).balance == Decimal("400")  # debited once
    for w in wallets:
        assert (await db.get(CreditWallet, w.id)).balance == Decimal("50")


@pytest.mark.asyncio
async def test_bulk_allocate_rejects_when_pool_cannot_cover_everyone(db):
    group, gw, _users, wallets = await _cohort(db, n=3, group_balance="250")
    with pytest.raises(InsufficientCredit):
        await bulk_allocate(BulkAllocateBody(group_id=group.id, amount=Decimal("100")),
                            principal=_admin(group.id), idem="short", db=db)


@pytest.mark.asyncio
async def test_bulk_monthly_grant_ceiling_and_apply(db):
    group, gw, _users, wallets = await _cohort(db, n=2, group_grant="300")
    out = await bulk_monthly_grant(
        BulkMonthlyGrantBody(group_id=group.id, amount=Decimal("150")),
        principal=_admin(group.id), db=db,
    )
    assert out["members"] == 2
    db.expunge_all()
    for w in wallets:
        got = await db.get(CreditWallet, w.id)
        assert got.monthly_grant == Decimal("150")
        assert got.balance == Decimal("150")   # increase credits immediately

    from app.core.errors import DomainError
    with pytest.raises(DomainError):   # 2 x 200 = 400 > parent grant 300
        await bulk_monthly_grant(
            BulkMonthlyGrantBody(group_id=group.id, amount=Decimal("200")),
            principal=_admin(group.id), db=db,
        )


@pytest.mark.asyncio
async def test_membership_grant_credit_actually_moves_money(db):
    """The grant_credit field on membership-add now debits the group wallet (it used to be
    audit-logged with no transfer)."""
    from app.api.groups_router import MembershipCreate, add_membership

    org = Organization(id=ids.new("org"), name="o")
    group = Project(id=ids.new("group"), org_id=org.id, name="g")
    gw = CreditWallet(id=ids.new("wallet"), owner_type="group", owner_id=group.id,
                      balance=Decimal("100"), reserved=Decimal("0"))
    from app.db.models import User
    user = User(id=ids.new("user"), email="s@u.ac.kr", name="S")
    uw = CreditWallet(id=ids.new("wallet"), owner_type="user", owner_id=user.id,
                      balance=Decimal("0"), reserved=Decimal("0"))
    async with db.begin():
        db.add_all([org, group, gw, user, uw])

    await add_membership(
        group.id, MembershipCreate(user_id=user.id, role="member", grant_credit="40"),
        principal=Principal(user_id=ids.new("user"), memberships={group.id: "group_admin"}), db=db,
    )
    db.expunge_all()
    assert (await db.get(CreditWallet, gw.id)).balance == Decimal("60")
    assert (await db.get(CreditWallet, uw.id)).balance == Decimal("40")
    txns = (await db.scalars(select(CreditTransaction))).all()
    assert {t.amount for t in txns} == {Decimal("-40"), Decimal("40")}
