"""Session request/response schemas.

There is **no** ``session_type`` / ``batch_command`` — sessions are interactive only.
``cluster_mode=single|multi`` is the only mode axis.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.schemas.common import ORMModel, PageMetaNoPages

# Paths a mount may not shadow: mounting a volume over these breaks the container image
# (system binaries/libraries) or collides with kernel filesystems.
_MOUNT_RESERVED = ("/proc", "/sys", "/dev", "/etc", "/bin", "/sbin", "/usr", "/lib", "/lib64")
_MOUNT_PATH_RE = re.compile(r"^(/[A-Za-z0-9._-]{1,64})+$")


class VolumeMountSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    volume_id: str
    mount_path: str
    mode: str = Field(default="rw", pattern="^(ro|rw)$")

    @field_validator("mount_path")
    @classmethod
    def _valid_mount_path(cls, v: str) -> str:
        """An absolute, plain-ASCII path: `/seg/seg` with segments of [A-Za-z0-9._-].

        Rejects relative paths, `..`/`.` traversal, spaces, and non-ASCII (e.g. Korean) characters
        — the path lands verbatim in the pod spec and in shell-visible mounts, so it is kept to
        the portable safe set. Reserved system prefixes are refused because mounting over them
        breaks the session container.
        """
        if len(v) > 255:
            raise ValueError("mount_path is too long (max 255 characters)")
        if not _MOUNT_PATH_RE.fullmatch(v):
            raise ValueError(
                "mount_path must be an absolute path of segments using only letters, digits, "
                "'.', '_' and '-' (no spaces, no '..', ASCII only)"
            )
        if any(seg in (".", "..") for seg in v.split("/")):
            raise ValueError("mount_path must not contain '.' or '..' segments")
        norm = v.rstrip("/") or "/"
        if any(norm == r or norm.startswith(r + "/") for r in _MOUNT_RESERVED):
            raise ValueError(f"mount_path may not shadow a system path ({norm})")
        return v


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None                           # optional user-chosen display name
    offering_id: str
    image_id: str
    resource_class: str = Field(pattern="^(gpu|cpu)$")
    cluster_id: str | None = None                     # unset means _resolve_cluster picks an available cluster
    cluster_mode: str = Field(default="single", pattern="^(single|multi)$")
    group_id: str | None = None
    gpu_mem_mb: int | None = Field(default=None, ge=0)  # required for gpu class
    gpu_cores: int | None = Field(default=None, ge=0, le=100)
    # Compute (cpu, mem, disk). When set, these override the offering defaults, as a compute preset.
    cpu: int | None = Field(default=None, ge=0)
    mem_gb: int | None = Field(default=None, ge=0)
    disk_gb: int | None = Field(default=None, ge=0)
    # MIG is unsupported: only fractional (shared) and exclusive are allowed, and mode=mig is 422.
    mode: str | None = Field(default=None, pattern="^(fractional|exclusive)$")
    billing_wallet_id: str | None = None              # NULL allowed for cpu (free)
    # Spot (preemptible) session: with no normal capacity free, it is admitted by borrowing a card a
    # resident yielded, and is reclaimed when that resident returns. Exclusive only.
    # (See docs/paper/manuscript, §Design.)
    preemptible: bool = False
    # Scheduling priority, higher wins. With no capacity free, a request can actively preempt a
    # lower-priority yieldable session.
    priority: int = Field(default=0, ge=0)
    volume_mounts: list[VolumeMountSpec] = []


class SessionMountRead(BaseModel):
    """One mounted volume, joined with its display facts for the detail screens."""
    volume_id: str
    name: str | None = None
    type: str | None = None
    quota_gb: int | None = None
    mount_path: str
    mode: str


class SessionRead(ORMModel):
    id: str
    name: str | None = None
    status: str            # pending|preparing|running|paused|terminating|terminated|error
    # WHY the session paused or ended: idle|credit_exhausted|admin_stopped|user_stopped|
    # max_runtime|preempted|disk_exceeded|error. None while running or for legacy rows.
    status_reason: str | None = None
    occupancy: float | None = None   # max(mem/total, cores/100)
    bound_gpu_uuid: str | None = None
    # WHERE the session runs: the node's hostname (operator-reported for CPU sessions, or the
    # bound GPU's node) and, when the inventory knows it, the GpuNode id for deep links.
    node_hostname: str | None = None
    node_id: str | None = None
    cluster_id: str
    group_id: str | None = None            # the session's group, shown in monitoring
    group_name: str | None = None          # group name, shown in monitoring
    org_id: str | None = None              # organization, derived from the group
    org_name: str | None = None            # organization name, shown in monitoring
    resource_class: str
    mode: str | None = None
    gpu_mem_mb: int | None = None
    gpu_cores: int | None = None
    # Host compute snapshot (request overrides or offering defaults at creation time) — shown
    # alongside the GPU slice so a session's full footprint is visible in the lists.
    cpu: int | None = None
    mem_gb: int | None = None
    disk_gb: int | None = None
    # The offering's full device model string ("NVIDIA RTX PRO 5000 Blackwell") — the answer to
    # "which GPU", shown in the user list/detail instead of the raw card UUID. None for CPU
    # offerings.
    gpu_model: str | None = None
    # Live scratch-disk gauge (single-session detail only): the pod's ephemeral-storage usage
    # against its limit — a kubelet /stats/summary reading relayed by the operator, up to
    # ~5 minutes stale. None on lists, for CPU-less-disk pods, or when no fresh reading exists.
    disk_used_bytes: int | None = None
    disk_limit_bytes: int | None = None
    # Volumes mounted into the session (single-session detail only; empty on lists).
    mounts: list[SessionMountRead] = []
    owner_user_id: str | None = None
    owner_name: str | None = None          # owner name, shown in the administrators' monitoring view
    # For the cost and uptime columns: the rate snapshot plus the start and end timestamps.
    credit_per_hour_snapshot: float | None = None
    started_at: datetime | None = None
    terminated_at: datetime | None = None
    created_at: datetime | None = None
    # When the status last changed — running since, errored at, paused at (admin monitor column).
    status_changed_at: datetime | None = None


class PreviewCostRequest(SessionCreate):
    pass


class PreviewCostResponse(BaseModel):
    estimated_credit_per_hour: float
    occupancy: float
    hold_amount: float


class BulkTerminateRequest(BaseModel):
    session_ids: list[str]
    reason: str | None = None


class ConnectionInfo(BaseModel):
    kind: str          # vscode|jupyter|webapp
    url: str
    connection_token: str   # one-time cnx_ token
    expires_at: datetime | None = None   # token expiry: now plus CONNECTION_TOKEN_TTL_SEC
    command: str | None = None           # optional command line shown as connect guidance


# ── queue entries ──
class QueueEntryView(BaseModel):
    """One queue entry as projected by ``_entry_view`` (status is always ``queued``)."""

    id: str
    session_id: str
    priority: int
    status: str
    position: int
    score: float
    session_req: dict[str, Any]
    enqueued_at: str | None = None
    # Rough wait estimate (position × median realized wait); None until enough dequeues sampled.
    eta_minutes: int | None = None


class QueueList(BaseModel):
    """GET /queue envelope (pagination without total_pages)."""

    data: list[QueueEntryView]
    pagination: PageMetaNoPages


class QueueMineList(BaseModel):
    """GET /queue/mine envelope (data only, no pagination)."""

    data: list[QueueEntryView]
