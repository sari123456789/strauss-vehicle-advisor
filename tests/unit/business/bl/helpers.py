"""Test builders for Business Logic tests (no persistence, no HTTP)."""

from decimal import Decimal
from functools import cache

from app.business.domain.arrangements import AlternativeVehicleSpec, CurrentCompanyCarSpec
from app.business.domain.employee_profiles import SalaryFacts
from app.business.domain.payroll import ArrangementComponents, PayrollCalculationInput, PayrollFacts
from app.business.domain.provenance import FieldValue, SourceType
from app.business.domain.rules import RulesConfig
from app.business.domain.vehicles import Propulsion, Track
from app.business.infrastructure.imports.rules_loader import load_rules_config
from tests.paths import REPO_ROOT

RULES_PATH = REPO_ROOT / "data" / "source" / "business-rules.json"
POLICY_PATH = REPO_ROOT / "data" / "rules" / "derived-demo-policy.json"


@cache
def rules() -> RulesConfig:
    return load_rules_config(RULES_PATH, POLICY_PATH)


def d(value: str | int) -> Decimal:
    return Decimal(value)


def usable[T](value: T, source: SourceType = SourceType.EMPLOYEE) -> FieldValue[T]:
    return FieldValue.usable(value, source)


def salary(
    base: str = "18500",
    commissions: str = "0",
    bonus: str = "0",
    pension: str = "0.06",
    study: str = "0.025",
    credits: str = "2.75",
) -> SalaryFacts:
    return SalaryFacts(
        base_gross=usable(d(base)),
        commissions_gross=usable(d(commissions)),
        bonus_gross=usable(d(bonus)),
        pension_employee_rate=usable(d(pension)),
        study_fund_employee_rate=usable(d(study)),
        credit_points=usable(d(credits)),
    )


def facts(**overrides: str) -> PayrollFacts:
    values = {
        "base_gross": "18500",
        "commissions_gross": "0",
        "bonus_gross": "0",
        "pension_employee_rate": "0.06",
        "study_fund_employee_rate": "0.025",
        "credit_points": "2.75",
    }
    values.update(overrides)
    return PayrollFacts(**{k: d(v) for k, v in values.items()})


def calc_input(
    payroll: PayrollFacts, components: ArrangementComponents | None = None, gross_up: str = "0"
) -> PayrollCalculationInput:
    return PayrollCalculationInput(payroll, components or ArrangementComponents(), d(gross_up))


def vehicle(
    catalogue_id: str,
    value_of_use: str,
    participation: str,
    cost: str,
    propulsion: Propulsion = Propulsion.PETROL,
    track: Track = Track.REGULAR,
    budget: str | None = None,
) -> AlternativeVehicleSpec:
    return AlternativeVehicleSpec(
        option_key=f"vehicle:{catalogue_id}",
        catalogue_id=catalogue_id,
        track=track,
        propulsion=propulsion,
        value_of_use=usable(d(value_of_use), SourceType.TAX_DATASET),
        participation=usable(d(participation), SourceType.CATALOGUE),
        monthly_company_cost=d(cost),
        group_budget=usable(d(budget), SourceType.RULES) if budget is not None else FieldValue(),
        participation_source="catalogue_default",
    )


def company_car(
    value_of_use: str,
    participation: str,
    propulsion: Propulsion = Propulsion.PETROL,
    track: Track = Track.REGULAR,
) -> CurrentCompanyCarSpec:
    return CurrentCompanyCarSpec(
        track=track,
        propulsion=usable(propulsion),
        value_of_use=usable(d(value_of_use), SourceType.TAX_DATASET),
        participation=usable(d(participation)),
    )
