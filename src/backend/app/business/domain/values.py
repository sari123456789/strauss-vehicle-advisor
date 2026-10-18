"""Decimal value handling. Money is never constructed from a binary float.

Calculations run inside `calculation_context()` (precision 40) regardless of the caller's
global decimal context, so identical inputs always produce identical outputs.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext

from app.business.domain.errors import InvalidValueError, OutOfSupportedRangeError

DECIMAL_PRECISION = 40
ZERO = Decimal(0)
ONE = Decimal(1)


@contextmanager
def calculation_context() -> Iterator[Context]:
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        ctx.rounding = ROUND_HALF_EVEN
        ctx.traps[InvalidOperation] = True
        yield ctx


def to_decimal(value: object, *, field: str = "value") -> Decimal:
    """Parse a str/int/Decimal into a finite Decimal. Floats and booleans are rejected."""
    if isinstance(value, bool | float):
        raise InvalidValueError(f"{field}: binary floats/booleans are not accepted", field=field)
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except InvalidOperation:
            raise InvalidValueError(f"{field}: not a decimal number", field=field) from None
    else:
        raise InvalidValueError(f"{field}: unsupported type {type(value).__name__}", field=field)
    if not result.is_finite():
        raise InvalidValueError(f"{field}: NaN/Infinity are not accepted", field=field)
    return result


def require_non_negative(value: Decimal, field: str) -> Decimal:
    if not value.is_finite() or value < ZERO:
        raise InvalidValueError(f"{field}: must be a finite non-negative amount", field=field)
    return value


def require_rate(value: Decimal, field: str) -> Decimal:
    if not value.is_finite() or not (ZERO <= value <= ONE):
        raise InvalidValueError(f"{field}: rate must be between 0 and 1", field=field)
    return value


def require_at_most(value: Decimal, limit: Decimal, field: str) -> Decimal:
    if value > limit:
        raise OutOfSupportedRangeError(f"{field}: exceeds supported limit {limit}", field=field)
    return value
