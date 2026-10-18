"""Shared helper: read a FieldValue as a usable value or record why it is not usable."""

from app.business.domain.provenance import FieldStatus, FieldValue
from app.business.domain.results import MissingFact, MissingReason


def require_usable[T](
    field_value: FieldValue[T],
    path: str,
    missing: list[MissingFact],
    option_key: str | None = None,
) -> T | None:
    """Return the value when usable; otherwise append a MissingFact and return None (never 0)."""
    if field_value.status is FieldStatus.USABLE and field_value.value is not None:
        return field_value.value
    reason = (
        MissingReason.NEEDS_CLARIFICATION
        if field_value.status is FieldStatus.NEEDS_CLARIFICATION
        else MissingReason.MISSING
    )
    missing.append(MissingFact(field=path, reason=reason, option_key=option_key))
    return None
