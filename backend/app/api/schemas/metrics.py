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
    disk_gb: int = 0
    role: str | None = None            # master|gpu|cpu|storage (operator-derived)
    region: str
    gpu_mode: str
    # Per-card pool counts on the node, e.g. {"fractional": 2, "mig": 1}.
    mode_counts: dict[str, int] = {}
    device_count: int
    # Host compute promised to sessions holding a live allocation on this node's cards. CPU-class
    # sessions are placed by the k8s scheduler, not the ledger, so they are not attributed here.
    alloc_cpu: int = 0
    alloc_mem_gb: int = 0
    alloc_disk_gb: int = 0
    # Sessions currently running on this node (operator-reported node_hostname; CPU ones too).
    running_sessions: int = 0
    heartbeat_at: str | None = None
    # Node pool membership; None means the node is shared (usable by every tenant).
    pool_id: str | None = None
    pool_name: str | None = None


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
    # Per-card pool target + drain state (ready|draining|applying|error); see gpu_device model.
    desired_mode: str | None = None
    mode_state: str = "ready"
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


class ClusterResource(BaseModel):
    """One host-compute dimension fleet-wide: promised to active sessions vs node capacity."""

    used: int
    total: int


class ClusterCompute(BaseModel):
    cpu: ClusterResource
    mem_gb: ClusterResource
    disk_gb: ClusterResource


class ClusterStorageDisk(BaseModel):
    used: int   # provisioned volume quota (GiB) — allocation, not bytes on disk
    total: int  # storage-server host disk capacity (GB)


class ClusterStorage(BaseModel):
    disk_gb: ClusterStorageDisk
    node_count: int = 0


class ClusterMetrics(BaseModel):
    as_of: str
    nodes: ClusterNodes
    gpu: ClusterGpu
    sessions: ClusterSessions
    credit: ClusterCredit
    compute: ClusterCompute
    storage: ClusterStorage | None = None


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
