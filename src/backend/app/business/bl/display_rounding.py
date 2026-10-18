"""Approved display rounding: whole shekels, midpoint away from zero (architecture §12).

Calculations never round; only presentation does. `Decimal.ROUND_HALF_UP` rounds ties away from
zero for negative values too (-0.5 -> -1). Built-in `round()` (banker's rounding) is not used.
"""

from decimal import ROUND_HALF_UP, Decimal

from app.business.domain.values import calculation_context

_WHOLE_SHEKEL = Decimal(1)
_ZERO = Decimal(0)


def display_round(amount: Decimal) -> Decimal:
    with calculation_context():
        rounded = amount.quantize(_WHOLE_SHEKEL, rounding=ROUND_HALF_UP)
    return _ZERO if rounded.is_zero() else rounded  # never display "-0"


def display_delta(alternative_net: Decimal, baseline_net: Decimal) -> Decimal:
    """Round the precise difference - never the difference of rounded nets."""
    with calculation_context():
        return display_round(alternative_net - baseline_net)
