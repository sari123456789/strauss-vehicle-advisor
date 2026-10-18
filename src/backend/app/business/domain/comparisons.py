"""Structured comparison outcomes (raw precise amounts; display rounding is applied separately)."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.business.domain.errors import InvalidValueError
from app.business.domain.payroll import PayrollResult
from app.business.domain.results import MissingFact


class ScenarioStatus(StrEnum):
    CALCULATED = "calculated"
    INELIGIBLE = "ineligible"
    NEEDS_INFORMATION = "needs_information"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ScenarioOutcome:
    option_key: str
    status: ScenarioStatus
    result: PayrollResult | None = None
    delta: Decimal | None = None
    """alternative.net - baseline.net at full precision; None without a calculated baseline."""
    missing: tuple[MissingFact, ...] = ()
    reasons: tuple[str, ...] = ()
    error_code: str | None = None

    def __post_init__(self) -> None:
        calculated = self.status is ScenarioStatus.CALCULATED
        if calculated != (self.result is not None):
            raise InvalidValueError("a result is present exactly when the scenario is calculated")
        if self.delta is not None and not calculated:
            raise InvalidValueError("delta requires a calculated scenario")


@dataclass(frozen=True, slots=True)
class ComparisonOutcome:
    baseline: ScenarioOutcome | None
    """None when the current arrangement was not requested."""
    alternatives: tuple[ScenarioOutcome, ...]
    payroll_missing: tuple[MissingFact, ...] = ()
