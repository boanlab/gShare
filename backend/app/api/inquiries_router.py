"""Inquiries (문의) — user questions with admin replies.

Visibility: the author; super_admin (all); a group_admin for their group members' inquiries.
Answering/closing requires inquiry.answer on the inquiry's group (or super_admin)."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination, get_current_principal
from app.auth.rbac import Principal, rbac_allows
from app.core import ids
from app.core.errors import Forbidden, NotFound
from app.db.base import get_db
from app.db.models import Inquiry, InquiryReply, Membership, User
from app.domain.audit_service import AuditService
from app.domain.notification_service import NotificationService

router = APIRouter(prefix="/inquiries", tags=["inquiries"])


class InquiryCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)
    # WHO should answer: "group" routes to the author's department admins (default);
    # "system" goes straight to the operators (super_admin inbox only).
    to: str = Field(default="group", pattern="^(group|system)$")


class ReplyCreate(BaseModel):
    body: str = Field(min_length=1, max_length=20000)
    close: bool = False   # answering usually resolves it; leave open for a follow-up round


def _admin_gids(principal: Principal) -> list[str]:
    return [gid for gid, role in principal.memberships.items() if role in ("group_admin", "org_admin")]


def _can_answer(principal: Principal, inq: Inquiry) -> bool:
    if "super_admin" in principal.global_roles:
        return True
    return bool(inq.group_id) and rbac_allows(principal, "inquiry.answer", group_id=inq.group_id)


def _can_read(principal: Principal, inq: Inquiry) -> bool:
    return inq.author_id == principal.user_id or _can_answer(principal, inq)


def _out(i: Inquiry, author_name: str | None = None, reply_count: int = 0) -> dict[str, Any]:
    return {
        "id": i.id, "title": i.title, "body": i.body, "status": i.status,
        "author_id": i.author_id, "author_name": author_name, "group_id": i.group_id,
        "reply_count": reply_count, "created_at": i.created_at, "updated_at": i.updated_at,
    }


@router.get("")
async def list_inquiries(
    box: str = "mine",   # mine | incoming (관리 대상)
    page: Pagination = Depends(),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Inquiry).where(Inquiry.deleted_at.is_(None))
    if box == "incoming":
        if "super_admin" in principal.global_roles:
            pass
        else:
            gids = _admin_gids(principal)
            stmt = stmt.where(Inquiry.group_id.in_(gids)) if gids else stmt.where(Inquiry.id.is_(None))
    else:
        stmt = stmt.where(Inquiry.author_id == principal.user_id)
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (await db.scalars(
        stmt.order_by(Inquiry.created_at.desc()).offset(page.offset).limit(page.size)
    )).all()
    names = {}
    if rows:
        names = dict((await db.execute(
            select(User.id, User.name).where(User.id.in_({r.author_id for r in rows}))
        )).all())
    counts: dict[str, int] = {}
    if rows:
        counts = {iid: int(c) for iid, c in (await db.execute(
            select(InquiryReply.inquiry_id, func.count()).where(
                InquiryReply.inquiry_id.in_([r.id for r in rows])
            ).group_by(InquiryReply.inquiry_id)
        )).all()}
    return {
        "data": [_out(r, names.get(r.author_id), counts.get(r.id, 0)) for r in rows],
        "pagination": {"page": page.page, "size": page.size, "total": total},
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_inquiry(
    body: InquiryCreate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    # "group" binds the inquiry to the author's department (its admins can see and answer);
    # "system" leaves it unbound, so only super_admin's inbox carries it.
    gid = next(iter(principal.memberships.keys()), None) if body.to == "group" else None
    inq = Inquiry(
        id=ids.new("inquiry"), author_id=principal.user_id, group_id=gid,
        title=body.title, body=body.body, status="open",
    )
    db.add(inq)
    await db.flush()
    await AuditService(db).record(
        actor=principal.user_id, action="inquiry.create", target=inq.id, result="ok",
        group_id=gid, name=inq.title,
    )
    # Wake the people who can answer: the department's admins, or — for a system-targeted
    # inquiry — the operators themselves.
    if body.to == "system":
        supers = [uid for (uid,) in (await db.execute(
            select(User.id).where(User.global_role == "super_admin", User.deleted_at.is_(None))
        )).all() if uid != principal.user_id]
        if supers:
            await NotificationService(db).notify(
                supers, "inquiry_created", f"Inquiry: {inq.title}",
                params={"title": inq.title}, inquiry_id=inq.id,
            )
    elif gid:
        admin_ids = [uid for (uid,) in (await db.execute(
            select(Membership.user_id).where(
                Membership.group_id == gid, Membership.role == "group_admin"
            )
        )).all()]
        if admin_ids:
            await NotificationService(db).notify(
                admin_ids, "inquiry_created", f"Inquiry: {inq.title}",
                params={"title": inq.title}, inquiry_id=inq.id,
            )
    await db.commit()
    await db.refresh(inq)
    return _out(inq)


@router.get("/{inquiry_id}")
async def get_inquiry(
    inquiry_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    inq = await db.get(Inquiry, inquiry_id)
    if inq is None or inq.deleted_at is not None:
        raise NotFound("inquiry not found", {"inquiry_id": inquiry_id})
    if not _can_read(principal, inq):
        raise Forbidden("not permitted: you can only read your own inquiries")
    replies = (await db.scalars(
        select(InquiryReply).where(InquiryReply.inquiry_id == inquiry_id)
        .order_by(InquiryReply.created_at.asc())
    )).all()
    author_ids = {r.author_id for r in replies} | {inq.author_id}
    urows = (await db.execute(
        select(User.id, User.name, User.global_role).where(User.id.in_(author_ids))
    )).all()
    names = {uid: nm for uid, nm, _ in urows}
    supers = {uid for uid, _, gr in urows if gr == "super_admin"}
    # WHO answered, by role — readers rarely know the operators' display names. super_admin
    # outranks; otherwise a group_admin of the inquiry's own department is tagged as such.
    group_admins: set[str] = set()
    if inq.group_id:
        group_admins = {uid for (uid,) in (await db.execute(
            select(Membership.user_id).where(
                Membership.group_id == inq.group_id,
                Membership.role == "group_admin",
                Membership.user_id.in_(author_ids),
            )
        )).all()}

    def _role(uid: str) -> str | None:
        if uid in supers:
            return "super_admin"
        if uid in group_admins:
            return "group_admin"
        return None

    return {
        **_out(inq, names.get(inq.author_id), len(replies)),
        "author_role": _role(inq.author_id),
        "replies": [
            {"id": r.id, "author_id": r.author_id, "author_name": names.get(r.author_id),
             "author_role": _role(r.author_id), "body": r.body, "created_at": r.created_at}
            for r in replies
        ],
    }


@router.post("/{inquiry_id}/replies", status_code=status.HTTP_201_CREATED)
async def reply_inquiry(
    inquiry_id: str,
    body: ReplyCreate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    inq = await db.get(Inquiry, inquiry_id)
    if inq is None or inq.deleted_at is not None:
        raise NotFound("inquiry not found", {"inquiry_id": inquiry_id})
    # Who replies decides what the reply MEANS: an answerer (admin) resolves someone ELSE's
    # thread; the author's plain reply is a follow-up that reopens it — even when the author
    # is an admin of their own inquiry. Only the explicit close flag resolves an own thread.
    is_author = inq.author_id == principal.user_id
    can_answer = _can_answer(principal, inq)
    if not is_author and not can_answer:
        raise Forbidden("not permitted: only administrators can answer inquiries")
    r = InquiryReply(id=ids.new("ireply"), inquiry_id=inquiry_id, author_id=principal.user_id, body=body.body)
    db.add(r)
    if can_answer and (not is_author or body.close):
        inq.status = "closed" if body.close else "answered"
        if not is_author:
            await NotificationService(db).notify(
                [inq.author_id], "inquiry_answered", f"Answered: {inq.title}",
                params={"title": inq.title}, inquiry_id=inq.id,
            )
    else:
        # A follow-up from the author reopens the thread — and tells the admins so, or the
        # reopened question sat unnoticed until someone happened to open the inbox.
        inq.status = "open"
        if inq.group_id:
            admin_ids = [uid for (uid,) in (await db.execute(
                select(Membership.user_id).where(
                    Membership.group_id == inq.group_id, Membership.role == "group_admin"
                )
            )).all()]
        else:
            admin_ids = [uid for (uid,) in (await db.execute(
                select(User.id).where(User.global_role == "super_admin", User.deleted_at.is_(None))
            )).all()]
        admin_ids = [u for u in admin_ids if u != principal.user_id]
        if admin_ids:
            await NotificationService(db).notify(
                admin_ids, "inquiry_created", f"Follow-up: {inq.title}",
                params={"title": inq.title}, inquiry_id=inq.id,
            )
    await AuditService(db).record(
        actor=principal.user_id, action="inquiry.reply", target=inq.id, result="ok", group_id=inq.group_id,
    )
    await db.commit()
    return {"id": r.id, "status": inq.status}


@router.post("/{inquiry_id}/close")
async def close_inquiry(
    inquiry_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    inq = await db.get(Inquiry, inquiry_id)
    if inq is None or inq.deleted_at is not None:
        raise NotFound("inquiry not found", {"inquiry_id": inquiry_id})
    if inq.author_id != principal.user_id and not _can_answer(principal, inq):
        raise Forbidden("not permitted")
    inq.status = "closed"
    await db.commit()
    return {"id": inq.id, "status": inq.status}


@router.delete("/{inquiry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inquiry(
    inquiry_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    inq = await db.get(Inquiry, inquiry_id)
    if inq is None or inq.deleted_at is not None:
        raise NotFound("inquiry not found", {"inquiry_id": inquiry_id})
    if inq.author_id != principal.user_id and "super_admin" not in principal.global_roles:
        raise Forbidden("not permitted: only the author or super_admin can delete an inquiry")
    inq.deleted_at = datetime.now(UTC)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
