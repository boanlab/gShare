"""Org-list aggregates: soft-deleted users must not inflate 사용자 수 (their Membership rows
survive a soft delete by design — the count query has to look at User.deleted_at)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.api.deps import Pagination
from app.api.groups_router import list_memberships, list_organizations
from app.auth.rbac import Principal
from app.core import ids
from app.db.models import Membership, Organization, Project, User


def _admin() -> Principal:
    return Principal(user_id=ids.new("user"), global_role="super_admin", global_roles=["super_admin"])


async def _seed(db):
    org = Organization(id=ids.new("org"), name="SW융합대학")
    grp = Project(id=ids.new("project"), org_id=org.id, name="소프트웨어학과")
    alive = User(id=ids.new("user"), email="alive@x.kr", name="Alive")
    ghost = User(id=ids.new("user"), email="ghost@x.kr", name="Ghost",
                 status="suspended", deleted_at=datetime.now(UTC))
    db.add_all([org, grp, alive, ghost,
                Membership(id=ids.new("membership"), user_id=alive.id, group_id=grp.id, role="member"),
                Membership(id=ids.new("membership"), user_id=ghost.id, group_id=grp.id, role="member")])
    await db.commit()
    return org, grp


@pytest.mark.asyncio
async def test_org_user_count_excludes_soft_deleted(db):
    org, _ = await _seed(db)
    resp = await list_organizations(pagination=Pagination(page=1, size=50), principal=_admin(), db=db)
    row = next(r for r in resp["data"] if (r["id"] if isinstance(r, dict) else r.id) == org.id)
    uc = row["user_count"] if isinstance(row, dict) else row.user_count
    assert uc == 1, f"soft-deleted member still counted: {uc}"


@pytest.mark.asyncio
async def test_group_member_list_excludes_soft_deleted(db):
    _, grp = await _seed(db)
    resp = await list_memberships(grp.id, principal=_admin(), db=db)
    names = [(m["user_name"] if isinstance(m, dict) else getattr(m, "user_name", None)) for m in resp["data"]]
    assert len(resp["data"]) == 1 and "Ghost" not in [n for n in names if n]
