"""User dashboard summary response schema."""
from __future__ import annotations

from pydantic import BaseModel


class DashboardCredit(BaseModel):
    """Owner wallet snapshot; all None when the user has no wallet."""

    available: float | None = None
    balance: float | None = None
    reserved: float | None = None


class DashboardSessions(BaseModel):
    running: int
    active: int


class DashboardVram(BaseModel):
    used_mb: int
    total_mb: int


class DashboardRegion(BaseModel):
    """Per-GPU-model availability bucket."""

    model: str
    total: int
    free: int


class DashboardInstances(BaseModel):
    used: int
    total: int


class DashboardAllocation(BaseModel):
    instances: DashboardInstances
    vram: DashboardVram


class DashboardSummary(BaseModel):
    """GET /dashboard/summary aggregate."""

    credit: DashboardCredit
    sessions: DashboardSessions
    vram: DashboardVram
    regions: list[DashboardRegion]
    allocation: DashboardAllocation
