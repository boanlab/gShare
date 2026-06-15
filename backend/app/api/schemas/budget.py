"""Budget/FinOps schemas."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.api.schemas.common import ORMModel


class BudgetCreate(BaseModel):
    scope: str = Field(pattern="^(org|group)$")
    scope_id: str
    period_start: datetime
    period: str = "monthly"
    limit_credit: Decimal = Field(gt=0)
    action: str = Field(default="alert", pattern="^(alert|block)$")


class BudgetRead(ORMModel):
    id: str
    scope: str
    scope_id: str
    limit_credit: Decimal
    spent_credit: Decimal
    action: str


class BudgetForecast(BaseModel):
    projected_exhaustion_date: datetime | None = None
    burn_rate_per_day: float
