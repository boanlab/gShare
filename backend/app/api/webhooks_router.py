"""Webhooks router. Event subscriptions."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_principal
from app.auth.rbac import Principal
from app.core import ids
from app.core.errors import DomainError, Forbidden, NotFound
from app.db.base import get_db
from app.db.models import WebhookSubscription
from app.domain.audit_service import AuditService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Valid outbound event types.
# Only events that actually have an emitter may be subscribed to — advertising an event that
# nothing fires is a silent lie to the subscriber. Add a name here TOGETHER with its
# emit_webhook_safe call site.
_VALID_EVENTS = {
    "session.status_changed",   # status_sync (pause/terminate transitions)
    "wallet.low_balance",       # billing_worker daily low-balance warning
    "node_health.event",        # internal inventory_router health callback
}


class _Unprocessable(DomainError):
    code, http = "validation_failed", 422


class WebhookCreate(BaseModel):
    url: str
    events: list[str]
    secret: str
    scope: dict | None = None
    org_id: str | None = None   # owning organization; required for an org_admin, and omitting it as super_admin means global


def _assert_webhook_org(principal: Principal, org_id: str | None) -> None:
    """Check the org_id falls inside the caller's authority. super_admin may use any, including the
    global NULL; an org_admin is limited to their own organization and cannot create a global
    one."""
    if "super_admin" in principal.global_roles:
        return
    if org_id is None or org_id not in principal.org_admin_orgs:
        raise Forbidden("not permitted: webhook outside your organization")


def _subscription_view(w: WebhookSubscription) -> dict:
    events = w.events
    if isinstance(events, dict):
        events = events.get("events", [])
    scope = None
    if w.scope:
        try:
            scope = json.loads(w.scope)
        except (ValueError, TypeError):
            scope = w.scope
    return {
        "id": w.id,
        "org_id": w.org_id,
        "url": w.url,
        "events": events or [],
        "scope": scope,
        "status": w.status,
        # Never expose the HMAC secret; report only that one is set.
        "has_secret": bool(w.secret),
        "created_at": w.created_at.isoformat() if w.created_at else None,
    }


@router.get("")
async def list_webhooks(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    principal.require(action="webhook.read")
    q = select(WebhookSubscription).order_by(WebhookSubscription.created_at.desc())
    if "super_admin" not in principal.global_roles:
        # An org_admin sees only their own organization's subscriptions, never the global ones.
        q = q.where(WebhookSubscription.org_id.in_(principal.org_admin_orgs or {"__none__"}))
    rows = (await db.scalars(q)).all()
    return {"data": [_subscription_view(w) for w in rows]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_webhook(
    body: WebhookCreate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    principal.require(action="webhook.create")
    _assert_webhook_org(principal, body.org_id)

    if not body.url.startswith("https://"):
        raise _Unprocessable("webhook url must be https")
    if not body.events:
        raise _Unprocessable("at least one event type is required")
    invalid = [e for e in body.events if e not in _VALID_EVENTS]
    if invalid:
        raise _Unprocessable("unknown event type(s)", {"invalid": invalid})
    if not (16 <= len(body.secret) <= 128):
        raise _Unprocessable("secret must be 16..128 chars")

    sub = WebhookSubscription(
        id=ids.new("webhook"),
        org_id=body.org_id,
        # The scope column is NOT NULL, so a global subscription with no scope is stored as "{}".
        scope=json.dumps(body.scope) if body.scope else "{}",
        url=body.url,
        # Store the deduplicated event list inside the JSONB column.
        events={"events": sorted(set(body.events))},
        secret=body.secret,
        status="active",
    )
    db.add(sub)
    await db.flush()
    await AuditService(db).record(
        actor=principal.user_id, action="webhook.create", target=sub.id,
        url=body.url, events=sorted(set(body.events)),
    )
    await db.commit()
    return _subscription_view(sub)


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    principal.require(action="webhook.delete")
    sub = await db.get(WebhookSubscription, webhook_id)
    if sub is None:
        raise NotFound("webhook subscription not found")
    _assert_webhook_org(principal, sub.org_id)
    await db.delete(sub)
    await AuditService(db).record(
        actor=principal.user_id, action="webhook.delete", target=webhook_id,
    )
    await db.commit()
