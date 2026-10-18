"""Calculation readiness concepts (standard library only).

Readiness is decided from the same scenario specifications the comparison uses, without
calculating payroll. Ineligible is a business result (blocked), distinct from missing facts.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.business.domain.errors import InvalidValueError
from app.business.domain.results import MissingFact


class ReadinessStatus(StrEnum):
    READY = "ready"
    INELIGIBLE = "ineligible"
    NEEDS_INFORMATION = "needs_information"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class ScenarioReadiness:
    option_key: str
    status: ReadinessStatus
    missing: tuple[MissingFact, ...] = ()
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status is ReadinessStatus.READY and self.missing:
            raise InvalidValueError("a ready scenario has no missing facts")


@dataclass(frozen=True, slots=True)
class ReadinessAssessment:
    payroll_missing: tuple[MissingFact, ...]
    """Salary facts missing for every calculation (reported once, not per scenario)."""
    baseline: ScenarioReadiness | None
    """None when the current arrangement is not part of the request."""
    alternatives: tuple[ScenarioReadiness, ...]
