"""E2 (approved 2026-10-20): `ReadinessRequest.kind` uses the same fact basis as the comparison.

A hypothetical goal is checked against the case copy with its hypothetical overrides; a regular
goal against the latest actual profile. Omitting `kind` keeps the previous (regular) behavior.
"""

import pytest

from app.contracts.common import EnvelopeStatus, FieldPath, RunKind
from app.contracts.readiness import ReadinessRequest
from tests.integration.business.database.db_helpers import run
from tests.integration.business.database.service_helpers import services, set_, update
from tests.integration.business.database.test_comparison_services import compare, employee

pytestmark = pytest.mark.sources

CAR = "vehicle:CAR-10"
MISSING_PAYROLL = {
    FieldPath.COMMISSIONS_GROSS: "0",
    FieldPath.BONUS_GROSS: "0",
    FieldPath.PENSION_EMPLOYEE_RATE: "0.06",
    FieldPath.STUDY_FUND_EMPLOYEE_RATE: "0.025",
    FieldPath.CREDIT_POINTS: "2.75",
}


def readiness(version: int, kind: RunKind | None = None) -> ReadinessRequest:
    body = {
        "goal": "calculate_net",
        "selection": {"mode": "explicit", "optionKeys": [CAR]},
        "expectedCaseVersion": version,
    }
    if kind is not None:
        body["kind"] = kind.value
    return ReadinessRequest.model_validate(body)


def test_hypothetical_readiness_uses_the_case_overrides(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            who, ctx = await employee(s, salary=False)  # only the base salary is known
            regular = await s.readiness.check_calculation_readiness(ctx, readiness(0))
            assert set(MISSING_PAYROLL) <= set(regular.data.missing_employee_fields)
            assert regular.data.ready_scenario_ids == []

            changes = [set_(field.value, value) for field, value in MISSING_PAYROLL.items()]
            await s.cases.update_inputs(ctx, update("hypothetical", 0, changes))

            hypothetical = await s.readiness.check_calculation_readiness(
                ctx, readiness(1, RunKind.HYPOTHETICAL)
            )
            assert hypothetical.status is EnvelopeStatus.OK
            assert hypothetical.data.ready_scenario_ids == [CAR]
            assert hypothetical.data.missing_employee_fields == []

            # The actual profile is unchanged: a regular goal still needs the facts.
            still = await s.readiness.check_calculation_readiness(
                ctx, readiness(1, RunKind.REGULAR)
            )
            assert set(MISSING_PAYROLL) <= set(still.data.missing_employee_fields)
            assert (await s.profiles.get_profile(who)).commissions_gross.value is None

            # What readiness reports ready, the hypothetical comparison really calculates.
            result = await s.comparisons.compare_scenarios(
                ctx, compare([CAR], kind="hypothetical", case_version=1)
            )
            assert result.data.alternatives[0].net is not None

    run(scenario())


def test_omitted_kind_is_regular(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            _, ctx = await employee(s, salary=False)
            await s.cases.update_inputs(
                ctx, update("hypothetical", 0, [set_(FieldPath.BONUS_GROSS.value, "0")])
            )
            omitted = await s.readiness.check_calculation_readiness(ctx, readiness(1))
            regular = await s.readiness.check_calculation_readiness(
                ctx, readiness(1, RunKind.REGULAR)
            )
            assert omitted.data == regular.data
            assert FieldPath.BONUS_GROSS in omitted.data.missing_employee_fields

    run(scenario())
