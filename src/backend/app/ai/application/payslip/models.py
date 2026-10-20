"""Payslip-import models: extracted document text, deterministic candidates and the review draft.

Values are kept as `Decimal` (parsed from the document's own digits, never through float) and
travel to the Business contracts as decimal strings. The draft holds only mapped fields and a
short redacted evidence line per field: never the PDF bytes or the full document text.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from app.contracts.case_inputs import UpdateInputsResult
from app.contracts.common import FieldPath

# The Business profile fields a payslip may propose (all exist in `FieldPath`; none are new).
# Current-arrangement amounts (allowance/participation) and the taxable car benefit are
# deliberately excluded: the benefit is computed by Business from the official tax dataset.
PAYSLIP_FIELDS: tuple[FieldPath, ...] = (
    FieldPath.BASE_GROSS,
    FieldPath.COMMISSIONS_GROSS,
    FieldPath.BONUS_GROSS,
    FieldPath.PENSION_EMPLOYEE_RATE,
    FieldPath.STUDY_FUND_EMPLOYEE_RATE,
    FieldPath.CREDIT_POINTS,
)
MONEY_FIELDS = frozenset({FieldPath.BASE_GROSS, FieldPath.COMMISSIONS_GROSS, FieldPath.BONUS_GROSS})
RATE_FIELDS = frozenset({FieldPath.PENSION_EMPLOYEE_RATE, FieldPath.STUDY_FUND_EMPLOYEE_RATE})


@dataclass(frozen=True, slots=True)
class DocumentText:
    """Normalized text lines per page, in memory only."""

    pages: tuple[tuple[str, ...], ...]
    byte_size: int

    @property
    def page_count(self) -> int:
        return len(self.pages)


class Party(StrEnum):
    EMPLOYEE = "employee"
    EMPLOYER = "employer"
    UNKNOWN = "unknown"


class Period(StrEnum):
    MONTHLY = "monthly"
    CUMULATIVE = "cumulative"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Candidate:
    """One document line that contains numbers, with deterministic hints."""

    candidate_id: str
    page: int
    """1-based page number."""
    line_index: int
    """0-based line index within the page."""
    evidence: str
    """The redacted, length-limited line text."""
    amounts: tuple[Decimal, ...]
    """Plain amounts (no percent sign), in document order."""
    percents: tuple[Decimal, ...]
    """Values printed with a percent sign, as percentages (6.00 means 6%)."""
    keyword_fields: frozenset[FieldPath]
    """Fields whose label keywords appear in the line."""
    party: Party
    period: Period
    excluded: bool
    """Net/total/tax lines that never map to a profile field."""
    suspicious: bool
    """The line contains instruction-like text (possible prompt injection)."""


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FieldStatus(StrEnum):
    EXTRACTED = "extracted"
    """One unambiguous value, confirmed by both the label rules and the AI interpretation."""
    NEEDS_REVIEW = "needs_review"
    """A proposed value that the employee must check."""
    MISSING = "missing"
    CONFLICT = "conflict"
    """Several different values; the employee must choose or type the value."""
    REJECTED = "rejected"
    """Only non-applicable evidence (employer share, cumulative, invalid value)."""
    CORRECTED = "corrected"
    """Typed or chosen by the employee during review."""


APPROVABLE = frozenset({FieldStatus.EXTRACTED, FieldStatus.NEEDS_REVIEW, FieldStatus.CORRECTED})


class FieldIssue(StrEnum):
    MULTIPLE_AMOUNTS_ON_LINE = "multiple_amounts_on_line"
    MULTIPLE_VALUES = "multiple_values"
    EMPLOYER_SHARE_ONLY = "employer_share_only"
    CUMULATIVE_ONLY = "cumulative_only"
    RATE_NOT_PRINTED = "rate_not_printed"
    NEGATIVE_AMOUNT = "negative_amount"
    INVALID_VALUE = "invalid_value"
    NOT_CONFIRMED_BY_AI = "not_confirmed_by_ai"
    AI_ONLY = "ai_only"
    AI_UNAVAILABLE = "ai_unavailable"
    SUSPICIOUS_CONTENT = "suspicious_content"


@dataclass(frozen=True, slots=True)
class Evidence:
    candidate_id: str
    page: int
    line_index: int
    text: str


@dataclass(frozen=True, slots=True)
class DraftField:
    field: FieldPath
    status: FieldStatus
    value: Decimal | None = None
    """Proposed value in profile units (money in ILS, rates as fractions, points)."""
    confidence: Confidence = Confidence.LOW
    evidence: tuple[Evidence, ...] = ()
    alternatives: tuple[Decimal, ...] = ()
    """Other values found for this field, offered to the employee instead of guessing."""
    issues: tuple[FieldIssue, ...] = ()


class DraftStatus(StrEnum):
    REVIEW = "review"
    APPLYING = "applying"
    APPLIED = "applied"


class ExtractionMode(StrEnum):
    AI_ASSISTED = "ai_assisted"
    RULES_ONLY = "rules_only"


class DocumentIssue(StrEnum):
    SUSPICIOUS_CONTENT = "suspicious_content"
    AI_UNAVAILABLE = "ai_unavailable"
    AI_DISABLED = "ai_disabled"
    NO_CANDIDATES = "no_candidates"


@dataclass(frozen=True, slots=True)
class AppliedOperation:
    operation_id: UUID
    request_hash: str
    result: UpdateInputsResult
    applied_at: datetime


@dataclass(frozen=True, slots=True)
class PayslipDraft:
    draft_id: UUID
    owner_subject_id: UUID
    conversation_id: UUID
    version: int
    status: DraftStatus
    created_at: datetime
    expires_at: datetime
    mode: ExtractionMode
    page_count: int
    fields: tuple[DraftField, ...]
    document_issues: tuple[DocumentIssue, ...] = ()
    last_error: str | None = None
    """Payslip error code of the last failed approval, if any."""
    applied: AppliedOperation | None = None

    def field_for(self, path: FieldPath) -> DraftField | None:
        return next((f for f in self.fields if f.field is path), None)


@dataclass(frozen=True, slots=True)
class Correction:
    field: FieldPath
    value: str
    """Decimal string typed by the employee, in profile units (rates as fractions)."""


@dataclass(frozen=True, slots=True)
class ApproveCommand:
    expected_draft_version: int
    expected_profile_version: int
    operation_id: UUID
    fields: tuple[FieldPath, ...] = field(default_factory=tuple)
