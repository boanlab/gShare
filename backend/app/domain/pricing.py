"""Credit rounding helpers.

Only GPU sessions consume credits (offering rate x occupancy); CPU-class sessions are free —
host CPU, RAM, and disk are governed by resource policies (per-user / per-group quotas), not by
billing. Persistent storage volumes are not billed; policy limits govern them.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def round_credit(x: Decimal) -> Decimal:
    """Round an hourly credit rate to a whole number. Rates and billed amounts are whole credits
    throughout."""
    return Decimal(x).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
