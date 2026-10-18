"""Family-track eligibility: at least `minChildren` qualifying children (approved rule: a
child counts until the 24th birthday; the stored count already applies that rule). Group
ordering never rejects the family track. Unknown count -> needs information, never zero.
"""

from app.business.bl.facts import require_usable
from app.business.domain.eligibility import (
    EligibilityDecision,
    EligibilityFacts,
    EligibilityReason,
    EligibilityStatus,
)
from app.business.domain.results import MissingFact
from app.business.domain.rules import RulesConfig

CHILDREN_FIELD = "employee.qualifyingChildrenCount"


def evaluate_family(
    option_key: str, facts: EligibilityFacts, rules: RulesConfig
) -> EligibilityDecision:
    missing: list[MissingFact] = []
    children = require_usable(facts.qualifying_children_count, CHILDREN_FIELD, missing)
    if children is None:
        return EligibilityDecision(
            option_key,
            EligibilityStatus.NEEDS_INFORMATION,
            (EligibilityReason.FAMILY_CHILDREN_UNKNOWN,),
            tuple(missing),
        )
    if children >= rules.family_track.min_children:
        return EligibilityDecision(
            option_key, EligibilityStatus.ELIGIBLE, (EligibilityReason.FAMILY_CHILDREN_MET,)
        )
    return EligibilityDecision(
        option_key, EligibilityStatus.INELIGIBLE, (EligibilityReason.FAMILY_CHILDREN_NOT_MET,)
    )
