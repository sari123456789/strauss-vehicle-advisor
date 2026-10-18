"""Eligibility of one catalogue vehicle or the waiver option (no I/O, no persistence)."""

from app.business.bl.eligibility.family_policy import evaluate_family
from app.business.bl.eligibility.group_policy import evaluate_regular
from app.business.bl.eligibility.waiver_policy import evaluate_waiver
from app.business.domain.eligibility import EligibilityDecision, EligibilityFacts
from app.business.domain.occupations import OccupationPolicy
from app.business.domain.rules import RulesConfig
from app.business.domain.vehicles import CatalogueVehicle, Track


def evaluate_vehicle(
    vehicle: CatalogueVehicle,
    facts: EligibilityFacts,
    occupation: OccupationPolicy | None,
    rules: RulesConfig,
) -> EligibilityDecision:
    """`occupation` is the table entry for the employee's occupation (if known and listed);
    for regular vehicles it is used only to detect an occupation/group conflict."""
    if vehicle.track is Track.FAMILY:
        return evaluate_family(vehicle.option_key, facts, rules)
    assert vehicle.group is not None  # regular vehicles always carry a group
    return evaluate_regular(vehicle.option_key, vehicle.group, facts, occupation, rules)


def evaluate_waiver_option(
    facts: EligibilityFacts, occupation: OccupationPolicy | None, rules: RulesConfig
) -> EligibilityDecision:
    return evaluate_waiver(facts, occupation, rules)
