"""Approved display rounding: whole shekel, half away from zero; deltas round the exact delta."""

from decimal import Decimal

import pytest

from app.business.display_rounding import display_delta, display_round


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("0.5", "1"),
        ("-0.5", "-1"),
        ("2.5", "3"),
        ("-2.5", "-3"),
        ("1.4999", "1"),
        ("-1938.681", "-1939"),
        ("721.700", "722"),
        ("-0.4", "0"),
        ("11031.819", "11032"),
    ],
)
def test_display_round(raw: str, shown: str) -> None:
    assert display_round(Decimal(raw)) == Decimal(shown)
    assert str(display_round(Decimal(raw))) == shown


def test_delta_rounds_the_precise_difference_not_rounded_nets() -> None:
    """E-003: 13108.657 - 15047.338 = -1938.681 -> -1939 (rounded nets would give -1938)."""
    alternative, current = Decimal("13108.657"), Decimal("15047.338")
    assert display_delta(alternative, current) == Decimal(-1939)
    assert display_round(alternative) - display_round(current) == Decimal(-1938)
