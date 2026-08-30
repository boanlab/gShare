"""BudgetService — FinOps gate.

Order: resource policy -> budget -> hold. For the active Budget in scope (project first, else org):
if action=block and spent_credit + est > limit_credit -> BudgetExceeded (409). action=alert passes
and fires a BudgetAlert at 80/100% thresholds. Governance cap independent of balance; no real payment.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BudgetExceeded  # noqa: F401  (raised by gate)
from app.core.logging import get_logger
from app.db.models import Budget, BudgetAlert
from app.domain.notification_service import NotificationService

log = get_logger(__name__)

_CENT = Decimal("0.01")
_ZERO = Decimal("0")
_THRESHOLDS = (80, 100)


def _round2(value) -> Decimal:
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _as_decimal(estimate) -> Decimal:
    """Coerce the estimate (Decimal/number/object with est_cost) to a Decimal."""
    if estimate is None:
        return _ZERO
    if isinstance(estimate, Decimal):
        return estimate
    if isinstance(estimate, (int, float, str)):
        return Decimal(str(estimate))
    # DTO carrying the estimate. The scheduler's _Estimate.hold_amount is what a new session adds to
    # the budget; leaving it empty makes the gate evaluate at est=0 and the new session never counts
    # against the budget.
    for attr in ("est_cost", "estimated_credit", "hold_amount", "credit_per_hour", "amount", "credit"):
        v = getattr(estimate, attr, None)
        if v is not None:
            return Decimal(str(v))
    return _ZERO


class BudgetService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def gate(self, scope_chain: list[tuple[str, str]], estimate) -> None:
        """Evaluate the budget gate before crediting hold.

        scope_chain is ordered most-specific-first, e.g. [("group", prj_id), ("org", org_id)].
        For every active Budget in the chain: if action=block and spent+est > limit -> 409
        budget_exceeded (hold NOT taken). Either way evaluate 80/100% thresholds and fire
        BudgetAlert (alert never blocks). Both present => both must pass.
        """
        est = _round2(_as_decimal(estimate))
        # Free sessions (est_cost == 0) pass the gate with no charge.
        for scope, scope_id in scope_chain:
            budget = await self._active_budget(scope, scope_id)
            if budget is None:
                continue
            projected = budget.spent_credit + est
            if budget.action == "block" and projected > budget.limit_credit:
                await self._notify_budget(
                    budget, "budget_exceeded", "Blocked: budget exceeded",
                    f"The session was blocked because the {budget.scope} budget limit was exceeded.",
                    params={"scope": budget.scope},
                )
                raise BudgetExceeded(
                    "budget cap exceeded",
                    {
                        "budget_id": budget.id,
                        "scope": budget.scope,
                        "scope_id": budget.scope_id,
                        "limit_credit": str(budget.limit_credit),
                        "spent_credit": str(budget.spent_credit),
                        "estimate": str(est),
                    },
                )
            # alert (and block under cap): fire 80/100% thresholds, no blocking.
            await self._fire_alerts(budget, projected)

    async def rollup(self) -> None:
        """Aggregate spent_credit + evaluate thresholds; called by budget_rollup worker.

        Re-evaluate 80/100% thresholds against the current spent_credit for every active budget
        and fire/debounce BudgetAlert rows. spent_credit accumulation itself is driven
        by consume/settle aggregation (see budget_rollup.run); this method is the threshold
        evaluation pass and is safe to call repeatedly (debounced via last_fired_at).
        """
        now = datetime.now(UTC)
        result = await self.db.execute(
            select(Budget).where(Budget.period_start <= now)
        )
        for budget in result.scalars().all():
            await self._fire_alerts(budget, budget.spent_credit)

    # ── internal helpers ──
    async def _active_budget(self, scope: str, scope_id: str) -> Budget | None:
        """Most recent active Budget for (scope, scope_id) whose period has started."""
        now = datetime.now(UTC)
        result = await self.db.execute(
            select(Budget)
            .where(
                Budget.scope == scope,
                Budget.scope_id == scope_id,
                Budget.period_start <= now,
            )
            .order_by(Budget.period_start.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _fire_alerts(self, budget: Budget, spent: Decimal) -> None:
        """Fire BudgetAlert for each crossed 80/100% threshold, debounced by last_fired_at.

        usage_pct = spent / limit. Within a budget period we fire each threshold at most once;
        last_fired_at >= period_start means already fired this period debounce.
        """
        if budget.limit_credit is None or budget.limit_credit <= _ZERO:
            return
        usage = (Decimal(spent) / Decimal(budget.limit_credit)) * Decimal(100)
        now = datetime.now(UTC)
        period_start = budget.period_start
        if period_start is not None and period_start.tzinfo is None:
            period_start = period_start.replace(tzinfo=UTC)

        for pct in _THRESHOLDS:
            if usage < Decimal(pct):
                continue
            alert = await self._get_alert(budget.id, pct)
            if alert is None:
                # No alert rule configured for this threshold -> nothing to fire.
                continue
            last = alert.last_fired_at
            if last is not None and last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            already_fired_this_period = (
                last is not None and period_start is not None and last >= period_start
            )
            if already_fired_this_period:
                continue
            alert.last_fired_at = now
            await self.db.flush()
            await self._notify_budget(
                budget, "budget_alert", f"Budget alert at {pct}%",
                f"The {budget.scope} budget is now over {pct}% used.",
                params={"pct": pct, "scope": budget.scope},
            )
            log.info(
                "budget alert fired budget=%s threshold=%d%% usage=%.1f%%",
                budget.id, pct, float(usage),
            )

    async def _notify_budget(
        self, budget: Budget, ntype: str, title: str, body: str, params: dict | None = None
    ) -> None:
        """Recipients of a budget alert or overrun: the scope's administrators (project or
        organization) plus the system and billing administrators."""
        notifier = NotificationService(self.db)
        recips = (
            await notifier.group_admins(budget.scope_id)
            if budget.scope == "group"
            else await notifier.org_admins(budget.scope_id)
        )
        recips += await notifier.system_admins()
        await notifier.notify(
            recips, ntype, title, body, params=params,
            budget_id=budget.id, scope=budget.scope, scope_id=budget.scope_id,
        )

    async def _get_alert(self, budget_id: str, threshold_pct: int) -> BudgetAlert | None:
        result = await self.db.execute(
            select(BudgetAlert).where(
                BudgetAlert.budget_id == budget_id,
                BudgetAlert.threshold_pct == threshold_pct,
            )
        )
        return result.scalar_one_or_none()
