"""Map the supplied rules JSON + derived demo policy into the Domain `RulesConfig`.

Business-policy support is validated by `app.business.rules.validator`, invoked by the
Application/BL layer (Infrastructure depends on Domain only).
"""

from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.domain.errors import DomainError
from app.domain.rules import (
    DerivedPolicy,
    ElectricRule,
    EntitlementGroup,
    FamilyTrackRule,
    GrossUpSettings,
    InsuranceSchedule,
    ParticipationRule,
    PensionCreditRule,
    RulesConfig,
    SourceReference,
    SupportLimits,
    TaxBracket,
    WaiverRule,
)
from app.domain.values import to_decimal
from app.infrastructure.imports.rules_json_loader import (
    JsonDocument,
    RulesFormatError,
    read_json_document,
)


def _get(document: Mapping[str, Any], path: str) -> Any:
    current: Any = document
    for key in path.split("."):
        if not isinstance(current, Mapping) or key not in current:
            raise RulesFormatError(f"missing required key: {path}")
        current = current[key]
    return current


def _dec(document: Mapping[str, Any], path: str) -> Decimal:
    return to_decimal(_get(document, path), field=path)


def _int(document: Mapping[str, Any], path: str) -> int:
    value = _get(document, path)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RulesFormatError(f"{path}: expected an integer")
    return value


def _bool(document: Mapping[str, Any], path: str) -> bool:
    value = _get(document, path)
    if not isinstance(value, bool):
        raise RulesFormatError(f"{path}: expected a boolean")
    return value


def _str(document: Mapping[str, Any], path: str) -> str:
    value = _get(document, path)
    if not isinstance(value, str):
        raise RulesFormatError(f"{path}: expected a string")
    return value


def _insurance(document: Mapping[str, Any], path: str) -> InsuranceSchedule:
    return InsuranceSchedule(
        threshold=_dec(document, f"{path}.threshold"),
        ceiling=_dec(document, f"{path}.ceiling"),
        low_rate=_dec(document, f"{path}.lowRate"),
        high_rate=_dec(document, f"{path}.highRate"),
    )


def _brackets(document: Mapping[str, Any]) -> tuple[TaxBracket, ...]:
    raw = _get(document, "netMethod.brackets")
    if not isinstance(raw, list):
        raise RulesFormatError("netMethod.brackets: expected a list")
    brackets = []
    for index, item in enumerate(raw):
        if not isinstance(item, list) or len(item) != 2:
            raise RulesFormatError(f"netMethod.brackets[{index}]: expected [upperBound, rate]")
        upper, rate = item
        brackets.append(
            TaxBracket(
                upper_bound=None if upper is None else to_decimal(upper, field="bracket"),
                rate=to_decimal(rate, field="bracket.rate"),
            )
        )
    return tuple(brackets)


def build_rules_config(rules: JsonDocument, policy: JsonDocument) -> RulesConfig:
    src, pol = rules.content, policy.content
    if _str(pol, "appliesToSource.sha256") != rules.sha256:
        raise RulesFormatError("derived policy was written for a different rules source (hash)")
    try:
        budgets_raw = _get(src, "budgets")
        if not isinstance(budgets_raw, Mapping):
            raise RulesFormatError("budgets: expected an object")
        budgets = {
            EntitlementGroup(group): to_decimal(amount, field=f"budgets.{group}")
            for group, amount in budgets_raw.items()
        }
        group_order = tuple(EntitlementGroup(g) for g in _get(src, "groupOrder"))
        family_cars = _get(src, "familyTrack.cars")
        return RulesConfig(
            source=SourceReference(_str(src, "version"), rules.sha256),
            derived_source=SourceReference(_str(pol, "derivedPolicyVersion"), policy.sha256),
            budgets=budgets,
            group_order=group_order,
            choose_from=_str(src, "chooseFrom"),
            electric=ElectricRule(_dec(src, "electric.netBonus"), _str(src, "electric.method")),
            family_track=FamilyTrackRule(
                min_children=_int(src, "familyTrack.minChildren"),
                child_max_age=_int(src, "familyTrack.childMaxAge"),
                catalogue_ids=tuple(str(c) for c in family_cars),
                price_source=_str(src, "familyTrack.priceSource"),
                gap_split_employee=_dec(src, "familyTrack.gapSplitEmployee"),
            ),
            waiver=WaiverRule(
                allowed_by=_str(src, "waiver.allowedBy"),
                maintenance_is_taxable=_bool(src, "waiver.maintenanceIsTaxable"),
                maintenance_is_pensionable=_bool(src, "waiver.maintenanceIsPensionable"),
            ),
            unused_budget=_str(src, "unusedBudget"),
            net_basis=_str(src, "netMethod.basis"),
            tax_year=_int(src, "netMethod.taxYear"),
            credit_point_value=_dec(src, "netMethod.creditPointValue"),
            brackets=_brackets(src),
            national_insurance=_insurance(src, "netMethod.nationalInsurance"),
            health_insurance=_insurance(src, "netMethod.health"),
            pension_credit=PensionCreditRule(
                rate=_dec(src, "netMethod.pensionCredit.rate"),
                max_contribution_rate=_dec(src, "netMethod.pensionCredit.maxContributionRate"),
                income_ceiling=_dec(src, "netMethod.pensionCredit.incomeCeiling"),
            ),
            contributions_base=_str(src, "netMethod.contributionsBase"),
            participation=ParticipationRule(
                treatment=_str(src, "netMethod.participation.treatment"),
                reduces_value_of_use=_bool(src, "netMethod.participation.reducesValueOfUse"),
            ),
            rounding_during=_str(src, "netMethod.rounding.during"),
            display_rounding=_str(src, "netMethod.rounding.display"),
            derived=DerivedPolicy(
                version=_str(pol, "derivedPolicyVersion"),
                family_participation_mode=_str(pol, "policy.familyParticipationMode"),
                allowance_insurable=_bool(pol, "policy.allowanceInsurable"),
                gross_up_insurable=_bool(pol, "policy.grossUpInsurable"),
                contributions_exclude_vehicle_benefits=_bool(
                    pol, "policy.contributionsExcludeVehicleBenefits"
                ),
                child_age_comparison=_str(pol, "policy.childAgeComparison"),
            ),
            gross_up=GrossUpSettings(
                net_tolerance=_dec(pol, "calculation.grossUp.netTolerance"),
                max_iterations=_int(pol, "calculation.grossUp.maxIterations"),
                initial_upper_bound=_dec(pol, "calculation.grossUp.initialUpperBound"),
                max_upper_bound=_dec(pol, "calculation.grossUp.maxUpperBound"),
            ),
            limits=SupportLimits(
                max_monthly_amount=_dec(pol, "supportLimits.maxMonthlyAmount"),
                max_credit_points=_dec(pol, "supportLimits.maxCreditPoints"),
                max_children=_int(pol, "supportLimits.maxChildren"),
                max_alternatives_per_run=_int(pol, "supportLimits.maxAlternativesPerRun"),
            ),
            value_of_use_key=tuple(str(k) for k in _get(src, "valueOfUse.key")),
        )
    except (DomainError, ValueError, TypeError) as error:
        raise RulesFormatError(f"invalid rules content: {error}") from error


def load_rules_config(rules_path: Path, policy_path: Path) -> RulesConfig:
    return build_rules_config(read_json_document(rules_path), read_json_document(policy_path))
