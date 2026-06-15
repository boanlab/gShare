"""Infra/monitor read schemas (response_model for infra_router endpoints).

These mirror the EXACT dict shapes the handlers already return so OpenAPI is typed
and FastAPI serialization does not 500. Money fields are stringified Decimals
(wallet money style); counts/util are plain numbers.
"""
from __future__ import annotations

from pydantic import BaseModel


# ── GET /nodes ──
class NodeRow(BaseModel):
    id: str
    hostname: str
    cluster_id: str | None = None
    cluster_name: str | None = None
    status: str
    cpu: int
    mem_gb: int
    region: str
    gpu_mode: str
    device_count: int
    heartbeat_at: str | None = None


class NodeList(BaseModel):
    data: list[NodeRow]
    total: int


# ── GET /gpu-devices ──
class BoundSession(BaseModel):
    session_id: str
    gpu_mem_mb: int
    gpu_cores: int


class GpuDeviceRow(BaseModel):
    id: str
    node_id: str
    model: str
    mode: str
    status: str
    gpu_uuid: str
    total_mem_mb: int
    used_mem_mb: int
    free_mem_mb: int
    total_cores: int
    used_cores: int
    free_cores: int
    bound_sessions: list[BoundSession]


class GpuDeviceList(BaseModel):
    data: list[GpuDeviceRow]
    total: int


# ── GET /metrics/cluster ──
class ClusterNodes(BaseModel):
    total: int
    ready: int
    busy: int
    cordoned: int
    offline: int


class ClusterGpu(BaseModel):
    device_total: int
    vram_total_mb: int
    vram_used_mb: int
    vram_load_pct: float
    avg_utilization_pct: float
    empty_gpu_count: int


class ClusterSessions(BaseModel):
    running: int
    queued: int


class ClusterCredit(BaseModel):
    consumed_last_24h: str
    active_holds: str


class ClusterMetrics(BaseModel):
    as_of: str
    nodes: ClusterNodes
    gpu: ClusterGpu
    sessions: ClusterSessions
    credit: ClusterCredit


# ── GET /metrics/billing-report ──
class BillingReportRow(BaseModel):
    group: str
    group_name: str | None = None
    consumed: str | None = None
    topup: str | None = None
    gpu_hours: str | None = None


class BillingReport(BaseModel):
    rows: list[BillingReportRow]
    totals: dict[str, str]
    currency: str | None = None
