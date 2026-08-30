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
    """Per-GPU-model availability bucket.

    `free`/`total` count whole cards, which under fractional sharing understates what is usable: a
    card holding one small slice is not "free" yet can still host several more sessions. The VRAM
    figures are what actually decide whether a session starts, so both are reported.
    """

    model: str
    total: int
    free: int
    free_mb: int = 0
    total_mb: int = 0


class DashboardInstances(BaseModel):
    used: int
    total: int


class MyVram(BaseModel):
    """The caller's own VRAM footprint vs their policy limit (None = unlimited)."""

    used_mb: int
    limit_mb: int | None = None


class ResourceUsage(BaseModel):
    """One compute dimension: what the caller's active sessions hold vs the policy limit
    (None = no limit configured)."""

    used: int
    limit: int | None = None


class DashboardAllocation(BaseModel):
    instances: DashboardInstances
    vram: MyVram
    # GPU core share (percent) the caller holds vs their policy limit; None = no limit configured.
    gpu_cores: ResourceUsage | None = None


class DashboardCompute(BaseModel):
    """Host compute held by the caller's active sessions — separate from GPU/credits, since
    cpu/mem/disk are quota-governed, not billed."""

    cpu: ResourceUsage
    mem_gb: ResourceUsage
    disk_gb: ResourceUsage


class DashboardPool(BaseModel):
    """A node pool the caller may place on. `id` is None for the unassigned ("shared") bucket."""

    id: str | None = None
    name: str
    kind: str
    tier: str  # group|org|shared


class DashboardSummary(BaseModel):
    """GET /dashboard/summary aggregate."""

    credit: DashboardCredit
    sessions: DashboardSessions
    vram: DashboardVram
    regions: list[DashboardRegion]
    # Node pools the caller may place on, in placement order (group → org → shared).
    pools: list[DashboardPool] = []
    allocation: DashboardAllocation
    compute: DashboardCompute
