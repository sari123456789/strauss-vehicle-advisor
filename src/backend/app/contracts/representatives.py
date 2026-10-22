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


class HandlingStatus(StrEnum):
    """Assignment ownership as seen by the current representative — derived, not stored."""

    UNCLAIMED = "unclaimed"
    ASSIGNED_TO_ME = "assigned_to_me"
    ASSIGNED_TO_OTHER = "assigned_to_other"
    COMPLETED = "completed"


class RepresentativeFieldCode(StrEnum):
    """Server-recognized facts a representative may supply (provisional allowlist)."""

    ALTERNATIVE_PARTICIPATION = "alternative.participation"
    OCCUPATION_WAIVER_POLICY = "occupation.waiverPolicy"
    ENTITLEMENT_GROUP = "employee.entitlementGroup"
    VEHICLE_PROPULSION = "currentArrangement.propulsion"
    VEHICLE_TAX_KEY = "currentArrangement.vehicle"
    GENERAL_QUESTION = "general.question"
    """An open question about company cars, answered in text; never applied to a calculation."""


class ExpectedValueType(StrEnum):
    MONEY = "money"
    ENTITLEMENT_GROUP = "entitlement_group"
    PROPULSION = "propulsion"
    TAX_KEY = "tax_key"
    BOOLEAN = "boolean"
    TEXT = "text"


class AnswerOutcome(StrEnum):
    ANSWERED = "answered"
    CANNOT_DETERMINE = "cannot_determine"


class AnswerApplication(StrEnum):
    APPLIED = "applied"
    NOT_APPLICABLE = "not_applicable"


QuestionText = Annotated[str, StringConstraints(min_length=1, max_length=1000)]


RequestSource = Literal["chat", "form"]
"""Where the employee asked: the advisor chat or the stand-alone request form."""


class CreateQuestionRequest(ContractModel):
    field_code: RepresentativeFieldCode
    option_key: OptionKeyStr | None = None
    question_text: QuestionText
    idempotency_key: UUID
    expected_case_version: int = Field(ge=0)
    source: RequestSource = "chat"


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
    text: str | None = None
    """The representative's full text answer, for `general.question` requests only."""


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


class RequestEmployeeView(ContractModel):
    """Who the request is about, for the handling representative (decision 23). Never salary,
    payroll or other profile values."""

    display_name: str | None = None
    employee_code: str | None = None
    occupation: str | None = None
    current_vehicle: str | None = None


class RepresentativeRequestView(ContractModel):
    """Representative-visible, minimal context only (no employee profile or salary).

    Backward-compatible: all new fields are Optional with safe defaults so existing
    Angular consumers continue to work without changes.
    """

    request_id: UUID
    request_version: int = Field(ge=0)
    status: RequestStatus
    field_code: RepresentativeFieldCode
    expected_value_type: ExpectedValueType
    question_text: QuestionText
    vehicle: VehicleContext | None = None
    entitlement_group: EntitlementGroup | None = None
    created_at: datetime
    # --- new fields (all Optional for backward compatibility) ---
    updated_at: datetime | None = None
    handling_status: HandlingStatus | None = None
    handler_display_name: str | None = None
    """Display name of the current (or last) handler; never login or subject_id."""
    claimed_at: datetime | None = None
    assignment_version: int | None = None
    employee: RequestEmployeeView | None = None
    source: RequestSource = "chat"
    can_claim: bool = False
    can_answer: bool = False
    can_release: bool = False


class ClaimRequest(ContractModel):
    operation_id: UUID
    expected_request_version: int = Field(ge=0)


class ClaimResult(ContractModel):
    assignment_id: UUID
    assignment_version: int = Field(ge=0)
    claimed_at: datetime


class ReleaseRequest(ContractModel):
    operation_id: UUID
    expected_assignment_version: int = Field(ge=0)


class ReleaseResult(ContractModel):
    assignment_id: UUID
    released_at: datetime


class AssignmentFilter(StrEnum):
    ALL = "all"
    UNCLAIMED = "unclaimed"
    MINE = "mine"
    OTHERS = "others"


class RepresentativeSummary(ContractModel):
    """Accurate counts over the entire queue, independent of pagination."""

    total: int
    unclaimed: int
    assigned_to_me: int
    assigned_to_others: int
    answered: int
    cannot_determine: int
    cancelled: int


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


class MyRequestView(ContractModel):
    """One of the signed-in employee's own representative requests, with its answer."""

    request_id: UUID
    status: RequestStatus
    field_code: RepresentativeFieldCode
    question_text: QuestionText
    source: RequestSource = "chat"
    created_at: datetime
    updated_at: datetime | None = None
    answer: AnswerView | None = None
