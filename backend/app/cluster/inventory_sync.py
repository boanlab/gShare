"""InventorySync — reflect operator-reported GPU device inventory into the ledger.

The operator's InventoryReconciler measures device-plugin/DCGM capacity per node and upserts one
GpuDevice at a time. This is a pure *reflection* of measured total capacity; ledger-owned occupancy
(used_mem_mb/used_cores) is the source of truth and is NEVER overwritten from here.
Idempotent: keyed on (cluster_id, gpu_uuid). Python is the only DB writer.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.internal import OperatorGpuDeviceUpsert, OperatorNodeUpsert
from app.core import ids
from app.db.models import Cluster, GpuDevice, GpuNode


class InventorySync:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def upsert_node(self, ev: OperatorNodeUpsert, cluster_id: str) -> None:
        """Upsert a node's capacity, GPU or not. Rows are matched on (cluster_id, hostname), and
        soft-deleted clusters are ignored."""
        async with self.db.begin():
            live = await self.db.scalar(
                select(Cluster.id).where(Cluster.id == cluster_id, Cluster.deleted_at.is_(None))
            )
            if live is None:
                return
            node = (
                await self.db.execute(
                    select(GpuNode).where(
                        GpuNode.cluster_id == cluster_id, GpuNode.hostname == ev.node_id
                    )
                )
            ).scalar_one_or_none()
            if node is None:
                self.db.add(GpuNode(
                    id=ids.new("node"), cluster_id=cluster_id, hostname=ev.node_id, status="ready",
                    cpu=ev.node_cpu, mem=ev.node_mem_gb, disk=ev.node_disk_gb,
                    lossless_capable=ev.lossless_capable,
                    last_seen_at=datetime.now(UTC),
                ))
            else:
                # Liveness: the operator SAW this node just now, whether or not anything changed.
                node.last_seen_at = datetime.now(UTC)
                if ev.node_cpu is not None:
                    node.cpu = ev.node_cpu
                if ev.node_mem_gb is not None:
                    node.mem = ev.node_mem_gb
                if ev.node_disk_gb is not None:
                    node.disk = ev.node_disk_gb
                node.lossless_capable = ev.lossless_capable
                if ev.role:
                    node.role = ev.role

    async def upsert_device(self, ev: OperatorGpuDeviceUpsert, cluster_id: str) -> None:
        async with self.db.begin():
            # 0 Ignore inventory for unknown/soft-deleted clusters. A deregistered cluster's
            # operator keeps a valid token (TTL) and resyncs until redeployed/stopped; without this
            # guard it would resurrect inventory rows and collide with a re-registration of the same
            # hardware (device PK/gpu_uuid are global). 202 + no-op so the operator doesn't spin on
            # errors.
            live = await self.db.scalar(
                select(Cluster.id).where(
                    Cluster.id == cluster_id, Cluster.deleted_at.is_(None)
                )
            )
            if live is None:
                return

            # 1. Upsert the GpuNode by (cluster_id, hostname) rather than by id: the
            #    registration probe (_seed_inventory) may already have seeded it under a
            #    generated ULID, and matching on the hostname is what stops a duplicate
            #    node appearing.
            node = (
                await self.db.execute(
                    select(GpuNode).where(
                        GpuNode.cluster_id == cluster_id,
                        GpuNode.hostname == ev.node_id,
                    )
                )
            ).scalar_one_or_none()
            if node is None:
                node = GpuNode(
                    id=ids.new("node"),
                    cluster_id=cluster_id,
                    hostname=ev.node_id,
                    status="ready",
                    cpu=ev.node_cpu,
                    mem=ev.node_mem_gb,
                    disk=ev.node_disk_gb,
                )
                node.last_seen_at = datetime.now(UTC)
                self.db.add(node)
                await self.db.flush()   # materialise node.id, which GpuDevice references
            else:
                # Liveness: the operator SAW this node just now, whether or not anything changed.
                node.last_seen_at = datetime.now(UTC)
                # Apply the capacity. Status is owned by the health controller and is not
                # overwritten.
                if ev.node_cpu is not None:
                    node.cpu = ev.node_cpu
                if ev.node_mem_gb is not None:
                    node.mem = ev.node_mem_gb
                if ev.node_disk_gb is not None:
                    node.disk = ev.node_disk_gb

            # 2 GpuDevice upsert by (cluster_id, gpu_uuid). Preserve ledger-owned used_*.
            dev = (
                await self.db.execute(
                    select(GpuDevice).where(
                        GpuDevice.cluster_id == cluster_id,
                        GpuDevice.gpu_uuid == ev.uuid,
                    )
                )
            ).scalar_one_or_none()
            if dev is None:
                self.db.add(
                    GpuDevice(
                        id=ev.uuid,                       # stable device key
                        node_id=node.id,                  # the resolved GpuNode.id, matched on hostname to avoid duplicates
                        cluster_id=cluster_id,
                        model=ev.model or "unknown",      # device-plugin capacity carries no model
                        gpu_uuid=ev.uuid,
                        total_mem_mb=ev.total_mem_mb,
                        used_mem_mb=ev.used_mem_mb,        # new device: trust reported (usually 0)
                        total_cores=ev.total_cores or 100,
                        used_cores=ev.used_cores,
                        status=ev.status,
                        mode=ev.mode,
                    )
                )
            else:
                # Reflect capacity/status; DO NOT touch used_* (ledger owns occupancy).
                dev.total_mem_mb = ev.total_mem_mb
                if ev.total_cores:
                    dev.total_cores = ev.total_cores
                dev.status = ev.status
                # Mode is split into OBSERVATION and POLICY. The report decides only the
                # hami-core↔mig axis (a card-level hardware fact); within hami-core the pool
                # (fractional vs exclusive) is GShare policy: desired_mode wins, then the current
                # value, then the reported fallback. This is what stops a node relabel from
                # silently flipping every card's pool under running sessions — inventory no
                # longer writes policy.
                if ev.mode == "mig":
                    dev.mode = "mig"
                elif ev.mode in ("fractional", "exclusive"):
                    if dev.desired_mode in ("fractional", "exclusive"):
                        if dev.mode_state == "ready":
                            dev.mode = dev.desired_mode
                    elif dev.mode in (None, "", "mig"):
                        # No policy set and the card left MIG (or is new): take the report.
                        dev.mode = ev.mode
                if ev.model:
                    dev.model = ev.model
