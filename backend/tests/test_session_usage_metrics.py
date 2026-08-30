"""GET /monitoring/sessions/{id}/usage: super_admin-only; unknown session 404s before Prometheus."""
from __future__ import annotations

import pytest

from app.api.monitoring_router import session_usage
from app.auth.rbac import Principal
from app.core import ids
from app.core.errors import Forbidden, NotFound


@pytest.mark.asyncio
async def test_usage_requires_monitoring_read(db):
    member = Principal(user_id=ids.new("user"), memberships={})
    with pytest.raises(Forbidden):
        await session_usage("ses_x", principal=member, db=db)


@pytest.mark.asyncio
async def test_usage_unknown_session_404s_without_touching_prometheus(db):
    root = Principal(user_id=ids.new("user"), global_role="super_admin",
                     global_roles={"super_admin"}, memberships={})
    with pytest.raises(NotFound):
        await session_usage(ids.new("session"), principal=root, db=db)
