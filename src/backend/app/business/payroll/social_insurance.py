"""Demonstration national insurance / health insurance (two bands, threshold and ceiling)."""

from decimal import Decimal

from app.domain.rules import InsuranceSchedule
from app.domain.values import ZERO


def insurance_charge(insurance_base: Decimal, schedule: InsuranceSchedule) -> Decimal:
    """B = min(T, ceiling); charge = min(B, threshold) x low + max(0, B - threshold) x high."""
    capped = min(insurance_base, schedule.ceiling)
    return (
        min(capped, schedule.threshold) * schedule.low_rate
        + max(ZERO, capped - schedule.threshold) * schedule.high_rate
    )
