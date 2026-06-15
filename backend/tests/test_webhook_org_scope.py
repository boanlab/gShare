"""Regression: webhook subscriptions are org-scoped.

org_admin manages only its own organization's subscriptions; global (org_id NULL) subscriptions are
super_admin only. Previously webhook.* was matrix-scoped to org_admin with a group_id-less
require(), so any org_admin could list/create/delete every tenant's webhooks. """
from __future__ import annotations

import pytest

from app.api.webhooks_router import (
    WebhookCreate,
    create_webhook,
    delete_webhook,
    list_webhooks,
)
from app.auth.rbac import Principal
from app.core.errors import Forbidden
from app.db.models import WebhookSubscription

pytestmark = pytest.mark.asyncio

_SECRET = "x" * 16


def _org_admin(org: str) -> Principal:
    return Principal(user_id="u_oa", global_roles=set(), org_admin_orgs={org})


def _super() -> Principal:
    return Principal(user_id="u_super", global_roles={"super_admin"})


def _sub(sid: str, org_id: str | None) -> WebhookSubscription:
    return WebhookSubscription(
        id=sid, org_id=org_id, scope="{}", url=f"https://{sid}",
        events={"events": ["budget.exceeded"]}, secret=_SECRET, status="active",
    )


async def _seed(db) -> None:
    async with db.begin():
        db.add_all([_sub("wbh_A", "org_A"), _sub("wbh_B", "org_B"), _sub("wbh_G", None)])


async def test_list_scoped_to_own_org(db):
    await _seed(db)
    res = await list_webhooks(principal=_org_admin("org_A"), db=db)
    assert {w["id"] for w in res["data"]} == {"wbh_A"}  # not org_B, not the global one


async def test_super_sees_all(db):
    await _seed(db)
    res = await list_webhooks(principal=_super(), db=db)
    assert {w["id"] for w in res["data"]} == {"wbh_A", "wbh_B", "wbh_G"}


async def test_delete_cross_org_denied(db):
    await _seed(db)
    with pytest.raises(Forbidden):
        await delete_webhook("wbh_B", principal=_org_admin("org_A"), db=db)


async def test_create_outside_org_denied(db):
    other = WebhookCreate(url="https://x", events=["budget.exceeded"], secret=_SECRET, org_id="org_B")
    with pytest.raises(Forbidden):
        await create_webhook(body=other, principal=_org_admin("org_A"), db=db)
    glob = WebhookCreate(url="https://x", events=["budget.exceeded"], secret=_SECRET, org_id=None)
    with pytest.raises(Forbidden):  # global is super-only
        await create_webhook(body=glob, principal=_org_admin("org_A"), db=db)


async def test_create_own_org_ok(db):
    body = WebhookCreate(url="https://x", events=["budget.exceeded"], secret=_SECRET, org_id="org_A")
    out = await create_webhook(body=body, principal=_org_admin("org_A"), db=db)
    assert out["org_id"] == "org_A"
