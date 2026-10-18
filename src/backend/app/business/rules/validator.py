"""Business consistency of a rules snapshot: only policies the engine implements are accepted.

An unsupported value (e.g. a gross electric method, or participation reducing the benefit) fails
explicitly instead of being silently approximated.
"""

from app.domain.errors import UnsupportedPolicyError
from app.domain.rules import RulesConfig


def validate_supported_rules(rules: RulesConfig) -> None:
    expected: list[tuple[str, object, object]] = [
        ("chooseFrom", rules.choose_from, "own_group_or_lower"),
        ("electric.method", rules.electric.method, "gross_up"),
        ("familyTrack.priceSource", rules.family_track.price_source, "catalogue"),
        ("waiver.allowedBy", rules.waiver.allowed_by, "occupation"),
        ("waiver.maintenanceIsTaxable", rules.waiver.maintenance_is_taxable, True),
        ("waiver.maintenanceIsPensionable", rules.waiver.maintenance_is_pensionable, False),
        ("unusedBudget", rules.unused_budget, "not_paid"),
        ("netMethod.basis", rules.net_basis, "monthly"),
        ("netMethod.contributionsBase", rules.contributions_base, "gross"),
        ("participation.treatment", rules.participation.treatment, "net_deduction"),
        ("participation.reducesValueOfUse", rules.participation.reduces_value_of_use, False),
        ("rounding.during", rules.rounding_during, "none"),
        ("rounding.display", rules.display_rounding, "shekel"),
        (
            "derived.familyParticipationMode",
            rules.derived.family_participation_mode,
            "base_plus_gap",
        ),
        (
            "derived.contributionsExcludeVehicleBenefits",
            rules.derived.contributions_exclude_vehicle_benefits,
            True,
        ),
        ("derived.childAgeComparison", rules.derived.child_age_comparison, "age_less_than_24"),
        ("familyTrack.childMaxAge", rules.family_track.child_max_age, 23),
    ]
    problems = [
        f"{name}={actual!r} (supported: {supported!r})"
        for name, actual, supported in expected
        if actual != supported
    ]
    if problems:
        raise UnsupportedPolicyError("unsupported rules policy: " + "; ".join(problems))
