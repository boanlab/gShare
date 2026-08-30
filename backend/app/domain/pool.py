"""Per-card pool transitions (the hami-core half of the drain state machine).

An admin sets ``desired_mode`` on a card; placement stops immediately (mode_state=draining, the
scheduler filters on mode_state=="ready"). This helper is called wherever device occupancy is
released: once the card is empty, a fractional↔exclusive change is pure metadata and applies on
the spot. A ↔mig change additionally needs the node-side geometry work (the mig-agent Job of the
dynamic-rebalancing workstream), so it stays ``draining`` until that machinery confirms.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.db.models import GpuDevice

log = get_logger(__name__)

_METADATA_MODES = {"fractional", "exclusive"}


def maybe_apply_drained_mode(dev: GpuDevice) -> None:
    """Apply a pending pool change when the card just became empty (caller holds FOR UPDATE)."""
    if dev.mode_state != "draining" or not dev.desired_mode:
        return
    if dev.used_mem_mb or dev.used_cores or dev.lend_state:
        return  # not empty yet — keep draining
    if dev.desired_mode in _METADATA_MODES and (dev.mode in _METADATA_MODES or not dev.mode):
        old = dev.mode
        dev.mode = dev.desired_mode
        dev.mode_state = "ready"
        log.info("gpu pool applied: device=%s %s -> %s", dev.id, old, dev.mode)
    # hami-core↔mig transitions wait for the node-side geometry change (mode_state stays
    # draining; the rebalancer flips it to applying and back to ready).
