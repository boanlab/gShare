"""Group-wallet top-up requests: the group administrator's channel to the system tier.

Escalation is gone; a group admin who needs funding raises a TopupRequest against the GROUP wallet,
which lands in the super_admin inbox. A plain member can read the group wallet but must not ask for
money in the group's name.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.api.credits_router import create_topup_request, list_topup_requests
from app.api.deps import Pagination
from app.api.schemas.credit import TopupRequestBody
from app.auth.rbac import Principal
from app.core import ids
from app.core.errors import Forbidden
from app.db.models import CreditWallet, Organization, Project, User


async def _group(db):
    org = Organization(id=ids.new("org"), name="o1")
    prj = Project(id=ids.new("group"), org_id=org.id, name="cs")
    gw = CreditWallet(id=ids.new("wallet"), owner_type="group", owner_id=prj.id,
                      balance=Decimal("0"), reserved=Decimal("0"))
    admin = User(id=ids.new("user"), email="ga@t.local", name="ga")
    async with db.begin():
        db.add_all([org, prj, gw, admin])
    return prj, gw, admin.id


@pytest.mark.asyncio
async def test_group_admin_can_request_group_topup(db):
    prj, gw, uid = await _group(db)
    p = Principal(user_id=uid, memberships={prj.id: "group_admin"})
    out = await create_topup_request(TopupRequestBody(amount=Decimal("100"), wallet_id=gw.id),
                                     wallet_id=None, principal=p, db=db)
    assert out["wallet_id"] == gw.id and out["status"] == "pending"

    # The super_admin inbox labels it as GROUP funding, by name.
    root = Principal(user_id=ids.new("user"), global_role="super_admin",
                     global_roles={"super_admin"}, memberships={})
    lst = await list_topup_requests(page=Pagination(page=1, size=20), status_filter="pending",
                                    wallet_id=None, scope="all", principal=root, db=db)
    row = next(r for r in lst["data"] if r["id"] == out["id"])
    assert row["wallet_owner_type"] == "group" and row["wallet_owner_name"] == "cs"


@pytest.mark.asyncio
async def test_plain_member_cannot_request_group_topup(db):
    prj, gw, _ = await _group(db)
    member = Principal(user_id=ids.new("user"), memberships={prj.id: "member"})
    with pytest.raises(Forbidden):
        await create_topup_request(TopupRequestBody(amount=Decimal("50"), wallet_id=gw.id),
                                   wallet_id=None, principal=member, db=db)
