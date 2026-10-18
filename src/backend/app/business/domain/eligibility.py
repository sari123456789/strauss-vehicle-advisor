"""Eligibility concepts (standard library only). Decisions are made in BL from these facts.

`EligibilityReason` values equal the shared contract's reason tokens. Unknown facts produce
`needs_information`, never a guessed result; ineligible is a business result, not an error.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from app.business.domain.errors import InvalidValueError
from app.business.domain.provenance import FieldValue
from app.business.domain.results import MissingFact
from app.business.domain.rules import EntitlementGroup


class EligibilityStatus(StrEnum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    NEEDS_INFORMATION = "needs_information"


class EligibilityReason(StrEnum):
    GROUP_OWN = "group_own"
    GROUP_LOWER = "group_lower"
    GROUP_HIGHER = "group_higher_than_entitlement"
    GROUP_UNKNOWN = "group_unknown"
    FAMILY_CHILDREN_MET = "family_children_met"
    FAMILY_CHILDREN_NOT_MET = "family_children_not_met"
    FAMILY_CHILDREN_UNKNOWN = "family_children_unknown"
    WAIVER_ALLOWED = "waiver_allowed_by_occupation"
    WAIVER_MANDATORY_CAR = "waiver_not_allowed_mandatory_car"
    OCCUPATION_UNKNOWN = "occupation_unknown"
    OCCUPATION_NOT_IN_TABLE = "occupation_not_in_table"
    OCCUPATION_GROUP_CONFLICT = "occupation_group_conflict"


@dataclass(frozen=True, slots=True)
class EligibilityFacts:
    """The employee facts eligibility depends on (effective: actual + scenario overrides)."""

    entitlement_group: FieldValue[EntitlementGroup] = field(default_factory=FieldValue)
    qualifying_children_count: FieldValue[int] = field(default_factory=FieldValue)
    occupation_id: FieldValue[str] = field(default_factory=FieldValue)


@dataclass(frozen=True, slots=True)
class EligibilityDecision:
    option_key: str
    status: EligibilityStatus
    reasons: tuple[EligibilityReason, ...] = ()
    missing: tuple[MissingFact, ...] = ()

    def __post_init__(self) -> None:
        if self.status is EligibilityStatus.NEEDS_INFORMATION and not (
            self.missing or self.reasons
        ):
            raise InvalidValueError("needs_information requires a reason or missing fact")
