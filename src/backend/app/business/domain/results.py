"""Missing-information records and reason codes."""

from dataclasses import dataclass
from enum import StrEnum


class MissingReason(StrEnum):
    MISSING = "missing"
    NEEDS_CLARIFICATION = "needs_clarification"


@dataclass(frozen=True, slots=True)
class MissingFact:
    field: str
    """Field path, e.g. "payroll.bonusGross" or "alternative.participation"."""
    reason: MissingReason
    option_key: str | None = None
