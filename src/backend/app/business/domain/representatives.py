"""Representative requests and answers. A pending request blocks only its dependent fact or
alternative, never the conversation.

Answers are case-local (B3a, decision Q1): an applied answer changes only the case it was
asked in - an alternative's participation or a case representative fact - and never the
shared EmployeeProfile. An answer applies only while its effective dates cover the case's
reference date; no dates means no restriction (agent-spec T36).
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from app.business.domain.cases import FactPath
from app.business.domain.errors import DomainError, InvalidValueError
from app.business.domain.json_values import JsonObject, freeze_object
from app.business.domain.rules import EntitlementGroup
from app.business.domain.vehicles import Propulsion

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPTION_KEY = re.compile(r"^(vehicle:[A-Z]{2,5}-\d{2,4}|waiver)$")
MAX_QUESTION_LENGTH = 1000


class InvalidStateTransitionError(DomainError):
    code = "INVALID_STATE_TRANSITION"


class RequestStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    CANNOT_DETERMINE = "cannot_determine"
    CANCELLED = "cancelled"


class RepresentativeFieldCode(StrEnum):
    """Server-recognized facts a representative may supply (provisional allowlist)."""

    ALTERNATIVE_PARTICIPATION = "alternative.participation"
    OCCUPATION_WAIVER_POLICY = "occupation.waiverPolicy"
    ENTITLEMENT_GROUP = "employee.entitlementGroup"
    VEHICLE_PROPULSION = "currentArrangement.propulsion"
    VEHICLE_TAX_KEY = "currentArrangement.vehicle"


class ExpectedValueType(StrEnum):
    MONEY = "money"
    ENTITLEMENT_GROUP = "entitlement_group"
    PROPULSION = "propulsion"
    TAX_KEY = "tax_key"
    BOOLEAN = "boolean"


@dataclass(frozen=True, slots=True)
class RepresentativeRequest:
    request_id: UUID
    case_id: UUID
    field_code: RepresentativeFieldCode
    expected_value_type: ExpectedValueType
    question_text: str
    subject_snapshot: JsonObject
    """Minimal question context only (e.g. catalogue id, group); never salary data."""
    subject_fingerprint: str
    case_version_at_creation: int
    request_version: int
    status: RequestStatus
    idempotency_key: UUID
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    alternative_id: UUID | None = None
    alternative_version: int | None = None
    option_key: str | None = None

    def __post_init__(self) -> None:
        if not 1 <= len(self.question_text) <= MAX_QUESTION_LENGTH:
            raise InvalidValueError(f"question text must be 1-{MAX_QUESTION_LENGTH} characters")
        if not _SHA256.fullmatch(self.subject_fingerprint):
            raise InvalidValueError("subject fingerprint must be a lowercase SHA-256 hex digest")
        if (self.alternative_id is None) != (self.alternative_version is None):
            raise InvalidValueError("alternative id and version are given together")
        if self.option_key is not None and not _OPTION_KEY.fullmatch(self.option_key):
            raise InvalidValueError(f"invalid option key {self.option_key!r}")
        for name in ("case_version_at_creation", "request_version", "alternative_version"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or value < 0):
                raise InvalidValueError(f"{name} must be a non-negative integer")
        for name in ("created_at", "updated_at"):
            if getattr(self, name).utcoffset() is None:
                raise InvalidValueError(f"{name} must be timezone-aware")
        object.__setattr__(
            self, "subject_snapshot", freeze_object(self.subject_snapshot, path="subject")
        )

    def with_status(self, status: RequestStatus, *, at: datetime) -> "RepresentativeRequest":
        """Only an open request changes status; any other transition is rejected."""
        if self.status is not RequestStatus.OPEN or status is RequestStatus.OPEN:
            raise InvalidStateTransitionError(
                f"representative request cannot move from {self.status} to {status}"
            )
        return replace(self, status=status, updated_at=at)


class AnswerOutcome(StrEnum):
    ANSWERED = "answered"
    CANNOT_DETERMINE = "cannot_determine"


class AnswerApplication(StrEnum):
    """The effect of an `answered` answer on its case (architecture §17)."""

    APPLIED = "applied"
    NOT_APPLICABLE = "not_applicable"


class NotApplicableReason(StrEnum):
    OUTSIDE_EFFECTIVE_PERIOD = "outside_effective_period"
    ALTERNATIVE_CHANGED = "alternative_changed"


MAX_NOTE_LENGTH = 1000
MAX_VALUE_LENGTH = 200


def covers(effective_from: date | None, effective_to: date | None, day: date) -> bool:
    """True when the (inclusive, optional) effective period contains `day`."""
    return (effective_from is None or effective_from <= day) and (
        effective_to is None or day <= effective_to
    )


def _require_period(effective_from: date | None, effective_to: date | None) -> None:
    if effective_from is not None and effective_to is not None and effective_to < effective_from:
        raise InvalidValueError("effectiveTo must not precede effectiveFrom", field="effectiveTo")


@dataclass(frozen=True, slots=True)
class RepresentativeAnswer:
    """The one answer to a request; immutable (a correction needs a versioned extension)."""

    answer_id: UUID
    request_id: UUID
    case_id: UUID
    outcome: AnswerOutcome
    answered_by: UUID
    answered_at: datetime
    operation_id: UUID
    value: str | None = None
    """Canonical structured value per the request's expected type (e.g. money "350.00")."""
    unit: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    note: str | None = None
    """Free text; never interpreted as a value."""
    application: AnswerApplication | None = None
    """Set for `answered` only."""
    application_reason: NotApplicableReason | None = None

    def __post_init__(self) -> None:
        answered = self.outcome is AnswerOutcome.ANSWERED
        if answered != (self.value is not None):
            raise InvalidValueError("an answered outcome has a value; cannot_determine has none")
        if answered != (self.application is not None):
            raise InvalidValueError("only an answered outcome has an application")
        if (self.application is AnswerApplication.NOT_APPLICABLE) != (
            self.application_reason is not None
        ):
            raise InvalidValueError("a not_applicable answer states its reason")
        if self.value is not None and not 1 <= len(self.value) <= MAX_VALUE_LENGTH:
            raise InvalidValueError(f"value must be 1-{MAX_VALUE_LENGTH} characters")
        if self.unit not in (None, "ILS"):
            raise InvalidValueError("unit must be ILS")
        if self.note is not None and len(self.note) > MAX_NOTE_LENGTH:
            raise InvalidValueError(f"note must be at most {MAX_NOTE_LENGTH} characters")
        _require_period(self.effective_from, self.effective_to)
        if self.answered_at.utcoffset() is None:
            raise InvalidValueError("answered_at must be timezone-aware")


CASE_FACT_PATHS: Mapping[RepresentativeFieldCode, FactPath] = {
    RepresentativeFieldCode.ENTITLEMENT_GROUP: FactPath.ENTITLEMENT_GROUP,
    RepresentativeFieldCode.VEHICLE_PROPULSION: FactPath.CURRENT_PROPULSION,
}
"""Field codes whose applied answer becomes a case representative fact."""


@dataclass(frozen=True, slots=True)
class RepresentativeFact:
    """A case-local fact supplied by a representative (insert-only, with provenance).

    It overrides the employee-reported value in this case only while its effective period
    covers the case's reference date; the shared profile is never changed.
    """

    fact_id: UUID
    case_id: UUID
    request_id: UUID
    path: FactPath
    value: EntitlementGroup | Propulsion
    answered_at: datetime
    effective_from: date | None = None
    effective_to: date | None = None

    def __post_init__(self) -> None:
        expected = {
            FactPath.ENTITLEMENT_GROUP: EntitlementGroup,
            FactPath.CURRENT_PROPULSION: Propulsion,
        }.get(self.path)
        if expected is None:
            raise InvalidValueError(f"{self.path} is not a representative case fact")
        if not isinstance(self.value, expected):
            raise InvalidValueError(f"{self.path}: expected {expected.__name__}")
        _require_period(self.effective_from, self.effective_to)
        if self.answered_at.utcoffset() is None:
            raise InvalidValueError("answered_at must be timezone-aware")

    def applies_on(self, day: date) -> bool:
        return covers(self.effective_from, self.effective_to, day)


def applicable_facts(
    facts: Iterable[RepresentativeFact], day: date
) -> dict[FactPath, RepresentativeFact]:
    """Per path, the latest answered fact whose period covers `day`. A fact outside its
    period never applies, so it can never hide another (valid) value."""
    chosen: dict[FactPath, RepresentativeFact] = {}
    for fact in facts:
        if not fact.applies_on(day):
            continue
        current = chosen.get(fact.path)
        if current is None or (fact.answered_at, str(fact.fact_id)) > (
            current.answered_at,
            str(current.fact_id),
        ):
            chosen[fact.path] = fact
    return chosen


@dataclass(frozen=True, slots=True)
class RepresentativeParticipation:
    """An alternative's participation supplied by a representative (stored in its inputs)."""

    amount: Decimal
    request_id: UUID
    answered_at: datetime
    effective_from: date | None = None
    effective_to: date | None = None

    def __post_init__(self) -> None:
        if not self.amount.is_finite() or self.amount < 0:
            raise InvalidValueError("participation must be a finite non-negative amount")
        _require_period(self.effective_from, self.effective_to)

    def applies_on(self, day: date) -> bool:
        return covers(self.effective_from, self.effective_to, day)
