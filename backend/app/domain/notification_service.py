"""NotificationService — the shared producer for notifications.

Creates Notification rows of (user_id, type, payload{title, body, ref}). The caller's unit of work
owns the commit. Helpers for resolving recipients — project and organization administrators, system
administrators — live here too.
"""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ids
from app.core.redis import get_redis
from app.db.models import Membership, Notification, Project, User

# Live push channel: a per-user ping the console's EventSource receives and turns into a refetch.
NOTIF_CHANNEL = "notifications:{user_id}"


class NotificationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def notify(
        self,
        user_ids: Iterable[str],
        type: str,
        title: str,
        body: str | None = None,
        params: dict | None = None,
        **ref,
    ) -> None:
        """Add a notification for the given users, deduplicated. Empty and None recipients are
        ignored.

        `title`/`body` are the English fallback text; `params` carries the structured values the
        console interpolates into its own locale template (notif.<type>.title/body)."""
        payload: dict = {"title": title}
        if body is not None:
            payload["body"] = body
        if ref:
            payload["ref"] = ref
        if params:
            payload["params"] = params
        recipients = {u for u in user_ids if u}
        for uid in recipients:
            self.db.add(
                Notification(id=ids.new("notification"), user_id=uid, type=type, payload=payload)
            )
        # Ping each recipient's channel. Best-effort: a Redis failure must not stop the notification
        # itself from being created.
        if recipients:
            try:
                r = get_redis()
                # No "event" key, so the shared _sse_events helper emits the default "message" event
                # and the browser receives it through onmessage.
                for uid in recipients:
                    await r.publish(NOTIF_CHANNEL.format(user_id=uid), '{"kind":"notification"}')
            except Exception:  # noqa: BLE001
                pass

    async def group_admins(self, group_id: str | None) -> list[str]:
        if not group_id:
            return []
        rows = await self.db.scalars(
            select(Membership.user_id).where(
                Membership.group_id == group_id,
                Membership.role.in_(["group_admin", "org_admin"]),
            )
        )
        return list(rows.all())

    async def org_admins(self, org_id: str | None) -> list[str]:
        if not org_id:
            return []
        rows = await self.db.scalars(
            select(Membership.user_id)
            .join(Project, Project.id == Membership.group_id)
            .where(Project.org_id == org_id, Membership.role == "org_admin")
        )
        return list(rows.all())

    async def system_admins(self) -> list[str]:
        rows = await self.db.scalars(
            select(User.id).where(
                User.global_role == "super_admin",
                User.deleted_at.is_(None),
            )
        )
        return list(rows.all())
