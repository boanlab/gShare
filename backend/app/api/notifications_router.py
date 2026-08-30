"""Notifications router. In-app notifications (self-scoped)."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends, Query, Request, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.deps import Pagination, get_current_principal, get_sse_principal
from app.api.sessions_router import _sse_events  # reuse the shared Redis pub/sub SSE generator
from app.auth.rbac import Principal
from app.core.errors import NotFound
from app.db.base import get_db
from app.db.models import Notification
from app.domain.notification_service import NOTIF_CHANNEL

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/events")
async def notification_events(
    request: Request,
    principal: Principal = Depends(get_sse_principal),
):
    """Live SSE stream of the caller's notifications: a ping event per new notification, which the
    console turns into a refetch of the list.

    EventSource cannot set headers, so authentication goes through ``?access_token=``, which
    get_current_principal accepts.
    """
    channel = NOTIF_CHANNEL.format(user_id=principal.user_id)
    return EventSourceResponse(_sse_events([channel], None, request))


def _notification_view(n: Notification) -> dict:
    payload = dict(n.payload or {})
    return {
        "id": n.id,
        "type": n.type,
        "title": payload.get("title") or n.type,
        "body": payload.get("body"),
        "read": n.read_at is not None,
        # The notification bell computes the unread count from read_at, so it is returned alongside
        # the boolean read flag.
        "read_at": n.read_at.isoformat() if n.read_at else None,
        "deleted_at": n.deleted_at.isoformat() if n.deleted_at else None,
        # Legacy credit notifications stored a flat payload (ids at top level, no "ref" key);
        # fold those extras back in so their deep links keep working.
        "ref": payload.get("ref")
        or {k: v for k, v in payload.items() if k not in ("title", "body", "params")},
        "params": payload.get("params") or {},
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


@router.get("")
async def list_notifications(
    unread: bool = False,
    include_deleted: bool = False,
    type: list[str] | None = Query(default=None),
    page: Pagination = Depends(),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """List the caller's own notifications, newest first. Self-scoped only."""
    base = select(Notification).where(Notification.user_id == principal.user_id)
    # Dismissed rows are soft-deleted: hidden from the bell, kept for the my-page log.
    if not include_deleted:
        base = base.where(Notification.deleted_at.is_(None))
    if unread:
        base = base.where(Notification.read_at.is_(None))
    if type:
        base = base.where(Notification.type.in_(type))

    total = await db.scalar(select(func.count()).select_from(base.subquery()))
    rows = (
        await db.scalars(
            base.order_by(Notification.created_at.desc())
            .limit(page.size).offset(page.offset)
        )
    ).all()

    unread_count = await db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.user_id == principal.user_id,
            Notification.read_at.is_(None),
            Notification.deleted_at.is_(None),
        )
    )
    return {
        "data": [_notification_view(n) for n in rows],
        "pagination": {
            "page": page.page, "size": page.size, "total": total or 0,
            "total_pages": ((total or 0) + page.size - 1) // page.size,
        },
        "unread_count": unread_count or 0,
    }


@router.post("/{notification_id}/read")
async def mark_read(
    notification_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Mark one notification read; idempotent no-op if already read."""
    notif = await db.get(Notification, notification_id)
    # Other users' / nonexistent notifications -> 404 (do not leak existence).
    if notif is None or notif.user_id != principal.user_id:
        raise NotFound("notification not found")
    if notif.read_at is None:
        notif.read_at = datetime.now(UTC)
        await db.commit()
    return {
        "id": notif.id,
        "read": True,
        "read_at": notif.read_at.isoformat() if notif.read_at else None,
    }


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification(
    notification_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Delete one of the caller's notifications. 404 for other users' rows (no existence leak)."""
    notif = await db.get(Notification, notification_id)
    if notif is None or notif.user_id != principal.user_id:
        raise NotFound("notification not found")
    # Soft delete: the bell hides it, the my-page notification log keeps it.
    if notif.deleted_at is None:
        notif.deleted_at = datetime.now(UTC)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("", status_code=status.HTTP_200_OK)
async def delete_all_notifications(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Delete every notification of the caller. Self-scoped by construction."""
    res = await db.execute(
        update(Notification)
        .where(Notification.user_id == principal.user_id, Notification.deleted_at.is_(None))
        .values(deleted_at=datetime.now(UTC))
    )
    await db.commit()
    return {"deleted": int(res.rowcount or 0)}


@router.post("/read-all")
async def mark_all_read(
    type: list[str] | None = Query(default=None),
    body: dict | None = Body(default=None),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Bulk mark the caller's unread notifications read, optionally bounded."""
    now = datetime.now(UTC)
    stmt = (
        update(Notification)
        .where(
            Notification.user_id == principal.user_id,
            Notification.read_at.is_(None),
            Notification.deleted_at.is_(None),
        )
        .values(read_at=now)
    )
    if type:
        stmt = stmt.where(Notification.type.in_(type))
    if body and body.get("before"):
        before = body["before"]
        if isinstance(before, str):
            before = datetime.fromisoformat(before.replace("Z", "+00:00"))
        stmt = stmt.where(Notification.created_at < before)

    result = await db.execute(stmt)
    await db.commit()
    marked = result.rowcount if result.rowcount is not None else 0

    unread_count = await db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.user_id == principal.user_id,
            Notification.read_at.is_(None),
            Notification.deleted_at.is_(None),
        )
    )
    return {"marked_read": marked, "unread_count": unread_count or 0}
