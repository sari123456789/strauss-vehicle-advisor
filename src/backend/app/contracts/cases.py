"""Business part of `get_conversation_context` (architecture alias: `get_case_context`).

STATUS: PROVISIONAL. Conversation messages/history are owned by the AI Engine and are composed
by it; this contract carries only business-owned case and profile state.
"""

from datetime import date, datetime
from uuid import UUID

from pydantic import Field

from app.contracts.common import (
    CalculationMonthStr,
    CatalogueIdStr,
    ContractModel,
    FieldPath,
    Freshness,
    OptionKeyStr,
    RunKind,
    Track,
)
from app.contracts.profile import EmployeeProfileView, VehicleRef
from app.contracts.representatives import RepresentativeFieldCode, RequestStatus


class HypotheticalOverride(ContractModel):
    field: FieldPath
    value: VehicleRef | bool | int | str
    source_message_id: UUID | None = None


class AlternativeView(ContractModel):
    alternative_id: UUID
    alternative_version: int = Field(ge=0)
    option_key: OptionKeyStr
    catalogue_id: CatalogueIdStr | None = None
    track: Track | None = None
    active: bool


class OpenRequestSummary(ContractModel):
    request_id: UUID
    status: RequestStatus
    field_code: RepresentativeFieldCode
    option_key: OptionKeyStr | None = None


class RunSummary(ContractModel):
    run_id: UUID
    kind: RunKind
    created_at: datetime
    freshness: Freshness
    option_count: int | None = None
    """How many alternatives the run compared (from the stored result)."""
    best_option_key: str | None = None
    """The calculated alternative with the largest monthly difference from today."""
    best_delta: str | None = None
    """That alternative's monthly difference, the engine's whole-shekel `display` value."""


class CaseContext(ContractModel):
    case_id: UUID
    conversation_id: UUID
    case_version: int = Field(ge=0)
    calculation_month: CalculationMonthStr
    reference_date: date
    profile: EmployeeProfileView
    """Latest actual shared profile (side-panel facts)."""
    salary_profile_version_used: int = Field(ge=0)
    latest_profile_version: int = Field(ge=0)
    profile_mismatch: bool
    """True when the case was initialized/synchronized from an older profile version."""
    hypothetical_overrides: list[HypotheticalOverride] = Field(default_factory=list)
    alternatives: list[AlternativeView] = Field(default_factory=list)
    open_requests: list[OpenRequestSummary] = Field(default_factory=list)
    recent_runs: list[RunSummary] = Field(default_factory=list)
    fields_requiring_confirmation: list[FieldPath] = Field(default_factory=list)
    """Usable actual facts neither confirmed nor updated since this case was created (G4).

    Informational: ask only when relevant to the current goal; never a blanket questionnaire,
    and not a time-based staleness verdict. Distinct from `recentRuns[].freshness`."""
