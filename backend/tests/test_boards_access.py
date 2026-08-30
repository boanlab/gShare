"""Visibility boundaries for notices (5-11): a group notice must reach only that group's
members and super_admin — and only group_admin+ may post one."""
from __future__ import annotations

import pytest

from app.api.notices_router import NoticeCreate, create_notice, list_notices
from app.auth.rbac import Principal
from app.core.errors import Forbidden
from app.db.models import Membership, Project, User


class _Page:
    offset = 0
    size = 100
    page = 1


def _p(uid: str, *, super_admin: bool = False, memberships: dict | None = None) -> Principal:
    return Principal(
        user_id=uid,
        global_roles={"super_admin"} if super_admin else set(),
        memberships=memberships or {},
    )


async def _seed(db):
    db.add_all([
        User(id="root", email="r@x", name="Root"),
        User(id="adm", email="a@x", name="Adm"),
        User(id="mem", email="m@x", name="Mem"),
        User(id="out", email="o@x", name="Out"),
        Project(id="eng", org_id="org1", name="Eng"),
        Membership(id="m1", user_id="adm", group_id="eng", role="group_admin"),
        Membership(id="m2", user_id="mem", group_id="eng", role="member"),
    ])
    await db.commit()


@pytest.mark.asyncio
async def test_group_notice_visibility(db):
    await _seed(db)
    # the group admin posts to their group
    await create_notice(
        NoticeCreate(scope="group", group_id="eng", title="hi", body="b"),
        principal=_p("adm", memberships={"eng": "group_admin"}), db=db,
    )
    # member sees it; outsider does not; super_admin's USER view hides it too — the
    # operator's personal feed reads like any member's. The ADMIN view carries it.
    for caller, expect in ((_p("mem", memberships={"eng": "member"}), 1),
                           (_p("out"), 0),
                           (_p("root", super_admin=True), 0)):
        out = await list_notices(page=_Page(), principal=caller, db=db)
        assert len(out["data"]) == expect, caller.user_id
    out = await list_notices(page=_Page(), admin_view=True, principal=_p("root", super_admin=True), db=db)
    assert len(out["data"]) == 1


@pytest.mark.asyncio
async def test_member_cannot_post_group_notice(db):
    await _seed(db)
    with pytest.raises(Forbidden):
        await create_notice(
            NoticeCreate(scope="group", group_id="eng", title="x", body=""),
            principal=_p("mem", memberships={"eng": "member"}), db=db,
        )


@pytest.mark.asyncio
async def test_only_super_posts_global(db):
    await _seed(db)
    with pytest.raises(Forbidden):
        await create_notice(
            NoticeCreate(scope="global", title="x", body=""),
            principal=_p("adm", memberships={"eng": "group_admin"}), db=db,
        )
    out = await create_notice(
        NoticeCreate(scope="global", title="x", body=""),
        principal=_p("root", super_admin=True), db=db,
    )
    assert out["scope"] == "global"
