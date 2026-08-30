"""Notices (공지) — announcements from super_admin (global) or group_admin (their group).

Visibility: a global notice reaches everyone; a group notice reaches that group's members
and super_admin only (tenant boundary — mirrored in tests)."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination, get_current_principal
from app.auth.rbac import Principal, rbac_allows
from app.core import ids
from app.core.errors import Forbidden, NotFound
from app.db.base import get_db
from app.db.models import Membership, Notice, Project, User
from app.domain.audit_service import AuditService
from app.domain.notification_service import NotificationService

router = APIRouter(prefix="/notices", tags=["notices"])


class NoticeCreate(BaseModel):
    scope: str = Field(pattern="^(global|group)$")
    group_id: str | None = None
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=20000)
    pinned: bool = False
    # The author decides whether publishing pings the audience (default keeps the old behaviour).
    notify: bool = True


class NoticePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, max_length=20000)
    pinned: bool | None = None


def _out(n: Notice, author_name: str | None, group_name: str | None) -> dict[str, Any]:
    return {
        "id": n.id, "scope": n.scope, "group_id": n.group_id, "group_name": group_name,
        "title": n.title, "body": n.body, "pinned": n.pinned,
        "author_id": n.author_id, "author_name": author_name,
        "created_at": n.created_at, "updated_at": n.updated_at,
    }


def _visible_filter(principal: Principal, admin_view: bool):
    """Reader visibility: global notices plus the caller's OWN departments' notices.

    super_admin bypasses the filter only in the ADMIN view (공지 관리): their user-mode
    board must read like any member's — a department's internal notice does not belong on
    the operator's personal feed."""
    if admin_view and "super_admin" in principal.global_roles:
        return None
    gids = list(principal.memberships.keys())
    return or_(
        Notice.scope == "global",
        Notice.group_id.in_(gids) if gids else False,
    )


@router.get("")
async def list_notices(
    page: Pagination = Depends(),
    admin_view: bool = Query(default=False, alias="all"),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    admin_view = admin_view is True
    stmt = select(Notice).where(Notice.deleted_at.is_(None))
    vis = _visible_filter(principal, admin_view)
    if vis is not None:
        stmt = stmt.where(vis)
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (await db.scalars(
        stmt.order_by(Notice.pinned.desc(), Notice.created_at.desc())
        .offset(page.offset).limit(page.size)
    )).all()
    author_ids = {n.author_id for n in rows}
    names = dict((await db.execute(select(User.id, User.name).where(User.id.in_(author_ids)))).all()) if author_ids else {}
    gids = {n.group_id for n in rows if n.group_id}
    gnames = dict((await db.execute(select(Project.id, Project.name).where(Project.id.in_(gids)))).all()) if gids else {}
    return {
        "data": [_out(n, names.get(n.author_id), gnames.get(n.group_id)) for n in rows],
        "pagination": {"page": page.page, "size": page.size, "total": total},
    }


async def _load(db: AsyncSession, notice_id: str) -> Notice:
    n = await db.get(Notice, notice_id)
    if n is None or n.deleted_at is not None:
        raise NotFound("notice not found", {"notice_id": notice_id})
    return n


def _assert_can_manage(principal: Principal, n_scope: str, n_group: str | None) -> None:
    if "super_admin" in principal.global_roles:
        return
    if n_scope == "group" and n_group and rbac_allows(principal, "notice.create", group_id=n_group):
        return
    raise Forbidden("not permitted: only the posting scope's administrators can manage this notice")


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_notice(
    body: NoticeCreate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    if body.scope == "group" and not body.group_id:
        raise NotFound("group_id required for a group notice", {})
    _assert_can_manage(principal, body.scope, body.group_id)
    n = Notice(
        id=ids.new("notice"), scope=body.scope,
        group_id=body.group_id if body.scope == "group" else None,
        title=body.title, body=body.body, pinned=body.pinned, author_id=principal.user_id,
    )
    db.add(n)
    await db.flush()
    await AuditService(db).record(
        actor=principal.user_id, action="notice.create", target=n.id, result="ok",
        group_id=n.group_id, name=n.title,
    )
    # Tell the audience once, at publish — IF the author asked to: everyone for global, the
    # group's members otherwise.
    if not body.notify:
        await db.commit()
        await db.refresh(n)
        return _out(n, None, None)
    if body.scope == "group" and n.group_id:
        uids = [uid for (uid,) in (await db.execute(
            select(Membership.user_id).where(Membership.group_id == n.group_id).distinct()
        )).all()]
    else:
        uids = [uid for (uid,) in (await db.execute(select(User.id).where(User.deleted_at.is_(None)))).all()]
    await NotificationService(db).notify(
        [u for u in uids if u != principal.user_id], "notice_posted",
        f"Notice: {n.title}", params={"title": n.title}, notice_id=n.id,
    )
    await db.commit()
    await db.refresh(n)
    return _out(n, None, None)


@router.patch("/{notice_id}")
async def update_notice(
    notice_id: str,
    body: NoticePatch,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    n = await _load(db, notice_id)
    _assert_can_manage(principal, n.scope, n.group_id)
    if body.title is not None:
        n.title = body.title
    if body.body is not None:
        n.body = body.body
    if body.pinned is not None:
        n.pinned = body.pinned
    await AuditService(db).record(
        actor=principal.user_id, action="notice.update", target=n.id, result="ok", group_id=n.group_id,
    )
    await db.commit()
    await db.refresh(n)
    return _out(n, None, None)


@router.delete("/{notice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notice(
    notice_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    n = await _load(db, notice_id)
    _assert_can_manage(principal, n.scope, n.group_id)
    n.deleted_at = datetime.now(UTC)
    await AuditService(db).record(
        actor=principal.user_id, action="notice.delete", target=n.id, result="ok", group_id=n.group_id,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
