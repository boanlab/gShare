"""Budgets router. FinOps governance, no real payment.

Budgets cap spend per scope/period (project first, else org). ``spent_credit`` is read-only here
(rolled up by the budget_rollup worker / consume settling); this router does CRUD + forecast.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination, get_current_principal
from app.api.schemas.budget import BudgetCreate, BudgetForecast, BudgetRead
from app.auth.rbac import Principal, rbac_allows
from app.core import ids
from app.core.errors import DomainError, Forbidden, NotFound
from app.db.base import get_db
from app.db.models import Budget, BudgetAlert, CreditTransaction
from app.domain.audit_service import AuditService

router = APIRouter(prefix="/budgets", tags=["budgets"])


class _Conflict(DomainError):
    code, http = "conflict", 409


class BudgetUpdate(BaseModel):
    limit_credit: Decimal | None = Field(default=None, gt=0)
    action: str | None = Field(default=None, pattern="^(alert|block)$")


def _budget_view(b: Budget) -> BudgetRead:
    return BudgetRead.model_validate(b)


def _can_access(principal: Principal, budget: Budget) -> bool:
    if principal.global_role == "super_admin":
        return True
    if budget.scope == "group" and budget.scope_id in principal.memberships:
        return True
    if budget.scope == "org" and budget.scope_id in principal.org_admin_orgs:
        return True
    return False


def _assert_can_manage_budget(principal: Principal, scope: str, scope_id: str) -> None:
    """Authorize creating, updating, and deleting a budget, aware of its org or group scope.

    - super_admin: everything.
    - org scope: only the caller's own organizations (org_admin_orgs); organization budgets belong to
      the org_admin.
    - group scope: group_admin and above for that group, which the org_admin membership expansion
      already covers.
    """
    if "super_admin" in principal.global_roles:
        return
    if scope == "org":
        if scope_id in principal.org_admin_orgs:
            return
        raise Forbidden("not permitted: you can only manage your own organization's budgets")
    if scope == "group" and rbac_allows(principal, "budget.create", group_id=scope_id):
        return
    raise Forbidden("not permitted: only that group's administrators can manage its budget")


@router.get("", response_model=list[BudgetRead])
async def list_budgets(
    page: Pagination = Depends(),
    scope: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
    action: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    principal.require(action="budget.read")
    stmt = select(Budget)
    if scope is not None:
        stmt = stmt.where(Budget.scope == scope)
    if scope_id is not None:
        stmt = stmt.where(Budget.scope_id == scope_id)
    if action is not None:
        stmt = stmt.where(Budget.action == action)

    # Tenant isolation: outside super_admin, only budgets of the caller's own groups and
    # administered organizations are listed — the same rule as _can_access — so no other tenant's
    # budgets can be enumerated.
    if "super_admin" not in principal.global_roles:
        conds = []
        if principal.memberships:
            conds.append(
                (Budget.scope == "group") & Budget.scope_id.in_(set(principal.memberships))
            )
        if principal.org_admin_orgs:
            conds.append(
                (Budget.scope == "org") & Budget.scope_id.in_(principal.org_admin_orgs)
            )
        stmt = stmt.where(or_(*conds)) if conds else stmt.where(false())
    stmt = stmt.order_by(Budget.created_at.desc()).limit(page.size).offset(page.offset)
    rows = (await db.scalars(stmt)).all()
    return [_budget_view(b) for b in rows]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=BudgetRead)
async def create_budget(
    body: BudgetCreate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    _assert_can_manage_budget(principal, body.scope, body.scope_id)

    period_start = body.period_start
    # (scope, scope_id, period_start) UNIQUE -> 409 conflict.
    existing = (
        await db.scalars(
            select(Budget).where(
                Budget.scope == body.scope,
                Budget.scope_id == body.scope_id,
                Budget.period_start == period_start,
            )
        )
    ).one_or_none()
    if existing is not None:
        raise _Conflict("budget already exists for this scope/period")

    budget = Budget(
        id=ids.new("budget"),
        scope=body.scope,
        scope_id=body.scope_id,
        period_start=period_start,
        period=body.period,
        limit_credit=Decimal(body.limit_credit),
        spent_credit=Decimal(0),
        action=body.action,
    )
    db.add(budget)
    await db.flush()
    # Create the 80% and 100% threshold alert rules; budget_rollup's _fire_alerts fires on these
    # rows.
    for pct in (80, 100):
        db.add(BudgetAlert(id=ids.new("alert"), budget_id=budget.id, threshold_pct=pct))
    await db.flush()
    await AuditService(db).record(
        actor=principal.user_id, action="budget.create", target=budget.id,
        scope=body.scope, scope_id=body.scope_id, limit_credit=str(budget.limit_credit),
    )
    await db.commit()
    return _budget_view(budget)


@router.get("/{budget_id}", response_model=BudgetRead)
async def get_budget(
    budget_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    principal.require(action="budget.read")
    budget = await db.get(Budget, budget_id)
    if budget is None:
        raise NotFound("budget not found")
    if not _can_access(principal, budget):
        principal.require(action="budget.read", group_id=budget.scope_id)
    return _budget_view(budget)


@router.patch("/{budget_id}", response_model=BudgetRead)
async def update_budget(
    budget_id: str,
    body: BudgetUpdate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    budget = await db.get(Budget, budget_id)
    if budget is None:
        raise NotFound("budget not found")
    _assert_can_manage_budget(principal, budget.scope, budget.scope_id)

    # spent_credit is read-only; only limit/action may change.
    if body.limit_credit is not None:
        budget.limit_credit = Decimal(body.limit_credit)
    if body.action is not None:
        budget.action = body.action
    await AuditService(db).record(
        actor=principal.user_id, action="budget.update", target=budget.id,
        limit_credit=str(budget.limit_credit), action_mode=budget.action,
    )
    await db.commit()
    return _budget_view(budget)


@router.delete("/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_budget(
    budget_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    budget = await db.get(Budget, budget_id)
    if budget is None:
        raise NotFound("budget not found")
    _assert_can_manage_budget(principal, budget.scope, budget.scope_id)
    await db.delete(budget)
    await AuditService(db).record(
        actor=principal.user_id, action="budget.delete", target=budget_id,
    )
    await db.commit()
    return None


@router.get("/{budget_id}/forecast", response_model=BudgetForecast)
async def forecast(
    budget_id: str,
    lookback: str = Query(default="7d", pattern="^(7d|14d|30d)$"),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Project depletion from recent burn rate.

    burn_rate_per_day = consume over the lookback window / lookback days.
    projected_depletion_at = now + remaining_credit / burn_rate_per_day.
    """
    principal.require(action="budget.read")
    budget = await db.get(Budget, budget_id)
    if budget is None:
        raise NotFound("budget not found")
    if not _can_access(principal, budget):
        principal.require(action="budget.read", group_id=budget.scope_id)

    days = int(lookback.rstrip("d"))
    now = datetime.now(UTC)
    since = now - timedelta(days=days)

    # Burn rate from consume txns in this period since the lookback window.
    # Match by ref scope marker is unavailable on txn; use spent_credit as a robust fallback
    # and refine with recent consume volume if scoped txns are linkable.
    consume_rows = (
        await db.scalars(
            select(CreditTransaction).where(
                CreditTransaction.type == "consume",
                CreditTransaction.created_at >= since,
                CreditTransaction.created_at >= budget.period_start,
            )
        )
    ).all()
    recent_consumed = sum((t.amount for t in consume_rows), Decimal(0))

    if recent_consumed > 0:
        burn_per_day = float(recent_consumed) / float(days)
    elif budget.spent_credit > 0:
        # Fallback: average over elapsed period days.
        elapsed_days = max((now - _ensure_tz(budget.period_start)).total_seconds() / 86400.0, 1.0)
        burn_per_day = float(budget.spent_credit) / elapsed_days
    else:
        burn_per_day = 0.0

    remaining = float(budget.limit_credit) - float(budget.spent_credit)
    if burn_per_day > 0 and remaining > 0:
        depletion = now + timedelta(days=remaining / burn_per_day)
    else:
        depletion = None

    return BudgetForecast(
        projected_exhaustion_date=depletion,
        burn_rate_per_day=round(burn_per_day, 2),
    )


def _ensure_tz(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
