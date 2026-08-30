"""webhook_dispatcher — delivers the webhook outbox. Interval 15s.

Subscriptions used to be stored and listed with no dispatcher at all — a silent lie to any
operator who configured one. Emitters (status_sync and friends) insert WebhookDelivery rows via
app.domain.webhook_outbox in the same transaction as the domain change; this worker POSTs each
row with an HMAC signature and exponential backoff, and flips a subscription to ``failing``
after too many consecutive failures (visible in the admin list; it keeps trying).
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from app.core.logging import get_logger
from app.db.base import get_sessionmaker
from app.db.models import WebhookDelivery, WebhookSubscription

log = get_logger(__name__)

MAX_ATTEMPTS = 3
_BACKOFF_SEC = (60, 300, 900)
_BATCH = 50
_TIMEOUT = httpx.Timeout(10.0)
# Consecutive failed deliveries before the subscription is marked failing.
_FAILING_THRESHOLD = 10


def _sign(secret: str | None, body: bytes) -> dict[str, str]:
    if not secret:
        return {}
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {"X-GShare-Signature": f"sha256={digest}"}


async def run() -> None:
    now = datetime.now(UTC)
    maker = get_sessionmaker()
    async with maker() as db:
        rows = (
            await db.scalars(
                select(WebhookDelivery)
                .where(
                    WebhookDelivery.status == "pending",
                    (WebhookDelivery.next_attempt_at.is_(None))
                    | (WebhookDelivery.next_attempt_at <= now),
                )
                .order_by(WebhookDelivery.created_at)
                .limit(_BATCH)
            )
        ).all()
        if not rows:
            return
        subs = {
            s.id: s
            for s in (
                await db.scalars(
                    select(WebhookSubscription).where(
                        WebhookSubscription.id.in_({r.subscription_id for r in rows})
                    )
                )
            ).all()
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for row in rows:
                sub = subs.get(row.subscription_id)
                if sub is None:
                    row.status = "failed"
                    row.last_error = "subscription deleted"
                    continue
                body = json.dumps(
                    {"event": row.event, "delivery_id": row.id, "payload": row.payload}
                ).encode()
                try:
                    resp = await client.post(
                        sub.url, content=body,
                        headers={"Content-Type": "application/json", **_sign(sub.secret, body)},
                    )
                    ok = 200 <= resp.status_code < 300
                    error = None if ok else f"HTTP {resp.status_code}"
                except Exception as exc:  # noqa: BLE001 — every network failure is a retry case
                    ok, error = False, str(exc)[:200]
                row.attempts += 1
                if ok:
                    row.status = "delivered"
                    row.last_error = None
                    if sub.status == "failing":
                        sub.status = "active"
                elif row.attempts >= MAX_ATTEMPTS:
                    row.status = "failed"
                    row.last_error = error
                else:
                    row.next_attempt_at = now + timedelta(
                        seconds=_BACKOFF_SEC[min(row.attempts - 1, len(_BACKOFF_SEC) - 1)]
                    )
                    row.last_error = error
        # Mark subscriptions with a long failure streak so the admin list shows the problem.
        for sub in subs.values():
            recent_failed = await db.scalar(
                select(WebhookDelivery.id)
                .where(
                    WebhookDelivery.subscription_id == sub.id,
                    WebhookDelivery.status == "failed",
                )
                .order_by(WebhookDelivery.created_at.desc())
                .offset(_FAILING_THRESHOLD - 1)
                .limit(1)
            )
            if recent_failed is not None and sub.status == "active":
                sub.status = "failing"
        await db.commit()
