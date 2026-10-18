"""Vehicle-arrangement components, computed once: U, C, F, A and the electric flag.

- Regular vehicle: U from the official key, C = catalogue default/approved override, F = 0.
- Family vehicle: F = gapSplitEmployee x max(0, catalogue monthly cost - group budget), and C is
  charged in addition to F (derived policy `base_plus_gap`).
- Waiver: A = occupation maintenance allowance; U, C, F, E are zero.
- Current company car: actual reported participation; a family deduction is added unless the
  employee confirmed it is already included in the reported participation.
- Unused budget is never paid as salary. Participation never reduces U.
"""

from decimal import Decimal

from app.business.facts import require_usable
from app.domain.arrangements import (
    CURRENT_OPTION_KEY,
    AlternativeVehicleSpec,
    BaselineSpec,
    CurrentAllowanceSpec,
    CurrentCompanyCarSpec,
    CurrentNoCarSpec,
    WaiverSpec,
)
from app.domain.payroll import ArrangementComponents
from app.domain.results import MissingFact
from app.domain.rules import RulesConfig
from app.domain.values import ZERO, calculation_context, require_at_most
from app.domain.vehicles import Propulsion, Track


def family_gap_contribution(
    monthly_company_cost: Decimal, group_budget: Decimal, rules: RulesConfig
) -> Decimal:
    with calculation_context():
        gap = max(ZERO, monthly_company_cost - group_budget)
        return gap * rules.family_track.gap_split_employee


def _is_electric(propulsion: Propulsion, rules: RulesConfig) -> bool:
    return propulsion is Propulsion.ELECTRIC and rules.electric.method == "gross_up"


def _within_limit(amount: Decimal, rules: RulesConfig, path: str) -> Decimal:
    return require_at_most(amount, rules.limits.max_monthly_amount, path)


def alternative_components(
    spec: AlternativeVehicleSpec | WaiverSpec, rules: RulesConfig
) -> ArrangementComponents | tuple[MissingFact, ...]:
    missing: list[MissingFact] = []
    key = spec.option_key
    if isinstance(spec, WaiverSpec):
        allowance = require_usable(spec.allowance, "waiver.allowance", missing, key)
        if allowance is None:
            return tuple(missing)
        return ArrangementComponents(allowance=_within_limit(allowance, rules, "waiver.allowance"))

    value_of_use = require_usable(spec.value_of_use, "alternative.valueOfUse", missing, key)
    participation = require_usable(spec.participation, "alternative.participation", missing, key)
    budget = None
    if spec.track is Track.FAMILY:
        budget = require_usable(spec.group_budget, "employee.entitlementGroup", missing, key)
    if missing or value_of_use is None or participation is None:
        return tuple(missing)

    gap = ZERO
    if spec.track is Track.FAMILY and budget is not None:
        gap = family_gap_contribution(spec.monthly_company_cost, budget, rules)
    return ArrangementComponents(
        value_of_use=value_of_use,
        ordinary_participation=_within_limit(participation, rules, "alternative.participation"),
        family_gap_contribution=gap,
        electric=_is_electric(spec.propulsion, rules),
    )


def baseline_components(
    spec: BaselineSpec, rules: RulesConfig
) -> ArrangementComponents | tuple[MissingFact, ...]:
    missing: list[MissingFact] = []
    key = CURRENT_OPTION_KEY
    if isinstance(spec, CurrentNoCarSpec):
        return ArrangementComponents()
    if isinstance(spec, CurrentAllowanceSpec):
        allowance = require_usable(spec.allowance, "currentArrangement.allowance", missing, key)
        if allowance is None:
            return tuple(missing)
        path = "currentArrangement.allowance"
        return ArrangementComponents(allowance=_within_limit(allowance, rules, path))

    return _company_car_components(spec, rules, missing)


def _company_car_components(
    spec: CurrentCompanyCarSpec, rules: RulesConfig, missing: list[MissingFact]
) -> ArrangementComponents | tuple[MissingFact, ...]:
    key = CURRENT_OPTION_KEY
    propulsion = require_usable(spec.propulsion, "currentArrangement.propulsion", missing, key)
    value_of_use = require_usable(spec.value_of_use, "currentArrangement.vehicle", missing, key)
    participation = require_usable(
        spec.participation, "currentArrangement.participation", missing, key
    )
    family_deduction = ZERO
    if spec.track is Track.FAMILY:
        included = require_usable(
            spec.family_deduction_included_in_participation,
            "currentArrangement.familyDeductionIncludedInParticipation",
            missing,
            key,
        )
        if included is False:
            reported = require_usable(
                spec.family_deduction, "currentArrangement.familyDeduction", missing, key
            )
            family_deduction = reported if reported is not None else ZERO
    if missing or propulsion is None or value_of_use is None or participation is None:
        return tuple(missing)
    return ArrangementComponents(
        value_of_use=value_of_use,
        ordinary_participation=_within_limit(
            participation, rules, "currentArrangement.participation"
        ),
        family_gap_contribution=_within_limit(
            family_deduction, rules, "currentArrangement.familyDeduction"
        ),
        electric=_is_electric(propulsion, rules),
    )
