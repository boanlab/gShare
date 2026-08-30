"""budget_rollup — budget aggregation (interval 60s).

Accumulate Budget.spent_credit, evaluate 80/100% thresholds, fire BudgetAlert.

spent_credit is the net consumed credit (Σ consume + Σ settle-refund corrections) attributed to
the budget's scope within the current period window. Attribution is by SESSION, not by wallet:
billing always charges the requester's personal wallet, so scope membership comes from the
session's group (group scope) or the org's groups (org scope) via CreditTransaction.ref ==
session id. Period boundaries are the 1st-of-month 00:00 in the org timezone (default Asia/Seoul).
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

try:  # py3.9+ stdlib; fall back to UTC if tzdata is unavailable in the sandbox.
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

from sqlalchemy import func, select

from app.core.logging import get_logger
from app.db.base import get_sessionmaker
from app.db.models import Budget, CreditTransaction, Organization, Project, Session
from app.domain.budget_service import BudgetService

log = get_logger(__name__)

_CENT = Decimal("0.01")
_ZERO = Decimal("0")


def _round2(value) -> Decimal:
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _period_window(budget: Budget, org_tz: str) -> tuple[datetime, datetime]:
    """Return (period_start_utc, period_end_utc) for the budget's current monthly period.

    Monthly budgets reset on the 1st at 00:00 in the org timezone.
    We anchor on ``now`` in the org tz to find the active month, independent of how stale
    ``Budget.period_start`` is.
    """
    tz = UTC
    if ZoneInfo is not None and org_tz:
        try:
            tz = ZoneInfo(org_tz)
        except Exception:  # noqa: BLE001 — unknown tz -> UTC fallback
            tz = UTC

    now_local = datetime.now(tz)
    start_local = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # first day of next month
    if start_local.month == 12:
        end_local = start_local.replace(year=start_local.year + 1, month=1)
    else:
        end_local = start_local.replace(month=start_local.month + 1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


async def _scope_group_ids(db, budget: Budget) -> list[str]:
    """Group ids whose sessions roll up into this budget."""
    if budget.scope == "group":
        return [budget.scope_id]
    proj_rows = await db.execute(select(Project.id).where(Project.org_id == budget.scope_id))
    return [r[0] for r in proj_rows.all()]


async def _spent_in_period(db, group_ids: list[str], start: datetime, end: datetime) -> Decimal:
    """Net spend in [start, end) attributed to the scope's sessions (Σ consume + Σ settle refunds).

    Billing charges the requester's PERSONAL wallet, so wallets alone cannot scope a group/org
    budget. consume and session-refund transactions carry ref == session id; joining through the
    session's group_id is what attributes spend to the budget. consume amounts are negative and
    refunds positive, so net spend is the negated sum. Free sessions write no consume rows and
    are auto-excluded.
    """
    if not group_ids:
        return _ZERO
    result = await db.execute(
        select(func.coalesce(func.sum(CreditTransaction.amount), 0))
        .select_from(CreditTransaction)
        .join(Session, Session.id == CreditTransaction.ref)
        .where(
            Session.group_id.in_(group_ids),
            CreditTransaction.type.in_(("consume", "refund")),
            CreditTransaction.created_at >= start,
            CreditTransaction.created_at < end,
        )
    )
    net_signed = result.scalar_one() or _ZERO
    spent = -Decimal(net_signed)  # consume(-) dominates; refunds(+) reduce spend
    if spent < _ZERO:
        spent = _ZERO
    return _round2(spent)


async def run() -> None:
    """Recompute Budget.spent_credit for the current period and fire threshold alerts."""
    sessionmaker = get_sessionmaker()
    now = datetime.now(UTC)

    async with sessionmaker() as db:
        async with db.begin():
            budgets = (
                await db.execute(select(Budget).where(Budget.period_start <= now))
            ).scalars().all()

            # cache org timezone lookups (project budgets resolve via project.org_id).
            tz_by_org: dict[str, str] = {}

            async def _org_tz_for(budget: Budget) -> str:
                if budget.scope == "org":
                    org_id = budget.scope_id
                else:
                    proj = (
                        await db.execute(
                            select(Project.org_id).where(Project.id == budget.scope_id)
                        )
                    ).scalar_one_or_none()
                    org_id = proj
                if org_id is None:
                    return "Asia/Seoul"
                if org_id not in tz_by_org:
                    tz = (
                        await db.execute(
                            select(Organization.timezone).where(Organization.id == org_id)
                        )
                    ).scalar_one_or_none()
                    tz_by_org[org_id] = tz or "Asia/Seoul"
                return tz_by_org[org_id]

            updated = 0
            for budget in budgets:
                org_tz = await _org_tz_for(budget)
                start, end = _period_window(budget, org_tz)
                group_ids = await _scope_group_ids(db, budget)
                spent = await _spent_in_period(db, group_ids, start, end)
                if budget.spent_credit != spent:
                    budget.spent_credit = spent
                    updated += 1
            await db.flush()

        # threshold evaluation + BudgetAlert firing (own tx via BudgetService).
        await BudgetService(db).rollup()
        await db.commit()

    if budgets:
        log.info("budget rollup evaluated=%d updated_spent=%d", len(budgets), updated)
