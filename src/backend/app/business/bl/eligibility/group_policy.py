"""Regular-vehicle eligibility: the employee's own group or a lower one (`own_group_or_lower`).

The family track is not a fifth ranked group and is handled by `family_policy`. If the
occupation table assigns a different group than the employee reported, the reported group is
never overwritten: a vehicle is eligible only if it is eligible under both groups,
ineligible only if ineligible under both, and otherwise needs clarification (decision A3).
"""

from app.business.bl.facts import require_usable
from app.business.domain.eligibility import (
    EligibilityDecision,
    EligibilityFacts,
    EligibilityReason,
    EligibilityStatus,
)
from app.business.domain.errors import UnsupportedPolicyError
from app.business.domain.occupations import OccupationPolicy
from app.business.domain.results import MissingFact
from app.business.domain.rules import EntitlementGroup, RulesConfig

SUPPORTED_CHOICE = "own_group_or_lower"
GROUP_FIELD = "employee.entitlementGroup"


def _decide(
    vehicle_group: EntitlementGroup, employee_group: EntitlementGroup, rules: RulesConfig
) -> tuple[EligibilityStatus, EligibilityReason]:
    order = rules.group_order
    vehicle_rank, employee_rank = order.index(vehicle_group), order.index(employee_group)
    if vehicle_rank == employee_rank:
        return EligibilityStatus.ELIGIBLE, EligibilityReason.GROUP_OWN
    if vehicle_rank < employee_rank:
        return EligibilityStatus.ELIGIBLE, EligibilityReason.GROUP_LOWER
    return EligibilityStatus.INELIGIBLE, EligibilityReason.GROUP_HIGHER


def evaluate_regular(
    option_key: str,
    vehicle_group: EntitlementGroup,
    facts: EligibilityFacts,
    occupation: OccupationPolicy | None,
    rules: RulesConfig,
) -> EligibilityDecision:
    if rules.choose_from != SUPPORTED_CHOICE:
        raise UnsupportedPolicyError(f"unsupported group choice policy {rules.choose_from!r}")
    missing: list[MissingFact] = []
    group = require_usable(facts.entitlement_group, GROUP_FIELD, missing)
    if group is None:
        return EligibilityDecision(
            option_key,
            EligibilityStatus.NEEDS_INFORMATION,
            (EligibilityReason.GROUP_UNKNOWN,),
            tuple(missing),
        )
    status, reason = _decide(vehicle_group, group, rules)
    if occupation is None or occupation.group == group:
        return EligibilityDecision(option_key, status, (reason,))
    table_status, _ = _decide(vehicle_group, occupation.group, rules)
    if table_status is status:
        return EligibilityDecision(option_key, status, (reason,))
    return EligibilityDecision(
        option_key,
        EligibilityStatus.NEEDS_INFORMATION,
        (EligibilityReason.OCCUPATION_GROUP_CONFLICT,),
    )
