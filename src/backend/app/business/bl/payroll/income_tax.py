"""Demonstration progressive income tax (monthly, 2025 tables from the rules JSON)."""

from collections.abc import Sequence
from decimal import Decimal

from app.business.domain.rules import TaxBracket
from app.business.domain.values import ZERO


def progressive_tax(taxable_base: Decimal, brackets: Sequence[TaxBracket]) -> Decimal:
    """Marginal sum: each rate applies only to the portion of the base inside its band."""
    tax = ZERO
    lower = ZERO
    for bracket in brackets:
        upper = (
            taxable_base if bracket.upper_bound is None else min(taxable_base, bracket.upper_bound)
        )
        if upper > lower:
            tax += (upper - lower) * bracket.rate
        if bracket.upper_bound is None or taxable_base <= bracket.upper_bound:
            break
        lower = bracket.upper_bound
    return tax


def income_tax_after_credits(
    tax_before_credits: Decimal, credit_points_credit: Decimal, pension_credit: Decimal
) -> Decimal:
    """Credits cannot create a negative tax / cash payout."""
    return max(ZERO, tax_before_credits - credit_points_credit - pension_credit)
