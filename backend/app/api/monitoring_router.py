"""Monitoring proxy: whitelisted Prometheus queries for the admin monitoring page.

The console never talks to Prometheus directly. It names a PANEL; this router owns the PromQL.
Two reasons, both load-bearing:

* Authorisation. Prometheus has no notion of GShare tenancy, so an arbitrary-PromQL passthrough
  would be an authorisation hole. Every request here goes through ``monitoring.read``
  (super_admin only today).
* Stability. The panel ids are the contract with the console; the queries behind them can be
  retuned (or swapped from DCGM to HAMi) without touching the frontend.

Measured utilisation lives HERE and is labelled as measured. The admin dashboard's GPU figure is
deliberately allocation-based — a card can be fully allocated and sit at 0% — and the two must not
be confused (see infra_router._cluster_metrics).
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_principal
from app.auth.rbac import Principal
from app.core.config import settings
from app.core.errors import DomainError, NotFound
from app.db.base import get_db
from app.db.models import Allocation, GpuDevice, GpuNode
from app.db.models import Session as SessionRow

router = APIRouter(tags=["monitoring"])


class _Upstream(DomainError):
    code, http = "monitoring_unavailable", 503


class _BadPanel(DomainError):
    code, http = "validation_failed", 422


# Node names come from Kubernetes; keep the filter to a DNS-1123-ish shape so nothing can be
# smuggled into the PromQL selector.
_NODE_RE = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,61}[a-z0-9])?$")
# NVIDIA device UUIDs: "GPU-<uuid>" and, on a MIG slice, "MIG-<uuid>".
_GPU_RE = re.compile(r"^(GPU|MIG)-[0-9a-fA-F-]{8,64}$")

# Ranges the console offers, with a step that keeps every panel around 60-240 points.
_RANGES: dict[str, tuple[int, int]] = {   # range -> (seconds, step seconds)
    "15m": (900, 15),
    "1h": (3600, 30),
    "6h": (21600, 120),
    "24h": (86400, 300),
    "7d": (604800, 1800),
}

# panel id -> (PromQL template, unit, legend label keys). `{node}` expands to a node selector
# fragment (empty when no filter is applied).
_PANELS: dict[str, dict[str, Any]] = {
    # ── GPU (DCGM exporter) ──
    # Every query is aggregated max by (UUID, modelName, gpu, node): when the exporter pod
    # restarts, the same card re-appears under a new pod/instance label pair, which used to
    # split one card's line into two disconnected series (the "missing link" mid-chart).
    "gpu_util": {
        "q": 'max by (UUID, modelName, gpu, node) (DCGM_FI_DEV_GPU_UTIL{node=~"{node}",UUID=~"{gpu}"})',
        "unit": "percent", "legend": ["modelName", "gpu", "node"],
    },
    "gpu_mem_used": {
        # FB_USED is MiB; the console renders GiB.
        "q": 'max by (UUID, modelName, gpu, node) (DCGM_FI_DEV_FB_USED{node=~"{node}",UUID=~"{gpu}"})',
        "unit": "mib", "legend": ["modelName", "gpu", "node"],
    },
    "gpu_mem_pct": {
        "q": '100 * max by (UUID, modelName, gpu, node) (DCGM_FI_DEV_FB_USED{node=~"{node}",UUID=~"{gpu}"}) / '
             'clamp_min(max by (UUID, modelName, gpu, node) (DCGM_FI_DEV_FB_USED{node=~"{node}",UUID=~"{gpu}"}) + '
             'max by (UUID, modelName, gpu, node) (DCGM_FI_DEV_FB_FREE{node=~"{node}",UUID=~"{gpu}"}), 1)',
        "unit": "percent", "legend": ["modelName", "gpu", "node"],
    },
    "gpu_temp": {
        "q": 'max by (UUID, modelName, gpu, node) (DCGM_FI_DEV_GPU_TEMP{node=~"{node}",UUID=~"{gpu}"})',
        "unit": "celsius", "legend": ["modelName", "gpu", "node"],
    },
    "gpu_power": {
        "q": 'max by (UUID, modelName, gpu, node) (DCGM_FI_DEV_POWER_USAGE{node=~"{node}",UUID=~"{gpu}"})',
        "unit": "watt", "legend": ["modelName", "gpu", "node"],
    },
    "gpu_sm_clock": {
        "q": 'max by (UUID, modelName, gpu, node) (DCGM_FI_DEV_SM_CLOCK{node=~"{node}",UUID=~"{gpu}"})',
        "unit": "mhz", "legend": ["modelName", "gpu", "node"],
    },
    "gpu_xid": {
        "q": 'max by (UUID, modelName, gpu, node) (DCGM_FI_DEV_XID_ERRORS{node=~"{node}",UUID=~"{gpu}"})',
        "unit": "count", "legend": ["modelName", "gpu", "node"],
    },
    # ── Host (node-exporter / kube-state-metrics) ──
    "host_cpu": {
        "q": '100 - (avg by (node) (rate(node_cpu_seconds_total{mode="idle",node=~"{node}"}[5m])) * 100)',
        "unit": "percent", "legend": ["node"],
    },
    "host_mem": {
        "q": '100 * (1 - node_memory_MemAvailable_bytes{node=~"{node}"} / '
             'clamp_min(node_memory_MemTotal_bytes{node=~"{node}"}, 1))',
        "unit": "percent", "legend": ["node"],
    },
    "host_disk": {
        "q": '100 - 100 * sum by (node) (node_filesystem_avail_bytes{fstype!~"tmpfs|overlay|squashfs",node=~"{node}"}) '
             '/ clamp_min(sum by (node) (node_filesystem_size_bytes{fstype!~"tmpfs|overlay|squashfs",node=~"{node}"}), 1)',
        "unit": "percent", "legend": ["node"],
    },
    "host_net_rx": {
        "q": 'sum by (node) (rate(node_network_receive_bytes_total{device!~"lo|veth.*|cali.*|tunl.*|docker.*",node=~"{node}"}[5m]))',
        "unit": "bytes_per_sec", "legend": ["node"],
    },
    "host_net_tx": {
        "q": 'sum by (node) (rate(node_network_transmit_bytes_total{device!~"lo|veth.*|cali.*|tunl.*|docker.*",node=~"{node}"}[5m]))',
        "unit": "bytes_per_sec", "legend": ["node"],
    },
    "host_pods": {
        "q": 'count by (node) (kube_pod_info{node=~"{node}"})',
        "unit": "count", "legend": ["node"],
    },
}


def _render(panel: str, node: str | None, gpu: str | None = None) -> tuple[str, dict[str, Any]]:
    spec = _PANELS.get(panel)
    if spec is None:
        raise _BadPanel("unknown panel", {"panel": panel})
    if node is not None and not _NODE_RE.fullmatch(node):
        raise _BadPanel("invalid node name", {"node": node})
    if gpu is not None and not _GPU_RE.fullmatch(gpu):
        raise _BadPanel("invalid gpu uuid", {"gpu": gpu})
    q = spec["q"].replace("{node}", node or ".*").replace("{gpu}", gpu or ".*")
    return q, spec


async def _prom(path: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{settings.PROMETHEUS_URL}{path}", params=params)
            body = r.json()
    except Exception as exc:  # noqa: BLE001 — an unreachable Prometheus is an expected state
        raise _Upstream("metrics backend unreachable") from exc
    if body.get("status") != "success":
        raise _Upstream(str(body.get("error") or "metrics query failed"))
    return body["data"]


def _series(data: dict[str, Any], legend: list[str], value_key: str) -> list[dict[str, Any]]:
    out = []
    for s in data.get("result", []):
        m = s.get("metric", {})
        out.append({
            "labels": {k: m.get(k) for k in legend if m.get(k) is not None},
            "uuid": m.get("UUID"),
            "points": (
                [[float(t), None if v in ("NaN", None) else float(v)] for t, v in s.get("values", [])]
                if value_key == "values"
                else [[float(s["value"][0]), float(s["value"][1])]]
            ),
        })
    return out


@router.get("/monitoring/status")
async def monitoring_status(
    principal: Principal = Depends(get_current_principal),
):
    """Whether the metrics backend answers, so the page can say "not configured" instead of
    rendering empty charts."""
    principal.require(action="monitoring.read")
    try:
        data = await _prom("/api/v1/query", {"query": "up"})
        targets = len(data.get("result", []))
        return {"available": True, "targets": targets, "url": settings.PROMETHEUS_URL}
    except DomainError:
        return {"available": False, "targets": 0, "url": settings.PROMETHEUS_URL}


def _session_pod_selector(session_id: str) -> str:
    # Pod name mirrors the CR naming in app.cluster.crd (ses-<id, lowercased, _ -> ->).
    pod = "ses-" + session_id.lower().replace("_", "-")
    return f'pod="{pod}",namespace="gshare-sessions"'


async def session_usage_payload(session_id: str) -> dict[str, float | None]:
    """Instant measured usage of one session's pod. The caller owns authorisation."""
    sel = _session_pod_selector(session_id)
    queries = {
        "cpu_cores": f'sum(rate(container_cpu_usage_seconds_total{{{sel},container="session"}}[1m]))',
        "mem_bytes": f'sum(container_memory_working_set_bytes{{{sel},container="session"}})',
        "gpu_core_pct": f'sum(hami_container_device_utilization_ratio{{{sel}}})',
        "vram_bytes": f'sum(hami_container_device_memory_bytes{{{sel}}})',
    }

    async def one(q: str) -> float | None:
        data = await _prom("/api/v1/query", {"query": q})
        rows = data.get("result", [])
        return float(rows[0]["value"][1]) if rows else None

    vals = await asyncio.gather(*(one(q) for q in queries.values()))
    return dict(zip(queries.keys(), vals, strict=True))


async def session_usage_series(session_id: str, range_: str) -> dict[str, Any]:
    """The four usage metrics over a range. The caller owns authorisation."""
    if range_ not in _RANGES:
        raise _BadPanel("unknown range", {"range": range_})
    sel = _session_pod_selector(session_id)
    metrics = {
        "cpu_cores": (f'sum(rate(container_cpu_usage_seconds_total{{{sel},container="session"}}[1m]))', "cores"),
        "mem_mib": (f'sum(container_memory_working_set_bytes{{{sel},container="session"}}) / 1048576', "mib"),
        "vram_mib": (f'sum(hami_container_device_memory_bytes{{{sel}}}) / 1048576', "mib"),
        "gpu_core_pct": (f'sum(hami_container_device_utilization_ratio{{{sel}}})', "percent"),
    }
    span, step = _RANGES[range_]
    import time

    now = int(time.time())

    async def one(q: str) -> list[list[float | None]]:
        data = await _prom("/api/v1/query_range", {
            "query": q, "start": now - span, "end": now, "step": step,
        })
        rows = data.get("result", [])
        if not rows:
            return []
        return [
            [float(t), None if v in ("NaN", None) else float(v)]
            for t, v in rows[0].get("values", [])
        ]

    series = await asyncio.gather(*(one(q) for q, _ in metrics.values()))
    return {
        "range": range_, "step": step,
        "metrics": {
            k: {"unit": unit, "points": pts}
            for (k, (_, unit)), pts in zip(metrics.items(), series, strict=True)
        },
    }


@router.get("/monitoring/sessions/{session_id}/usage")
async def session_usage(
    session_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Measured live usage of ONE session's pod: cadvisor CPU/MEM + HAMi VRAM/GPU-core.

    Instant values for the monitor drawer, refreshed by the console. VRAM and core come from
    HAMi's in-container monitor, which only reports while a CUDA context exists — an idle
    notebook legitimately reads 0 there while CPU/MEM still move.
    """
    principal.require(action="monitoring.read")
    sess = await db.get(SessionRow, session_id)
    if sess is None or sess.deleted_at is not None:
        raise NotFound("session", {"session_id": session_id})
    return await session_usage_payload(session_id)


@router.get("/monitoring/sessions/{session_id}/usage/timeseries")
async def session_usage_timeseries(
    session_id: str,
    range_: str = Query(default="15m", alias="range"),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """The four per-session usage metrics over a range, for the monitor detail sparklines."""
    principal.require(action="monitoring.read")
    sess = await db.get(SessionRow, session_id)
    if sess is None or sess.deleted_at is not None:
        raise NotFound("session", {"session_id": session_id})
    return await session_usage_series(session_id, range_)


@router.get("/monitoring/timeseries")
async def monitoring_timeseries(
    panel: str = Query(...),
    range_: str = Query(default="1h", alias="range"),
    node: str | None = Query(default=None),
    gpu: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
):
    """One panel over a time range. The panel id selects the query; the console never sends PromQL."""
    principal.require(action="monitoring.read")
    if range_ not in _RANGES:
        raise _BadPanel("unknown range", {"range": range_})
    span, step = _RANGES[range_]
    query, spec = _render(panel, node, gpu)
    import time

    now = int(time.time())
    data = await _prom("/api/v1/query_range", {
        "query": query, "start": now - span, "end": now, "step": step,
    })
    return {
        "panel": panel, "unit": spec["unit"], "range": range_, "step": step,
        "series": _series(data, spec["legend"], "values"),
    }


@router.get("/monitoring/instant")
async def monitoring_instant(
    panel: str = Query(...),
    node: str | None = Query(default=None),
    gpu: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
):
    """Current value per series — the snapshot table and the tiles."""
    principal.require(action="monitoring.read")
    query, spec = _render(panel, node, gpu)
    data = await _prom("/api/v1/query", {"query": query})
    return {"panel": panel, "unit": spec["unit"], "series": _series(data, spec["legend"], "value")}


@router.get("/monitoring/gpu-inventory")
async def monitoring_gpu_inventory(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """The ledger's view of each card: mode, allocated VRAM/cores, and the sessions holding it.

    DCGM cannot attribute a shared card's usage to a session (HAMi splits below its visibility), so
    session attribution comes from the control plane instead of being guessed from metrics.
    """
    principal.require(action="monitoring.read")
    rows = (
        await db.execute(
            select(GpuDevice, GpuNode.hostname)
            .join(GpuNode, GpuNode.id == GpuDevice.node_id, isouter=True)
            .order_by(GpuNode.hostname.asc(), GpuDevice.id.asc())
        )
    ).all()
    live = (
        await db.execute(
            select(Allocation.device_id, SessionRow.id, SessionRow.name, SessionRow.status,
                   SessionRow.gpu_mem_mb, SessionRow.gpu_cores)
            .join(SessionRow, SessionRow.id == Allocation.session_id)
            .where(Allocation.ended_at.is_(None))
        )
    ).all()
    by_device: dict[str, list[dict[str, Any]]] = {}
    for dev_id, sid, name, status, mem, cores in live:
        if dev_id:
            by_device.setdefault(dev_id, []).append(
                {"id": sid, "name": name, "status": status, "gpu_mem_mb": mem, "gpu_cores": cores}
            )
    return {
        "data": [
            {
                "id": d.id,
                "gpu_uuid": d.gpu_uuid,
                "model": d.model,
                "node": hostname,
                "mode": d.mode,
                "desired_mode": d.desired_mode,
                "status": d.status,
                "total_mem_mb": d.total_mem_mb,
                "used_mem_mb": d.used_mem_mb,
                "used_cores": d.used_cores,
                "sessions": by_device.get(d.id, []),
            }
            for d, hostname in rows
        ]
    }
