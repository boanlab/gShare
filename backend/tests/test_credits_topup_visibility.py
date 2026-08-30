"""Top-up visibility: everyone defaults to their OWN requests (wallet page); the global
inbox is scope=all and admin-only — an admin's personal wallet page must not show the fleet."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.api.credits_router import list_topup_requests
from app.api.deps import Pagination
from app.auth.rbac import Principal
from app.core import ids
from app.db.models import CreditWallet, TopupRequest


@pytest.mark.asyncio
async def test_member_sees_only_their_own_topup_requests(db):
    me, other = ids.new("user"), ids.new("user")
    w1 = CreditWallet(id=ids.new("wallet"), owner_type="user", owner_id=me, balance=Decimal("0"), reserved=Decimal("0"))
    w2 = CreditWallet(id=ids.new("wallet"), owner_type="user", owner_id=other, balance=Decimal("0"), reserved=Decimal("0"))
    r1 = TopupRequest(id=ids.new("topup"), wallet_id=w1.id, requester_id=me, amount=Decimal("10"), status="pending")
    r2 = TopupRequest(id=ids.new("topup"), wallet_id=w2.id, requester_id=other, amount=Decimal("20"), status="pending")
    async with db.begin():
        db.add_all([w1, w2, r1, r2])

    mine = await list_topup_requests(page=Pagination(page=1, size=50), status_filter=None, wallet_id=None,
                                     principal=Principal(user_id=me), db=db)
    assert [r["id"] for r in mine["data"]] == [r1.id]

    root = Principal(user_id=ids.new("user"), global_role="super_admin",
                     global_roles=["super_admin"], memberships={})
    # An admin's DEFAULT view is also mine-scoped: their personal wallet page must not leak
    # other users' requests (they have none here, so the list is empty).
    admin_mine = await list_topup_requests(page=Pagination(page=1, size=50), status_filter=None,
                                           wallet_id=None, principal=root, db=db)
    assert admin_mine["data"] == []
    # The approver inbox is explicit: scope=all, admin-only.
    admin_all = await list_topup_requests(page=Pagination(page=1, size=50), status_filter=None,
                                          wallet_id=None, scope="all", principal=root, db=db)
    assert {r["id"] for r in admin_all["data"]} >= {r1.id, r2.id}
    from app.core.errors import Forbidden
    with pytest.raises(Forbidden):
        await list_topup_requests(page=Pagination(page=1, size=50), status_filter=None,
                                  wallet_id=None, scope="all", principal=Principal(user_id=me), db=db)
