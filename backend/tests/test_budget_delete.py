"""Budget delete must take its auto-created alert rows with it (P1 regression)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.api.budgets_router import create_budget, delete_budget
from app.api.schemas.budget import BudgetCreate
from app.auth.rbac import Principal
from app.db.models import Budget, BudgetAlert, Project


def _root() -> Principal:
    return Principal(user_id="root", global_roles={"super_admin"}, memberships={})


@pytest.mark.asyncio
async def test_delete_budget_removes_alert_children(db):
    db.add(Project(id="g1", org_id="o1", name="g"))
    await db.commit()
    out = await create_budget(
        BudgetCreate(scope="group", scope_id="g1",
                     period_start=datetime(2026, 8, 1, tzinfo=UTC),
                     period="monthly", limit_credit="100", action="alert"),
        principal=_root(), db=db,
    )
    bid = out.id if hasattr(out, "id") else out["id"]
    alerts = (await db.scalars(select(BudgetAlert).where(BudgetAlert.budget_id == bid))).all()
    assert len(alerts) >= 1, "budget create should seed alert rows"
    await delete_budget(bid, principal=_root(), db=db)
    assert await db.get(Budget, bid) is None
    left = (await db.scalars(select(BudgetAlert).where(BudgetAlert.budget_id == bid))).all()
    assert left == []
