"""BL readiness agrees with the comparison builder; Application fact bases and amounts."""

from datetime import UTC, datetime
from decimal import Decimal
from itertools import product
from uuid import uuid4

from app.business.application.comparisons.facts import FactBasis, effective_profile
from app.business.application.comparisons.mapping import amount
from app.business.bl.comparisons.builder import build_comparison
from app.business.bl.readiness.assessor import assess_readiness
from app.business.domain.arrangements import (
    AlternativeSpec,
    AlternativeVehicleSpec,
    CurrentAllowanceSpec,
    IneligibleSpec,
    WaiverSpec,
)
from app.business.domain.cases import (
    CaseLifecycleStatus,
    ComparisonCase,
    FactPath,
    HypotheticalOverride,
)
from app.business.domain.comparisons import ScenarioStatus
from app.business.domain.employee_profiles import EmployeeProfile, SalaryFacts
from app.business.domain.provenance import FieldValue, SourceType
from app.business.domain.readiness import ReadinessStatus
from app.business.domain.vehicles import Propulsion, Track
from tests.unit.business.bl.helpers import rules, salary, usable

D = Decimal


def vehicle(key: str, *, value: bool = True, participation: bool = True) -> AlternativeVehicleSpec:
    return AlternativeVehicleSpec(
        option_key=key,
        catalogue_id=key.removeprefix("vehicle:"),
        track=Track.REGULAR,
        propulsion=Propulsion.ELECTRIC,
        value_of_use=usable(D("3130"), SourceType.TAX_DATASET) if value else FieldValue(),
        participation=usable(D("500"), SourceType.CATALOGUE) if participation else FieldValue(),
        monthly_company_cost=D("4000"),
    )


def test_ready_exactly_when_calculated() -> None:
    """Property over salary completeness x component completeness x baseline presence."""
    for full_salary, value, participation, with_baseline in product([True, False], repeat=4):
        facts = salary() if full_salary else SalaryFacts(base_gross=usable(D("18500")))
        specs: list[AlternativeSpec] = [
            vehicle("vehicle:CAR-10", value=value, participation=participation),
            WaiverSpec(allowance=usable(D("1500"), SourceType.CATALOGUE)),
            IneligibleSpec("vehicle:CAR-14", ("group_higher_than_entitlement",)),
        ]
        baseline = CurrentAllowanceSpec(allowance=usable(D("1500"))) if with_baseline else None
        readiness = assess_readiness(facts, baseline, specs, rules())
        outcome = build_comparison(facts, baseline, specs, rules())
        for state, result in zip(readiness.alternatives, outcome.alternatives, strict=True):
            assert (state.status is ReadinessStatus.READY) == (
                result.status is ScenarioStatus.CALCULATED
            ), (state, result.status)
            if state.status is ReadinessStatus.INELIGIBLE:
                assert result.status is ScenarioStatus.INELIGIBLE
        if with_baseline:
            assert readiness.baseline is not None and outcome.baseline is not None
            assert (readiness.baseline.status is ReadinessStatus.READY) == (
                outcome.baseline.status is ScenarioStatus.CALCULATED
            )


def test_fact_bases_never_mix_snapshots() -> None:
    now = datetime(2026, 10, 18, tzinfo=UTC)
    owner = uuid4()
    copy = EmployeeProfile(owner, 1, salary=SalaryFacts(base_gross=usable(D("18500"))))
    latest = EmployeeProfile(owner, 2, salary=SalaryFacts(base_gross=usable(D("19000"))))
    override = HypotheticalOverride(FactPath.BASE_GROSS, D("20000"), created_at=now)
    case = ComparisonCase(
        case_id=uuid4(),
        conversation_id=uuid4(),
        owner_subject_id=owner,
        calculation_month="2026-10",
        reference_date=now.date(),
        version=3,
        lifecycle_status=CaseLifecycleStatus.ACTIVE,
        actual_facts=copy,
        created_at=now,
        updated_at=now,
        hypothetical_overrides=(override,),
    )
    regular = effective_profile(FactBasis.ACTUAL, case, latest)
    hypothetical = effective_profile(FactBasis.CASE_COPY_WITH_OVERRIDES, case, latest)
    listing = effective_profile(FactBasis.LATEST_WITH_OVERRIDES, case, latest)
    assert regular is latest  # overrides are never applied to a regular calculation
    assert (hypothetical.version, hypothetical.salary.base_gross.value) == (1, D("20000"))
    assert (listing.version, listing.salary.base_gross.value) == (2, D("20000"))
    assert hypothetical.salary.base_gross.updated_at == now  # stated in this conversation
    assert case.actual_facts.salary.base_gross.value == D("18500")  # nothing is mutated


def test_amounts_keep_precision_and_round_only_for_display() -> None:
    delta = amount(D("-1938.681"))
    assert (delta.raw, delta.display) == ("-1938.681", "-1939")
    assert amount(D("721.700")).display == "722"
    assert amount(D("-0.4")).display == "0"
