"""Bulk roster import tests: partial success, per-row artifacts, idempotent replay."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.api.users_router import BulkUserCreate, BulkUserRow, bulk_create_users
from app.auth.rbac import Principal
from app.core import ids
from app.db.models import CreditWallet, Membership, Organization, Project, User


def _admin() -> Principal:
    return Principal(user_id=ids.new("user"), global_roles=["super_admin"])


async def _group(db) -> Project:
    org = Organization(id=ids.new("org"), name="school")
    group = Project(id=ids.new("group"), org_id=org.id, name="cs500")
    async with db.begin():
        db.add_all([org, group])
    return group


@pytest.mark.asyncio
async def test_bulk_create_partial_success(db):
    group = await _group(db)
    existing = User(id=ids.new("user"), email="old@u.ac.kr", name="Old")
    async with db.begin():
        db.add(existing)

    body = BulkUserCreate(group_id=group.id, rows=[
        BulkUserRow(email="a@u.ac.kr", name="A"),
        BulkUserRow(email="old@u.ac.kr", name="Old Again"),   # already registered
        BulkUserRow(email="broken-email", name="B"),           # invalid
        BulkUserRow(email="a@u.ac.kr", name="A dup"),          # duplicate within the file
    ])
    out = await bulk_create_users(body, principal=_admin(), db=db, idem="batch-1")

    statuses = {r["row"]: r["status"] for r in out["results"]}
    assert statuses == {0: "created", 1: "exists", 2: "invalid", 3: "invalid"}
    assert out["summary"] == {"requested": 4, "created": 1, "exists": 1, "invalid": 2}

    created = next(r for r in out["results"] if r["status"] == "created")
    assert created["initial_password"]  # returned exactly once, in this response

    db.expunge_all()
    user = (await db.scalars(select(User).where(User.email == "a@u.ac.kr"))).first()
    assert user is not None and user.must_change_password
    assert (await db.scalars(
        select(CreditWallet).where(CreditWallet.owner_type == "user", CreditWallet.owner_id == user.id)
    )).first() is not None
    member = (await db.scalars(select(Membership).where(Membership.user_id == user.id))).first()
    assert member is not None and member.group_id == group.id and member.role == "member"


@pytest.mark.asyncio
async def test_bulk_create_idempotent_replay(db):
    group = await _group(db)
    body = BulkUserCreate(group_id=group.id, rows=[BulkUserRow(email="c@u.ac.kr", name="C")])
    admin = _admin()
    first = await bulk_create_users(body, principal=admin, db=db, idem="batch-2")
    replay = await bulk_create_users(body, principal=admin, db=db, idem="batch-2")
    # Same stored response, initial password included — and no second user row.
    assert replay == first
    db.expunge_all()
    rows = (await db.scalars(select(User).where(User.email == "c@u.ac.kr"))).all()
    assert len(rows) == 1
