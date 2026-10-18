"""Representative requests (employee side). A pending request blocks only its dependent fact
or alternative, never the conversation. Answers are a later stage.
"""

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.business.domain.errors import DomainError, InvalidValueError
from app.business.domain.json_values import JsonObject, freeze_object

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
