"""Internal callback schemas (operator -> control plane)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class OperatorStatusEvent(BaseModel):
    """Status callback payload. Drives consume/settle triggers."""
    phase: str                          # running|terminated|error|preparing|terminating
    bound_gpu_uuid: str | None = None   # physical GPU bound on running
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
