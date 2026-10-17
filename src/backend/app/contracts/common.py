"""Common contract types: base model, value strings, enums, issues and the response envelope.

STATUS: PROVISIONAL - requires joint review (docs/api-contracts.md).
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Generic, Literal, Self, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic.alias_generators import to_camel

CONTRACT_STATUS: Literal["provisional"] = "provisional"
CONTRACT_VERSION = "0.1.0"


class ContractModel(BaseModel):
    """Base for every shared contract: camelCase JSON, unknown fields rejected, immutable."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


# --- Value strings -----------------------------------------------------------------------
# Money and rates travel as decimal strings; JSON numbers (and therefore binary floats) are
# rejected because pydantic does not coerce numbers into `str` fields.

MoneyStr = Annotated[str, StringConstraints(pattern=r"^-?\d+(\.\d+)?$", max_length=40)]
"""Signed decimal string in ILS, e.g. "-1938.681" (raw results may be negative)."""

NonNegativeMoneyStr = Annotated[str, StringConstraints(pattern=r"^\d+(\.\d+)?$", max_length=40)]
"""Non-negative decimal string in ILS, e.g. "18500.00"."""

WholeShekelStr = Annotated[str, StringConstraints(pattern=r"^-?\d+$", max_length=20)]
"""Display value rounded to whole shekels (half away from zero), e.g. "-1939"."""

RateStr = Annotated[str, StringConstraints(pattern=r"^(0(\.\d+)?|1(\.0+)?)$")]
"""Decimal rate between 0 and 1: "0.06" means 6%."""

CreditPointsStr = Annotated[str, StringConstraints(pattern=r"^\d+(\.\d+)?$", max_length=10)]

CalculationMonthStr = Annotated[str, StringConstraints(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
"""Calculation month, YYYY-MM."""

CatalogueIdStr = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2,5}-\d{2,4}$")]
"""Opaque catalogue identifier, e.g. "CAR-10"."""

OccupationIdStr = Annotated[str, StringConstraints(pattern=r"^OCC-[A-Z0-9-]{2,40}$")]

OptionKeyStr = Annotated[str, StringConstraints(pattern=r"^(vehicle:[A-Z]{2,5}-\d{2,4}|waiver)$")]
"""Deterministic option key: "vehicle:CAR-10" or "waiver"."""


# --- Enumerations ------------------------------------------------------------------------


class FieldStatus(StrEnum):
    MISSING = "missing"
    NEEDS_CLARIFICATION = "needs_clarification"
    USABLE = "usable"


class SourceType(StrEnum):
    """Provenance of a value. `document_confirmed` is future-only and deliberately absent."""

    EMPLOYEE = "employee"
    CATALOGUE = "catalogue"
    RULES = "rules"
    TAX_DATASET = "tax_dataset"
    REPRESENTATIVE = "representative"


class EntitlementGroup(StrEnum):
    """Source Hebrew group labels are preserved as values."""

    ALEF = "א"
    BET = "ב"
    GIMEL = "ג"
    DALET = "ד"


class Propulsion(StrEnum):
    PETROL = "petrol"
    HYBRID = "hybrid"
    PLUG_IN_HYBRID = "plug_in_hybrid"
    ELECTRIC = "electric"


class ArrangementKind(StrEnum):
    COMPANY_CAR = "company_car"
    ALLOWANCE = "allowance"
    NO_CAR_NO_ALLOWANCE = "no_car_no_allowance"


class Track(StrEnum):
    REGULAR = "regular"
    FAMILY = "family"


class OptionCategory(StrEnum):
    REGULAR = "regular"
    FAMILY = "family"
    WAIVER = "waiver"


class UpdateMode(StrEnum):
    ACTUAL_UPDATE = "actual_update"
    HYPOTHETICAL = "hypothetical"


class RunKind(StrEnum):
    REGULAR = "regular"
    HYPOTHETICAL = "hypothetical"


class Freshness(StrEnum):
    CURRENT = "current"
    STALE = "stale"


class EnvelopeStatus(StrEnum):
    OK = "ok"
    """Everything requested was completed."""
    PARTIAL = "partial"
    """Some items completed; others are blocked/pending (see issues and per-item statuses)."""
    NEEDS_INFORMATION = "needs_information"
    """Nothing could be completed until missing/ambiguous facts are resolved."""
    ERROR = "error"
    """The operation was rejected or failed; `error` is set and `data` is null."""


class IssueCode(StrEnum):
    MISSING_INPUT = "MISSING_INPUT"
    AMBIGUOUS_INPUT = "AMBIGUOUS_INPUT"
    INELIGIBLE = "INELIGIBLE"
    VEHICLE_NOT_FOUND = "VEHICLE_NOT_FOUND"
    TAX_KEY_AMBIGUOUS = "TAX_KEY_AMBIGUOUS"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"
    STALE_REVISION = "STALE_REVISION"
    PENDING_REPRESENTATIVE = "PENDING_REPRESENTATIVE"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
    UNSUPPORTED_INPUT = "UNSUPPORTED_INPUT"


class ErrorCode(StrEnum):
    """Stable machine-readable failure codes. Never shown raw to employees."""

    MALFORMED_REQUEST = "MALFORMED_REQUEST"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    OPERATION_PAYLOAD_MISMATCH = "OPERATION_PAYLOAD_MISMATCH"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    INVALID_BUSINESS_INPUT = "INVALID_BUSINESS_INPUT"
    INPUT_OUT_OF_SUPPORTED_RANGE = "INPUT_OUT_OF_SUPPORTED_RANGE"
    GROSS_UP_NOT_CONVERGED = "GROSS_UP_NOT_CONVERGED"
    RATE_LIMITED = "RATE_LIMITED"
    INFRA_UNAVAILABLE = "INFRA_UNAVAILABLE"


class FieldPath(StrEnum):
    """Allowlisted fields of the actual EmployeeProfile / case working state."""

    BASE_GROSS = "payroll.baseGross"
    COMMISSIONS_GROSS = "payroll.commissionsGross"
    BONUS_GROSS = "payroll.bonusGross"
    PENSION_EMPLOYEE_RATE = "payroll.pensionEmployeeRate"
    STUDY_FUND_EMPLOYEE_RATE = "payroll.studyFundEmployeeRate"
    CREDIT_POINTS = "payroll.creditPoints"
    OCCUPATION_ID = "employee.occupationId"
    ENTITLEMENT_GROUP = "employee.entitlementGroup"
    QUALIFYING_CHILDREN_COUNT = "employee.qualifyingChildrenCount"
    CURRENT_KIND = "currentArrangement.kind"
    CURRENT_VEHICLE = "currentArrangement.vehicle"
    CURRENT_PROPULSION = "currentArrangement.propulsion"
    CURRENT_PARTICIPATION = "currentArrangement.participation"
    CURRENT_FAMILY_DEDUCTION = "currentArrangement.familyDeduction"
    CURRENT_FAMILY_DEDUCTION_INCLUDED = "currentArrangement.familyDeductionIncludedInParticipation"
    CURRENT_ALLOWANCE = "currentArrangement.allowance"


# --- Issues, errors and the envelope -----------------------------------------------------


class Issue(ContractModel):
    """A business-level observation. Never contains raw exceptions or salary documents."""

    code: IssueCode
    field: FieldPath | None = None
    option_key: OptionKeyStr | None = None
    request_id: UUID | None = None
    reason: str | None = Field(default=None, max_length=200)
    """Stable machine-oriented reason token (e.g. "group_higher_than_entitlement")."""


class ErrorInfo(ContractModel):
    code: ErrorCode
    trace_id: str
    safe_details: dict[str, str] = Field(default_factory=dict)


class SourceVersions(ContractModel):
    rules_snapshot_id: UUID | None = None
    catalogue_version_id: UUID | None = None
    tax_dataset_version_id: UUID | None = None


T = TypeVar("T")


class Envelope(ContractModel, Generic[T]):
    """Uniform tool/service response. Unknown or failed money is never returned as zero."""

    status: EnvelopeStatus
    data: T | None = None
    issues: list[Issue] = Field(default_factory=list)
    error: ErrorInfo | None = None
    case_version: int | None = Field(default=None, ge=0)
    profile_version: int | None = Field(default=None, ge=0)
    source_versions: SourceVersions | None = None
    trace_id: str
    generated_at: datetime | None = None

    @model_validator(mode="after")
    def _error_consistency(self) -> Self:
        if self.status is EnvelopeStatus.ERROR:
            if self.error is None or self.data is not None:
                raise ValueError("status=error requires `error` and forbids `data`")
        elif self.error is not None:
            raise ValueError("`error` is only allowed with status=error")
        return self
