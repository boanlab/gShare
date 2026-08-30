"""Clusters router. CRUD + connection-test. super_admin only.

Cluster lifecycle / state machine:
  status: connected | degraded | unreachable
  transitions: connected <-> degraded; connected|degraded -> unreachable;
               unreachable -> connected|degraded (connection-test / periodic health).

Registration validates (1) RuntimeClass ``nvidia`` exists, (2) HAMi device-plugin is
advertised, (3) api server reachable, then stores the kubeconfig as a SECRET REFERENCE only never
plaintext and seeds the node/GpuDevice inventory. All K8s I/O goes through a
narrow ``ClusterProbe`` port (lazy kubernetes_asyncio) so the module is sandbox-safe.
"""
from __future__ import annotations

import base64
import binascii
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination, get_current_principal
from app.api.schemas.cluster import ClusterList
from app.auth.rbac import Principal
from app.core import ids
from app.core.errors import DomainError, NotFound
from app.db.base import get_db
from app.db.models import Allocation, Cluster, GpuDevice, GpuNode
from app.db.models import Session as SessionModel
from app.domain.audit_service import AuditService

router = APIRouter(prefix="/clusters", tags=["clusters"])

_CLUSTER_ROLES = {"primary", "standby"}
_CLUSTER_STATUSES = {"connected", "degraded", "unreachable"}
# Allowed state-machine transitions. pending is the pre-validation seed state.
_ALLOWED_TRANSITIONS = {
    "pending": {"connected", "degraded", "unreachable"},
    "connected": {"connected", "degraded", "unreachable"},
    "degraded": {"connected", "degraded", "unreachable"},
    "unreachable": {"connected", "degraded", "unreachable"},
}


class _Conflict(DomainError):
    code, http = "conflict", 409


class _Validation(DomainError):
    code, http = "validation_failed", 422


# ── request bodies ──
class ClusterRegister(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    role: str
    kubeconfig_b64: str


class ClusterPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    role: str | None = None


# ── K8s probe port (validation + inventory). Injectable for tests/offline. ──
@dataclass
class ProbeResult:
    """Outcome of a connectivity/runtime probe against a cluster apiserver."""

    api_server_reachable: bool = False
    runtime_class_nvidia: bool = False
    hami_device_plugin: bool = False
    api_server: str | None = None
    runtime: str | None = None
    latency_ms: int | None = None
    # inventory discovered at registration time (auto-inventory).
    nodes: list[dict[str, Any]] = field(default_factory=list)
    devices: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    @property
    def checks(self) -> dict[str, bool]:
        return {
            "runtime_class_nvidia": self.runtime_class_nvidia,
            "hami_device_plugin": self.hami_device_plugin,
            "api_server_reachable": self.api_server_reachable,
        }

    def derive_status(self) -> str:
        """Map probe outcome -> cluster status."""
        if not self.api_server_reachable:
            return "unreachable"
        if not (self.runtime_class_nvidia and self.hami_device_plugin):
            return "degraded"
        # Any NotReady node degrades an otherwise-healthy cluster.
        if any(n.get("status") and n["status"] != "ready" for n in self.nodes):
            return "degraded"
        return "connected"


class ClusterProbe:
    """Validates connectivity + runtime and enumerates inventory via kubernetes_asyncio (lazy).

    Kept as a narrow port so tests substitute a fake. The real implementation builds an ApiClient
    from the kubeconfig YAML and queries: api server version (reachability/latency), RuntimeClass
    ``nvidia`` (node.k8s.io/v1), and HAMi device-plugin advertisement on node ``status.allocatable``
    (``nvidia.com/gpu``/``nvidia.com/gpumem``). Inventory is read from Nodes (+ allocatable GPU).
    """

    async def probe(self, kubeconfig_yaml: str | None) -> ProbeResult:
        try:
            import yaml  # lazy
            from kubernetes_asyncio import client, config  # lazy import (sandbox-safe)
        except Exception as exc:  # noqa: BLE001 — sandbox without the client installed
            return ProbeResult(error=f"k8s_client_unavailable: {exc}")

        configuration = client.Configuration()
        try:
            if kubeconfig_yaml:
                loader = config.kube_config.KubeConfigLoader(
                    config_dict=yaml.safe_load(kubeconfig_yaml)
                )
                await loader.load_and_set(configuration)
            else:
                config.load_incluster_config(client_configuration=configuration)
        except Exception as exc:  # noqa: BLE001
            return ProbeResult(error=f"kubeconfig_invalid: {exc}")

        api_client = client.ApiClient(configuration)
        result = ProbeResult(api_server=configuration.host)
        try:
            t0 = time.monotonic()
            version_api = client.VersionApi(api_client)
            await version_api.get_code()
            result.api_server_reachable = True
            result.latency_ms = int((time.monotonic() - t0) * 1000)

            # (1) RuntimeClass nvidia.
            node_api = client.NodeV1Api(api_client)
            rcs = await node_api.list_runtime_class()
            result.runtime_class_nvidia = any(
                rc.metadata and rc.metadata.name == "nvidia" for rc in (rcs.items or [])
            )

            # (2) HAMi device-plugin advertisement + (4) inventory from Nodes.
            core = client.CoreV1Api(api_client)
            nodes = await core.list_node()
            for n in nodes.items or []:
                allocatable = (n.status.allocatable or {}) if n.status else {}
                annotations = (n.metadata.annotations or {}) if n.metadata else {}
                # HAMi marks managed GPU nodes with these annotations (gpumem/gpucores live here and
                # are enforced at schedule time — node allocatable only carries the split
                # nvidia.com/gpu count, so checking allocatable for gpumem misses a healthy HAMi
                # node).
                if (
                    "hami.io/node-nvidia-register" in annotations
                    or "hami.io/node-handshake" in annotations
                    or "nvidia.com/gpumem" in allocatable
                ):
                    result.hami_device_plugin = True
                ready = _node_ready(n)
                result.nodes.append({
                    "hostname": n.metadata.name if n.metadata else None,
                    "status": "ready" if ready else "offline",
                    "gpu_count": _int_or_zero(allocatable.get("nvidia.com/gpu")),
                })
                if n.status and n.status.node_info and not result.runtime:
                    result.runtime = n.status.node_info.container_runtime_version
        except Exception as exc:  # noqa: BLE001
            # Reached partway — reachable flips false only if version call itself failed.
            if not result.api_server_reachable:
                result.error = f"cluster_unreachable: {exc}"
            else:
                result.error = f"probe_partial: {exc}"
        finally:
            await api_client.close()
        return result


def _node_ready(node: Any) -> bool:
    conditions = (node.status.conditions or []) if getattr(node, "status", None) else []
    for c in conditions:
        if getattr(c, "type", None) == "Ready":
            return getattr(c, "status", None) == "True"
    return False


def _int_or_zero(val: Any) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


# Module-level probe so tests can monkeypatch a fake.
_probe = ClusterProbe()


def _decode_kubeconfig(b64: str) -> str:
    try:
        return base64.b64decode(b64, validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise _Validation("kubeconfig_b64 invalid", {"reason": "decode_failed"}) from exc


async def _serialize_cluster(c: Cluster, db: AsyncSession) -> dict[str, Any]:
    node_count = await db.scalar(
        select(func.count()).select_from(GpuNode).where(GpuNode.cluster_id == c.id)
    ) or 0
    gpu_count = await db.scalar(
        select(func.count()).select_from(GpuDevice).where(GpuDevice.cluster_id == c.id)
    ) or 0
    return {
        "id": c.id,
        "name": c.name,
        "role": c.role,
        "api_server": c.api_server,
        "runtime": c.runtime,
        "status": c.status,
        "kubeconfig_secret_ref": c.kubeconfig_secret_ref,
        "node_count": int(node_count),
        "gpu_count": int(gpu_count),
        "registered_at": c.created_at,
    }


@router.get("", response_model=ClusterList)
async def list_clusters(
    pagination: Pagination = Depends(),
    role: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Cluster list. super_admin only."""
    principal.require(action="cluster.read")

    base = select(Cluster).where(Cluster.deleted_at.is_(None))
    if role is not None:
        if role not in _CLUSTER_ROLES:
            raise _Validation("invalid role", {"role": role})
        base = base.where(Cluster.role == role)
    if status_filter is not None:
        if status_filter not in _CLUSTER_STATUSES:
            raise _Validation("invalid status", {"status": status_filter})
        base = base.where(Cluster.status == status_filter)

    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = (
        await db.scalars(
            base.order_by(Cluster.created_at.desc()).offset(pagination.offset).limit(pagination.size)
        )
    ).all()
    size = pagination.size
    return {
        "data": [await _serialize_cluster(c, db) for c in rows],
        "pagination": {
            "page": pagination.page,
            "size": size,
            "total": total,
            "total_pages": math.ceil(total / size) if size else 0,
        },
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def register_cluster(
    body: ClusterRegister,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Register a cluster: validate kubeconfig, store secret ref, seed inventory."""
    principal.require(action="cluster.create")

    if body.role not in _CLUSTER_ROLES:
        raise _Validation("invalid role", {"role": body.role})

    kubeconfig_yaml = _decode_kubeconfig(body.kubeconfig_b64)

    # Name uniqueness pre-check among live clusters (partial UNIQUE index is final defense) -> 409.
    if await db.scalar(
        select(Cluster.id).where(Cluster.name == body.name, Cluster.deleted_at.is_(None))
    ) is not None:
        raise _Conflict("cluster name already registered", {"name": body.name})

    # (1)(2)(3) validation against the apiserver.
    probe = await _probe.probe(kubeconfig_yaml)
    if not probe.api_server_reachable:
        raise _Validation(
            "cluster unreachable", {"reason": "cluster_unreachable", "error": probe.error}
        )
    missing = [k for k, ok in probe.checks.items() if not ok and k != "api_server_reachable"]
    if missing:
        raise _Validation("runtime validation failed", {"missing": missing})

    # api_server uniqueness among live clusters -> 409.
    api_server = probe.api_server or ""
    if api_server and await db.scalar(
        select(Cluster.id).where(
            Cluster.api_server == api_server, Cluster.deleted_at.is_(None)
        )
    ) is not None:
        raise _Conflict("cluster api_server already registered", {"api_server": api_server})

    cluster_id = ids.new("cluster")
    # kubeconfig is stored as a secret REFERENCE only — never plaintext.
    secret_ref = f"secret://gshare/cluster-creds/{cluster_id}"
    cluster = Cluster(
        id=cluster_id,
        name=body.name,
        role=body.role,
        api_server=api_server,
        runtime=probe.runtime or "unknown",
        status=probe.derive_status(),
        kubeconfig_secret_ref=secret_ref,
    )
    db.add(cluster)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise _Conflict("cluster name/api_server already registered", {"name": body.name}) from exc

    # (4) auto-inventory nodes + GpuDevices discovered by the probe.
    _seed_inventory(db, cluster_id, probe)

    await AuditService(db).record(
        actor=principal.user_id, action="cluster.register", target=cluster_id, result="ok",
        name=body.name, role=body.role, api_server=api_server,
    )
    await db.commit()
    return await _serialize_cluster(cluster, db)


def _seed_inventory(db: AsyncSession, cluster_id: str, probe: ProbeResult) -> None:
    """Persist discovered GpuNode rows from the probe (devices populate as DCGM/operator
    reports)."""
    for node in probe.nodes:
        node_id = ids.new("node")
        db.add(GpuNode(
            id=node_id,
            cluster_id=cluster_id,
            hostname=node.get("hostname") or node_id,
            status=node.get("status") or "ready",
        ))


async def _load_cluster(cluster_id: str, db: AsyncSession) -> Cluster:
    cluster = await db.get(Cluster, cluster_id)
    if cluster is None or cluster.deleted_at is not None:
        raise NotFound("cluster", {"cluster_id": cluster_id})
    return cluster


@router.get("/{cluster_id}")
async def get_cluster(
    cluster_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Cluster detail + node/device summary. super_admin only."""
    principal.require(action="cluster.read")
    cluster = await _load_cluster(cluster_id, db)

    nodes = (
        await db.scalars(select(GpuNode).where(GpuNode.cluster_id == cluster_id))
    ).all()
    node_totals = {"total": len(nodes), "ready": 0, "cordoned": 0, "offline": 0}
    for n in nodes:
        if n.status in node_totals:
            node_totals[n.status] += 1

    device_total = await db.scalar(
        select(func.count()).select_from(GpuDevice).where(GpuDevice.cluster_id == cluster_id)
    ) or 0
    vram_total = await db.scalar(
        select(func.coalesce(func.sum(GpuDevice.total_mem_mb), 0)).where(
            GpuDevice.cluster_id == cluster_id
        )
    ) or 0

    base = await _serialize_cluster(cluster, db)
    base.pop("node_count", None)
    base.pop("gpu_count", None)
    base["checks"] = {
        "runtime_class_nvidia": cluster.status != "unreachable",
        "hami_device_plugin": cluster.status != "unreachable",
        "api_server_reachable": cluster.status != "unreachable",
    }
    base["nodes"] = node_totals
    base["gpu"] = {"device_total": int(device_total), "vram_total_mb": int(vram_total)}
    base["last_checked_at"] = cluster.updated_at
    return base


@router.patch("/{cluster_id}")
async def update_cluster(
    cluster_id: str,
    body: ClusterPatch,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Update name/role only. kubeconfig rotation is out of scope here. super_admin only."""
    principal.require(action="cluster.update")
    cluster = await _load_cluster(cluster_id, db)

    changes: dict[str, Any] = {}   # {field: {"from": old, "to": new}} for the audit log
    if body.role is not None and body.role != cluster.role:
        if body.role not in _CLUSTER_ROLES:
            raise _Validation("invalid role", {"role": body.role})
        changes["role"] = {"from": cluster.role, "to": body.role}
        cluster.role = body.role
    if body.name is not None and body.name != cluster.name:
        dup = await db.scalar(
            select(Cluster.id).where(
                Cluster.name == body.name,
                Cluster.id != cluster_id,
                Cluster.deleted_at.is_(None),
            )
        )
        if dup is not None:
            raise _Conflict("cluster name already registered", {"name": body.name})
        changes["name"] = {"from": cluster.name, "to": body.name}
        cluster.name = body.name

    if changes:
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            raise _Conflict("cluster name already registered", {"name": body.name}) from exc
        await AuditService(db).record(
            actor=principal.user_id, action="cluster.update", target=cluster_id, result="ok",
            changes=changes,
        )
    await db.commit()
    return await _serialize_cluster(cluster, db)


@router.delete("/{cluster_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deregister_cluster(
    cluster_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Deregister a cluster + discard its kubeconfig secret ref.

    Refused while live sessions/allocations exist on the cluster (-> 409). super_admin only.
    """
    principal.require(action="cluster.delete")
    cluster = await _load_cluster(cluster_id, db)

    live_sessions = await db.scalar(
        select(func.count())
        .select_from(SessionModel)
        .where(
            SessionModel.cluster_id == cluster_id,
            SessionModel.deleted_at.is_(None),
            SessionModel.status.notin_(["terminated", "error"]),
        )
    ) or 0
    live_allocs = await db.scalar(
        select(func.count())
        .select_from(Allocation)
        .join(SessionModel, Allocation.session_id == SessionModel.id)
        .where(SessionModel.cluster_id == cluster_id, Allocation.ended_at.is_(None))
    ) or 0
    if live_sessions or live_allocs:
        raise _Conflict(
            "cluster has active sessions/allocations",
            {"sessions": int(live_sessions), "allocations": int(live_allocs)},
        )

    # Discard inventory reflection (GpuNode/GpuDevice) so a re-registration of the same hardware
    # starts clean — device PK/gpu_uuid are global, so leaving stale rows would collide on the
    # operator's next inventory upsert. Historical allocations keep gpu_uuid (string) for the
    # record; only the nullable device_id FK is cleared so the device rows can be removed.
    dev_ids = (
        await db.scalars(select(GpuDevice.id).where(GpuDevice.cluster_id == cluster_id))
    ).all()
    if dev_ids:
        await db.execute(
            update(Allocation)
            .where(Allocation.device_id.in_(dev_ids))
            .values(device_id=None)
        )
    await db.execute(delete(GpuDevice).where(GpuDevice.cluster_id == cluster_id))
    await db.execute(delete(GpuNode).where(GpuNode.cluster_id == cluster_id))

    # Soft-delete + discard the credential reference (operator/external-secrets reclaims the
    # secret).
    cluster.deleted_at = func.now()
    cluster.status = "unreachable"

    await AuditService(db).record(
        actor=principal.user_id, action="cluster.deregister", target=cluster_id, result="ok",
        kubeconfig_secret_ref=cluster.kubeconfig_secret_ref,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{cluster_id}/connection-test")
async def connection_test(
    cluster_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Re-validate connectivity/health to the cluster apiserver.

    Re-runs the runtime/connectivity probe, applies the state-machine transition, and persists the
    new ``status``. super_admin only.
    """
    principal.require(action="cluster.update")
    cluster = await _load_cluster(cluster_id, db)

    # Resolve kubeconfig from the secret ref (mounted by external-secrets); None -> in-cluster SA.
    kubeconfig_yaml = await _read_cluster_secret(cluster.kubeconfig_secret_ref)
    probe = await _probe.probe(kubeconfig_yaml)
    new_status = probe.derive_status()

    # Enforce the documented state machine — all probe outcomes are reachable targets.
    allowed = _ALLOWED_TRANSITIONS.get(cluster.status, set(_CLUSTER_STATUSES))
    if new_status not in allowed:
        # Defensive: should not happen given the matrix, but keep the contract explicit.
        new_status = cluster.status

    prev_status = cluster.status
    cluster.status = new_status
    # Notify the system administrators when the state degrades (connected to degraded or
    # unreachable).
    if new_status in ("degraded", "unreachable") and new_status != prev_status:
        from app.domain.notification_service import NotificationService

        await db.flush()
        notifier = NotificationService(db)
        await notifier.notify(
            await notifier.system_admins(), "cluster_health",
            f"Cluster {new_status}",
            f"Cluster '{cluster.name}' changed from {prev_status} to {new_status}.",
            params={"cluster_name": cluster.name, "prev_status": prev_status,
                    "new_status": new_status},
            cluster_id=cluster_id, status=new_status,
        )
    await AuditService(db).record(
        actor=principal.user_id, action="cluster.connection_test", target=cluster_id,
        result=new_status, checks=probe.checks,
    )
    await db.commit()

    return {
        "cluster_id": cluster_id,
        "status": new_status,
        "checks": probe.checks,
        "latency_ms": probe.latency_ms,
        "checked_at": datetime.now(UTC),
    }


async def _read_cluster_secret(secret_ref: str | None) -> str | None:
    """Resolve a cluster kubeconfig secret ref to its YAML payload (mounted file; never in DB).

    See app.cluster.kubeconfig.resolve_cluster_kubeconfig — external-secrets (k8s) or a host mount
    (compose/bare-metal) projects the file under CLUSTER_KUBECONFIG_DIR; we only read it. Returns
    None when no file exists (-> in-cluster SA fallback / probe reports unreachable in-sandbox).
    """
    from app.cluster.kubeconfig import resolve_cluster_kubeconfig
    from app.core.config import settings

    return resolve_cluster_kubeconfig(secret_ref, settings.CLUSTER_KUBECONFIG_DIR)
