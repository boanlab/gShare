"""Unit tests for the scheduler's pure logic: SchedulerService._validate_balance and
_reserve_target.

Both are pure arithmetic with no database or cluster involved, so the request and device objects are
faked with SimpleNamespace and the methods are called directly.

- _validate_balance: rejects asymmetric fractional allocations such as 1 GB with 100% of the cores
  (anti-fragmentation), accepts balanced tiers, and skips cpu and exclusive sessions entirely.
- _reserve_target: exclusive reserves one completely empty card at full-card capacity; fractional
  best-fits the slice; neither finds anything, the result is (None, 0, 0).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.core.errors import ImbalancedAllocation
from app.domain.scheduler import SchedulerService

REF = settings.GPU_REFERENCE_MEM_MB  # reference full-card VRAM; in a mixed fleet this differs per model


def _svc() -> SchedulerService:
    """A bare instance, so methods can be called without __init__ pulling in Handoff, Redis, and the
    rest."""
    return SchedulerService.__new__(SchedulerService)


def _req(mem_mb, cores, mode="fractional", resource_class="gpu"):
    return SimpleNamespace(
        resource_class=resource_class,
        mode=mode,
        gpu_mem_mb=mem_mb,
        gpu_cores=cores,
    )


def _dev(used_mem, used_cores, total_mem=REF, total_cores=100, did="dev"):
    return SimpleNamespace(
        id=did,
        gpu_uuid=f"GPU-{did}",
        used_mem_mb=used_mem,
        used_cores=used_cores,
        total_mem_mb=total_mem,
        total_cores=total_cores,
    )


# ── _validate_balance ───────────────────────────────────────────────
def test_balance_rejects_asymmetric_cores():
    """1 GB of VRAM with 100% of the cores: a core share of 1.0 against a VRAM share of ~0.02, which
    must raise ImbalancedAllocation (422)."""
    with pytest.raises(ImbalancedAllocation) as ei:
        _svc()._validate_balance(_req(1024, 100), REF)
    assert ei.value.http == 422
    assert ei.value.code == "imbalanced_allocation"


def test_balance_passes_proportional_tier():
    """A proportional allocation such as the M tier — a quarter of the VRAM and 25% of the cores —
    passes."""
    quarter = round(REF * 0.25)
    # Passing means raising nothing.
    _svc()._validate_balance(_req(quarter, 25), REF)


def test_balance_passes_full_tier():
    """The L tier — half the VRAM, 50% of the cores — passes as well."""
    half = round(REF * 0.5)
    _svc()._validate_balance(_req(half, 50), REF)


def test_balance_skips_exclusive_and_cpu():
    """exclusive and cpu sessions do not take a fraction of a card, so the check is skipped even for
    asymmetric values."""
    _svc()._validate_balance(_req(1024, 100, mode="exclusive"), REF)
    _svc()._validate_balance(_req(1024, 100, resource_class="cpu"), REF)


def test_balance_uses_model_capacity_not_global():
    """A smaller ref_mem (the chosen model's full card) makes the same VRAM a larger share, which
    changes the balance verdict.

    Against a 12 GB card, 3 GB with 25% of the cores is a 0.25 VRAM share and a 0.25 core share, so
    it passes. """
    small_card = 12 * 1024
    _svc()._validate_balance(_req(3 * 1024, 25), small_card)


# ── _reserve_target ─────────────────────────────────────────────────
def test_reserve_exclusive_takes_fully_free_card_full_capacity():
    """Exclusive: pick a completely empty card and occupy its whole capacity, blocking
    co-tenancy."""
    busy = _dev(used_mem=1000, used_cores=10, did="busy")
    free = _dev(used_mem=0, used_cores=0, did="free")
    target, eff_mem, eff_cores = SchedulerService._reserve_target(
        [busy, free], req_mem=4096, req_cores=25, exclusive=True
    )
    assert target is free                       # only the empty card is a candidate
    assert (eff_mem, eff_cores) == (free.total_mem_mb, free.total_cores)  # the full card is occupied


def test_reserve_exclusive_none_when_no_free_card():
    """Exclusive: with no completely empty card there is nothing to reserve, so (None, 0, 0) — the
    caller queues the session."""
    target, eff_mem, eff_cores = SchedulerService._reserve_target(
        [_dev(used_mem=1, used_cores=0)], req_mem=4096, req_cores=25, exclusive=True
    )
    assert (target, eff_mem, eff_cores) == (None, 0, 0)


def test_reserve_fractional_best_fit():
    """Fractional: among the cards the slice fits on, pick the one with the least VRAM left, which
    minimises fragmentation."""
    roomy = _dev(used_mem=0, used_cores=0, total_mem=40000, did="roomy")       # 40000 free
    tight = _dev(used_mem=30000, used_cores=0, total_mem=40000, did="tight")   # 10000 free
    target, eff_mem, eff_cores = SchedulerService._reserve_target(
        [roomy, tight], req_mem=8000, req_cores=50, exclusive=False
    )
    assert target is tight                      # best fit is the tighter card
    assert (eff_mem, eff_cores) == (8000, 50)   # the requested slice is occupied verbatim


def test_reserve_fractional_none_when_no_fit():
    """Fractional: a slice that fits on no card yields (None, 0, 0)."""
    target, eff_mem, eff_cores = SchedulerService._reserve_target(
        [_dev(used_mem=39000, used_cores=90, total_mem=40000)],
        req_mem=8000, req_cores=50, exclusive=False,
    )
    assert (target, eff_mem, eff_cores) == (None, 0, 0)
