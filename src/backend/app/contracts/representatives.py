"""Representative workflow contracts. STATUS: PROVISIONAL.

A pending request blocks only the dependent fact/alternative, never the conversation.
Representatives receive only the minimal question context - no profile or salary data.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from app.contracts.common import (
    CatalogueIdStr,
    ContractModel,
    EntitlementGroup,
    OptionKeyStr,
)


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


class AnswerOutcome(StrEnum):
    ANSWERED = "answered"
    CANNOT_DETERMINE = "cannot_determine"


class AnswerApplication(StrEnum):
    APPLIED = "applied"
    NOT_APPLICABLE = "not_applicable"


QuestionText = Annotated[str, StringConstraints(min_length=1, max_length=1000)]


class CreateQuestionRequest(ContractModel):
    field_code: RepresentativeFieldCode
    option_key: OptionKeyStr | None = None
    question_text: QuestionText
    idempotency_key: UUID
    expected_case_version: int = Field(ge=0)


class CreateQuestionResult(ContractModel):
    request_id: UUID
    status: RequestStatus
    created: bool
    """False when an equivalent open request already existed (idempotent replay)."""


class AnswerView(ContractModel):
    outcome: AnswerOutcome
    value: str | None = None
    unit: Literal["ILS"] | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    answered_at: datetime
    application: AnswerApplication | None = None


class RepresentativeRequestLookup(ContractModel):
    """`get_representative_request` (G1): one request of the server-bound case."""

    request_id: UUID


class RequestStatusView(ContractModel):
    """Employee/agent-facing status of one request."""

    request_id: UUID
    status: RequestStatus
    field_code: RepresentativeFieldCode
    option_key: OptionKeyStr | None = None
    answer: AnswerView | None = None
    created_at: datetime
    updated_at: datetime


class VehicleContext(ContractModel):
    catalogue_id: CatalogueIdStr | None = None
    commercial_name: str | None = None
    registration_year: str | None = None


class RepresentativeRequestView(ContractModel):
    """Representative-visible, minimal context only (no employee profile or salary)."""

    request_id: UUID
    request_version: int = Field(ge=0)
    status: RequestStatus
    field_code: RepresentativeFieldCode
    expected_value_type: ExpectedValueType
    question_text: QuestionText
    vehicle: VehicleContext | None = None
    entitlement_group: EntitlementGroup | None = None
    created_at: datetime


class AnswerSubmit(ContractModel):
    operation_id: UUID
    expected_request_version: int = Field(ge=0)
    outcome: AnswerOutcome
    value: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
    """Structured value as a string per expectedValueType (e.g. money "350.00")."""
    unit: Literal["ILS"] | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    note: Annotated[str, StringConstraints(max_length=1000)] | None = None
    """Free text is never interpreted as a numeric value."""

    @model_validator(mode="after")
    def _outcome_value(self) -> Self:
        if self.outcome is AnswerOutcome.ANSWERED and self.value is None:
            raise ValueError("outcome=answered requires value")
        if self.outcome is AnswerOutcome.CANNOT_DETERMINE and self.value is not None:
            raise ValueError("outcome=cannot_determine forbids value")
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effectiveTo must not precede effectiveFrom")
        return self


class RepresentativeAnsweredEvent(ContractModel):
    """Internal Application-to-AI durable input (not a webhook). Schema version 1."""

    event_id: UUID
    schema_version: Literal[1] = 1
    occurred_at: datetime
    request_id: UUID
    case_id: UUID
    alternative_id: UUID | None = None
    request_version: int = Field(ge=0)
    case_version_after: int = Field(ge=0)
