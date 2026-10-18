"""Waiver eligibility from the occupation table (`waiver.allowedBy = occupation`).

Mandatory-car occupations are ineligible (a business result, AC12/T21); allowed occupations
are eligible with the table's allowance (T22); an unknown occupation or one absent from the
table needs information or clarification (T23) - never a similar-role guess. An occupation
that is neither mandatory nor waiver-allowed is not defined by the sources and fails
explicitly (decision A8).
"""

from app.business.bl.facts import require_usable
from app.business.domain.arrangements import WAIVER_OPTION_KEY
from app.business.domain.eligibility import (
    EligibilityDecision,
    EligibilityFacts,
    EligibilityReason,
    EligibilityStatus,
)
from app.business.domain.errors import UnsupportedPolicyError
from app.business.domain.occupations import OccupationPolicy
from app.business.domain.results import MissingFact
from app.business.domain.rules import RulesConfig

OCCUPATION_FIELD = "employee.occupationId"
SUPPORTED_WAIVER_SOURCE = "occupation"


def evaluate_waiver(
    facts: EligibilityFacts, occupation: OccupationPolicy | None, rules: RulesConfig
) -> EligibilityDecision:
    """`occupation` is the table entry for the employee's occupation ID, or None if absent."""
    if rules.waiver.allowed_by != SUPPORTED_WAIVER_SOURCE:
        raise UnsupportedPolicyError(f"unsupported waiver policy {rules.waiver.allowed_by!r}")
    missing: list[MissingFact] = []
    occupation_id = require_usable(facts.occupation_id, OCCUPATION_FIELD, missing)
    if occupation_id is None:
        return EligibilityDecision(
            WAIVER_OPTION_KEY,
            EligibilityStatus.NEEDS_INFORMATION,
            (EligibilityReason.OCCUPATION_UNKNOWN,),
            tuple(missing),
        )
    if occupation is None or occupation.occupation_id != occupation_id:
        return EligibilityDecision(
            WAIVER_OPTION_KEY,
            EligibilityStatus.NEEDS_INFORMATION,
            (EligibilityReason.OCCUPATION_NOT_IN_TABLE,),
        )
    if occupation.mandatory_car:
        return EligibilityDecision(
            WAIVER_OPTION_KEY,
            EligibilityStatus.INELIGIBLE,
            (EligibilityReason.WAIVER_MANDATORY_CAR,),
        )
    if occupation.waiver_allowed:
        return EligibilityDecision(
            WAIVER_OPTION_KEY, EligibilityStatus.ELIGIBLE, (EligibilityReason.WAIVER_ALLOWED,)
        )
    raise UnsupportedPolicyError(
        f"occupation {occupation.occupation_id} is neither mandatory-car nor waiver-allowed"
    )
