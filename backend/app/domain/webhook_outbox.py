"""Webhook outbox emitter: insert delivery rows for every matching subscription.

Called inside the emitter's own transaction so the delivery row commits atomically with the
domain change (the transactional-outbox pattern). The dispatcher worker does the POSTing.
"""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ids
from app.db.models import WebhookDelivery, WebhookSubscription


def _events_of(sub: WebhookSubscription) -> list[str]:
    events = sub.events
    if isinstance(events, dict):
        events = events.get("events", [])
    return list(events or [])


async def emit_webhook(
    db: AsyncSession, event: str, payload: dict, org_id: str | None = None
) -> None:
    """Queue ``event`` for every active subscription that listens to it.

    Global subscriptions (org_id NULL) receive everything; org-scoped ones only their own
    organization's events. Best-effort by design at the emitter: failures to enqueue must not
    fail the domain transaction, so callers wrap this in try/except via emit_webhook_safe.
    """
    subs = (
        await db.scalars(
            select(WebhookSubscription).where(
                WebhookSubscription.status.in_(("active", "failing")),
                or_(
                    WebhookSubscription.org_id.is_(None),
                    WebhookSubscription.org_id == org_id if org_id else WebhookSubscription.org_id.is_(None),
                ),
            )
        )
    ).all()
    for sub in subs:
        if event not in _events_of(sub):
            continue
        db.add(WebhookDelivery(
            id=ids.new("delivery"),
            subscription_id=sub.id,
            event=event,
            payload=payload,
        ))


async def emit_webhook_safe(
    db: AsyncSession, event: str, payload: dict, org_id: str | None = None
) -> None:
    """emit_webhook that never raises — the domain change must not fail on outbox trouble."""
    try:
        await emit_webhook(db, event, payload, org_id)
    except Exception:  # noqa: BLE001 — advisory channel only
        from app.core.logging import get_logger

        get_logger(__name__).exception("webhook outbox emit failed event=%s", event)
