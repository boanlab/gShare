"""Rate calculation for compute (CPU) sessions.

A CPU-class session has no GPU, so it is not billed by occupancy share but by an hourly credit rate
proportional to its cpu, mem, and disk. The per-unit rates live in settings and administrators can
change them. GPU sessions are billed separately, as the offering rate multiplied by occupancy.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.core.config import settings


def round_credit(x: Decimal) -> Decimal:
    """Round an hourly credit rate to a whole number. Rates and billed amounts are whole credits
    throughout."""
    return Decimal(x).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def compute_credit_per_hour(cpu: int | None, mem_gb: int | None, disk_gb: int | None) -> Decimal:
    """Hourly credits for a CPU session: cpu, mem, and disk each multiplied by their rate, rounded
    to a whole number."""
    total = (
        Decimal(str(settings.COMPUTE_CREDIT_PER_VCPU)) * Decimal(int(cpu or 0))
        + Decimal(str(settings.COMPUTE_CREDIT_PER_GB_MEM)) * Decimal(int(mem_gb or 0))
        + Decimal(str(settings.COMPUTE_CREDIT_PER_GB_DISK)) * Decimal(int(disk_gb or 0))
    )
    return round_credit(total)
