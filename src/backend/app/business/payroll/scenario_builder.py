"""Partial salary facts -> complete `PayrollFacts`, or explicit missing/ambiguous facts.

Never turns a missing fact into zero. Hypothetical overrides produce a NEW fact set and never
modify the actual profile facts they are applied to.
"""

from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal

from app.business.facts import require_usable
from app.domain.employee_profiles import SalaryFacts
from app.domain.errors import InvalidValueError
from app.domain.payroll import PayrollFacts
from app.domain.provenance import FieldValue
from app.domain.results import MissingFact
from app.domain.rules import SupportLimits
from app.domain.values import require_at_most

SALARY_FIELD_PATHS: dict[str, str] = {
    "base_gross": "payroll.baseGross",
    "commissions_gross": "payroll.commissionsGross",
    "bonus_gross": "payroll.bonusGross",
    "pension_employee_rate": "payroll.pensionEmployeeRate",
    "study_fund_employee_rate": "payroll.studyFundEmployeeRate",
    "credit_points": "payroll.creditPoints",
}
_MONEY_FIELDS = ("base_gross", "commissions_gross", "bonus_gross")


def with_hypothetical_overrides(
    actual: SalaryFacts, overrides: Mapping[str, FieldValue[Decimal]]
) -> SalaryFacts:
    """Scenario-local copy with overrides applied; `actual` is left untouched."""
    unknown = set(overrides) - set(SALARY_FIELD_PATHS)
    if unknown:
        raise InvalidValueError(f"unknown salary fields: {sorted(unknown)}")
    return replace(actual, **overrides)


def build_payroll_facts(
    salary: SalaryFacts, limits: SupportLimits
) -> PayrollFacts | tuple[MissingFact, ...]:
    missing: list[MissingFact] = []
    values: dict[str, Decimal] = {}
    for attribute, path in SALARY_FIELD_PATHS.items():
        value = require_usable(getattr(salary, attribute), path, missing)
        if value is not None:
            values[attribute] = value
    if missing:
        return tuple(missing)

    for attribute in _MONEY_FIELDS:
        require_at_most(values[attribute], limits.max_monthly_amount, SALARY_FIELD_PATHS[attribute])
    require_at_most(values["credit_points"], limits.max_credit_points, "payroll.creditPoints")
    return PayrollFacts(**values)  # domain invariants: non-negative amounts, rates within 0..1
