"""Internal callback schemas (operator -> control plane)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class OperatorStatusEvent(BaseModel):
    """Status callback payload. Drives consume/settle triggers."""
    phase: str                          # running|terminated|error|preparing|terminating
    bound_gpu_uuid: str | None = None   # physical GPU bound on running
    node_name: str | None = None        # k8s node the pod landed on (pod.spec.nodeName)
    yield_state: str | None = None      # "Yielded" if operator did an in-place yield (not cold)
    pod_ref: str | None = None          # "namespace/name"
    used_mem_mb: int | None = None      # measured occupancy (inventory reconciliation)
    message: str | None = None
    trace_id: str | None = None         # W3C traceparent trace-id
    ts: datetime                        # event time (UTC)


class OperatorGpuDeviceUpsert(BaseModel):
    """GPU device inventory upsert (operator InventoryReconciler -> ledger).

    Pure reflection of measured device-plugin/DCGM capacity. The control plane preserves
    ledger-owned occupancy (used_mem_mb/used_cores) — those are NOT overwritten from here.
    """
    node_id: str                        # k8s node name
    uuid: str                           # stable device key (DCGM UUID or <node>-gpu-<i>)
    mode: str | None = None             # exclusive|fractional|mig
    total_mem_mb: int = 0
    used_mem_mb: int = 0                 # advisory only; ledger is source of truth
    total_cores: int = 0
    used_cores: int = 0
    status: str = "ready"
    model: str | None = None            # GPU model if known (node label nvidia.com/gpu.product)
    cluster_id: str | None = None       # operator injects cfg.ClusterID
    node_cpu: int | None = None         # node CPU cores
    node_mem_gb: int | None = None      # node memory in GiB
    node_disk_gb: int | None = None     # node ephemeral storage in GiB


class OperatorNodeUpsert(BaseModel):
    """Upsert node inventory, reporting capacity for every node whether or not it has a GPU.

    GPU-less nodes — CPU workers and the control plane — still contribute their cpu, mem, and disk
    to the displayed inventory and the capacity calculation. """
    node_id: str                        # k8s node name
    cluster_id: str | None = None       # operator injects cfg.ClusterID
    node_cpu: int | None = None
    node_mem_gb: int | None = None
    node_disk_gb: int | None = None
    lossless_capable: bool = False      # lossless-pause prerequisites (cuda-checkpoint plus CRIU) are labelled ready on the node
    role: str | None = None             # master|gpu|cpu|storage, from node labels/devices


class OperatorNodeHealthEvent(BaseModel):
    """Node health transition (operator HealthReconciler -> ledger).

    On a critical breach (e.g. fatal Xid) the operator cordons the node and reports this; the
    control plane records a NodeHealthEvent and marks GpuNode.status=cordoned.
    """
    node_id: str
    kind: str                           # xid|ecc|temp|down
    severity: str = "critical"          # info|warning|critical
    action: str | None = None           # cordon|alert
    message: str | None = None
    cluster_id: str | None = None
    id: str | None = None


class OperatorAuditEvent(BaseModel):
    """Operator privileged-action audit callback."""
    actor: str                          # "operator:clu_<id>"
    action: str                         # node.cordon|node.drain|pod.delete|session.force_terminate
    target: str                         # "gpu-node-3" | "gshare-sessions/ses-..-pod"
    result: str                         # ok|failed
    detail: dict | None = None
    trace_id: str | None = None
    ts: datetime


class OperatorVolumeObserved(BaseModel):
    """One PVC the operator sees in the session namespace (label gshare.io/volume)."""

    name: str                           # PVC name (the sanitized volume id: vol-01abc...)
    volume_id: str | None = None        # the ledger id when the PVC carries it (gshare.io/volume-id)
    capacity_gb: int = 0                # the claim's current size
    used_bytes: int | None = None       # kubelet volume stats; None when nothing has it mounted
    mounted: bool = False               # some pod currently mounts it


class OperatorSessionDisk(BaseModel):
    """Ephemeral (scratch) disk usage of one session pod, read from kubelet /stats/summary.

    ``name`` is the GShareSession CR name — the sanitized session id (``ses-01abc...``).
    """

    name: str
    ephemeral_used_bytes: int
    ephemeral_limit_bytes: int


class OperatorVolumeSync(BaseModel):
    """POST /internal/volumes/sync — the operator's periodic view of every session-volume PVC."""

    volumes: list[OperatorVolumeObserved]
    cluster_id: str | None = None
    # Per-session scratch-disk usage, piggybacked on the same tick. Old operators omit it.
    sessions: list[OperatorSessionDisk] = []


class VolumeSyncDirective(BaseModel):
    """What the control plane wants for one observed PVC."""

    name: str
    volume_id: str | None = None
    quota_gb: int | None = None         # desired size; the operator grows the claim up to it
    reclaim: bool = False               # deleted past the grace window: drop the PVC and its data


class VolumeSyncResponse(BaseModel):
    volumes: list[VolumeSyncDirective]
    orphans: int = 0                    # PVCs no ledger row explains; kept, surfaced for an admin
