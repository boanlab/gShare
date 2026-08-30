"""pool_rebalancer — drives hami-core↔mig card transitions. Interval 30s.

The hami-core-internal half of the drain machine (fractional↔exclusive) applies inline when a
card empties (app.domain.pool). This worker handles the half that needs node work:

  draining (desired mig↔hami-core, card empty)
      → create a GpuModeChange CR → mode_state=applying
  applying
      → CR Failed  → mode_state=error (admin retries from the console)
      → CR Succeeded AND inventory reports the new per-card mode → mode=desired,
        mode_state=ready, CR deleted

The card's observed mode comes back through the normal inventory sync (HAMi's register
annotation), so "ready" always means HAMi itself agrees with the ledger.
"""
from __future__ import annotations

from sqlalchemy import select

from app.cluster.crd import GpuModeChangeCRD
from app.core.logging import get_logger
from app.db.base import get_sessionmaker
from app.db.models import GpuDevice, GpuNode
from app.domain.pool import maybe_apply_drained_mode

log = get_logger(__name__)

# desired (ledger pool) → HAMi backend the node must run.
_TARGET_BACKEND = {"mig": "mig", "fractional": "hami-core", "exclusive": "hami-core"}


def _needs_node_work(dev: GpuDevice) -> bool:
    """mig↔hami-core requires the geometry Job; within hami-core it is metadata (pool.py)."""
    return (dev.desired_mode == "mig") != (dev.mode == "mig")


async def run() -> None:
    maker = get_sessionmaker()
    async with maker() as db:
        crd = GpuModeChangeCRD(db)
        rows = (
            await db.scalars(
                select(GpuDevice).where(GpuDevice.mode_state.in_(("draining", "applying")))
            )
        ).all()
        for dev in rows:
            try:
                await _step(db, crd, dev)
            except Exception:  # noqa: BLE001 — per-card isolation; retry next tick
                log.exception("pool rebalance step failed device=%s", dev.id)
        await db.commit()


async def _step(db, crd: GpuModeChangeCRD, dev: GpuDevice) -> None:
    if not dev.desired_mode:
        dev.mode_state = "ready"
        return

    if dev.mode_state == "draining":
        if not _needs_node_work(dev):
            # Metadata transition (within hami-core). maybe_apply_drained_mode applies it when a
            # session RELEASES the card, but a card that is already empty has no such trigger and
            # would drain forever — apply it here too.
            maybe_apply_drained_mode(dev)
            return
        if dev.used_mem_mb or dev.used_cores or dev.lend_state:
            return  # still occupied — keep draining
        node = await db.get(GpuNode, dev.node_id)
        if node is None:
            return
        # The card's index on its node: HAMi reports devices in index order and inventory keeps
        # insertion order per node, so derive it from the sibling ordering.
        siblings = (
            await db.scalars(
                select(GpuDevice.gpu_uuid)
                .where(GpuDevice.node_id == dev.node_id)
                .order_by(GpuDevice.created_at, GpuDevice.id)
            )
        ).all()
        try:
            gpu_index = list(siblings).index(dev.gpu_uuid)
        except ValueError:
            gpu_index = 0
        await crd.apply_change(
            dev.cluster_id, device_id=dev.id, node_name=node.hostname,
            gpu_uuid=dev.gpu_uuid, gpu_index=gpu_index,
            target_mode=_TARGET_BACKEND[dev.desired_mode],
        )
        dev.mode_state = "applying"
        log.info("pool rebalance: mode change requested device=%s -> %s", dev.id, dev.desired_mode)
        return

    # applying: poll the CR + the observed mode.
    change = await crd.get_change(dev.cluster_id, dev.id)
    phase = ((change or {}).get("status") or {}).get("phase")
    if phase == "Failed":
        dev.mode_state = "error"
        log.warning("pool rebalance FAILED device=%s: %s", dev.id,
                    ((change or {}).get("status") or {}).get("message"))
        return
    if phase != "Succeeded":
        return  # Pending/Running — wait
    # The Job succeeded; wait until inventory confirms HAMi sees the new backend, then finish.
    want_mig = dev.desired_mode == "mig"
    if (dev.mode == "mig") == want_mig:
        dev.mode = dev.desired_mode
        dev.mode_state = "ready"
        await crd.delete_change(dev.cluster_id, dev.id)
        log.info("pool rebalance complete device=%s mode=%s", dev.id, dev.mode)
