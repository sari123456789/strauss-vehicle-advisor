"""Demo seed on real PostgreSQL (decision Q8, option A) and the bridge to the reference results.

Seeded facts are usable for calculation but never employee-confirmed (lastConfirmedAt null);
the demo origin is kept internally. The bridge test loads the seeded profiles from the DB,
builds the scenarios from the stored reference data (catalogue, complete-key value of use,
occupation allowance) and checks the existing BL results against Agent Spec §8.
"""

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from app.bootstrap import build_demo_seed_reader, build_seed_components
from app.business.application.ports.demo_seed import DemoSeedSource
from app.business.application.seeding.demo_seed_service import (
    DemoSeedConflictError,
    DemoSeedNotAuthorizedError,
    DemoSeedReport,
    DemoSeedService,
    employee_subject_id,
)
from app.business.bl.comparisons.builder import build_comparison
from app.business.bl.display_rounding import display_round
from app.business.domain.arrangements import (
    AlternativeVehicleSpec,
    ArrangementKind,
    BaselineSpec,
    CurrentAllowanceSpec,
    CurrentCompanyCarSpec,
    WaiverSpec,
)
from app.business.domain.cases import FactPath, profile_fact, with_profile_fact
from app.business.domain.comparisons import ScenarioStatus
from app.business.domain.employee_profiles import EmployeeProfile
from app.business.domain.provenance import FieldStatus, FieldValue, SourceType
from app.business.domain.rules import EntitlementGroup
from app.business.domain.vehicles import Propulsion
from app.contracts.tool_context import ActorRef, Origin, Role
from tests.integration.business.database.case_helpers import (
    Persistence,
    latest_release,
    persistence,
    usable,
)
from tests.integration.business.database.conftest import IMPORT_OPERATOR
from tests.integration.business.database.db_helpers import run, sql
from tests.unit.business.bl.helpers import rules
from tests.unit.business.bl.test_reference_employees import ALTERNATIVE, DELTA_EXCEPTIONS, SPEC_RAW

pytestmark = pytest.mark.sources

EMPLOYEES = ("E-001", "E-002", "E-003", "E-004", "E-005", "E-006")
FAKE_ID_NUMBERS = ("312456783", "209876549", "301122339", "287654321", "334455664", "255566770")


def _seed(url: str, *, dry_run: bool = False, actor: ActorRef = IMPORT_OPERATOR) -> DemoSeedReport:
    async def scenario() -> DemoSeedReport:
        components = build_seed_components(url)
        try:
            return await components.service.seed(actor, dry_run=dry_run)
        finally:
            await components.engine.dispose()

    return run(scenario())


def _namespace() -> Any:
    return build_demo_seed_reader().read().uuid_namespace


async def _profiles(p: Persistence) -> dict[str, EmployeeProfile]:
    namespace = _namespace()
    result = {}
    async with p.uow() as uow:
        for ref in EMPLOYEES:
            stored = await uow.profiles.get(employee_subject_id(namespace, ref))
            assert stored is not None, ref
            result[ref] = stored
    return result


@pytest.fixture(scope="module")
def seeded(business_db: str) -> DemoSeedReport:
    return _seed(business_db)


def test_seed_creates_accounts_and_profiles_with_demo_provenance(
    business_db: str, seeded: DemoSeedReport
) -> None:
    assert seeded.profiles_created == EMPLOYEES and seeded.profiles_existing == ()
    assert set(seeded.users_created) == {
        "e001",
        "e002",
        "e003",
        "e004",
        "e005",
        "e006",
        "rep01",
        "rep02",
        "ops01",
    }

    async def scenario() -> None:
        async with persistence(business_db) as p:
            profiles = await _profiles(p)
            e1 = profiles["E-001"]
            assert e1.version == 1
            assert e1.salary.base_gross.value == Decimal("18500")
            assert e1.salary.pension_employee_rate.value == Decimal("0.06")
            assert e1.salary.study_fund_employee_rate.value == Decimal("0.025")
            assert e1.salary.credit_points.value == Decimal("2.75")
            assert e1.entitlement_group.value is EntitlementGroup.GIMEL
            assert e1.occupation_id.value == "OCC-PRODUCT-MGR"
            assert e1.qualifying_children_count.value == 2
            current = e1.current_arrangement
            assert current.kind.value is ArrangementKind.COMPANY_CAR
            assert (
                current.vehicle.value is not None and current.vehicle.value.catalogue_id == "CAR-09"
            )
            assert current.propulsion.value is Propulsion.HYBRID
            assert current.propulsion.source is SourceType.CATALOGUE
            assert current.participation.value == Decimal(300)
            assert current.family_deduction.status is FieldStatus.MISSING
            e3 = profiles["E-003"]
            assert e3.current_arrangement.kind.value is ArrangementKind.ALLOWANCE
            assert e3.current_arrangement.allowance.value == Decimal(1500)
            assert e3.current_arrangement.vehicle.status is FieldStatus.MISSING
            assert e3.current_arrangement.participation.status is FieldStatus.MISSING
            assert e3.salary.pension_employee_rate.value == Decimal("0.065")

            async with p.uow() as uow:
                origin = await uow.profiles.get_origin(e1.employee_id)
                account = await uow.users.get(e1.employee_id)
            assert origin is not None and origin["kind"] == "demo_seed"
            assert origin["employeeRef"] == "E-001" and origin["seedVersion"] == "demo-seed-v1"
            assert account is not None and account.display_name == "נועה לוי"
            assert account.roles == frozenset({Role.EMPLOYEE})
            for prof in profiles.values():
                for path in FactPath:
                    field = profile_fact(prof, path)
                    assert field.last_confirmed_at is None, path  # never employee-confirmed
                    assert field.source_message_id is None, path
                    if field.status is FieldStatus.USABLE:
                        # all profiles of one seed run share the seed time recorded in origin
                        assert field.updated_at is not None
                        assert field.updated_at.isoformat() == origin["seededAt"], path

    run(scenario())


def test_no_fictional_id_number_is_stored(business_db: str, seeded: DemoSeedReport) -> None:
    dump = sql(
        business_db,
        "SELECT string_agg(fields::text || coalesce(origin::text, ''), '|') "
        "FROM business.employee_profiles",
    )[0][0] + str(sql(business_db, "SELECT string_agg(u::text, '|') FROM auth.users u")[0][0])
    for fake_id in FAKE_ID_NUMBERS:
        assert fake_id not in dump


def test_reseeding_is_idempotent_and_never_overwrites_profiles(
    business_db: str, seeded: DemoSeedReport
) -> None:
    async def change_e002() -> None:
        async with persistence(business_db) as p:
            e2 = (await _profiles(p))["E-002"]
            async with p.uow() as uow:
                await uow.profiles.update(
                    with_profile_fact(e2, FactPath.BASE_GROSS, usable(Decimal("12345.00")))
                )
                await uow.commit()

    run(change_e002())
    again = _seed(business_db)
    assert again.profiles_created == () and again.profiles_existing == EMPLOYEES
    assert again.users_created == () and len(again.users_existing) == 9

    async def check() -> None:
        async with persistence(business_db) as p:
            e2 = (await _profiles(p))["E-002"]
        assert e2.salary.base_gross.value == Decimal("12345.00") and e2.version == 2

    run(check())


class _ChangedReader:
    def __init__(self, change: Any) -> None:
        self._change = change

    def read(self) -> DemoSeedSource:
        source = build_demo_seed_reader().read()
        return self._change(source)


def _misnamed_vehicle(source: DemoSeedSource) -> DemoSeedSource:
    first = source.employees[0]
    moved = replace(first, current=replace(first.current, catalogue_id="CAR-10"))
    return replace(source, employees=(moved, *source.employees[1:]))


def _wrong_allowance(source: DemoSeedSource) -> DemoSeedSource:
    employees = tuple(
        replace(e, current=replace(e.current, allowance=Decimal(1400)))
        if e.current.kind is ArrangementKind.ALLOWANCE
        else e
        for e in source.employees
    )
    return replace(source, employees=employees)


@pytest.mark.parametrize("change", [_misnamed_vehicle, _wrong_allowance])
def test_inconsistent_seed_fails_before_any_write(
    business_db: str, seeded: DemoSeedReport, change: Any
) -> None:
    before = sql(business_db, "SELECT count(*) FROM auth.users")

    async def scenario() -> None:
        async with persistence(business_db) as p:
            service = DemoSeedService(_ChangedReader(change), p.reference, p.uow)
            await service.seed(IMPORT_OPERATOR, dry_run=False)

    with pytest.raises(DemoSeedConflictError):
        run(scenario())
    assert sql(business_db, "SELECT count(*) FROM auth.users") == before


def test_seeding_requires_the_import_operator_role(business_db: str) -> None:
    employee = IMPORT_OPERATOR.model_copy(
        update={"roles": frozenset({Role.EMPLOYEE}), "origin": Origin.HTTP}
    )
    with pytest.raises(DemoSeedNotAuthorizedError):
        _seed(business_db, actor=employee)


def test_dry_run_writes_nothing(components: Any, clean_database: str) -> None:
    run(components.service.run(IMPORT_OPERATOR))
    report = _seed(clean_database, dry_run=True)
    assert report.dry_run and report.profiles_created == EMPLOYEES
    assert sql(clean_database, "SELECT count(*) FROM auth.users") == [(0,)]
    assert sql(clean_database, "SELECT count(*) FROM business.employee_profiles") == [(0,)]


def _three(amount: Decimal) -> str:
    return str(amount.quantize(Decimal("0.001")))


async def _scenarios(
    p: Persistence, profile: EmployeeProfile, employee_ref: str
) -> tuple[BaselineSpec, Any]:
    release = await latest_release(p)
    catalogue, dataset = release.catalogue_version_id, release.tax_dataset_version_id
    current = profile.current_arrangement
    baseline: BaselineSpec
    if current.kind.value is ArrangementKind.ALLOWANCE:
        baseline = CurrentAllowanceSpec(allowance=current.allowance)
    else:
        assert current.vehicle.value is not None and current.vehicle.value.catalogue_id
        car = await p.reference.get_vehicle(catalogue, current.vehicle.value.catalogue_id)
        assert car is not None
        record = await p.reference.get_value_of_use(dataset, car.tax_key)
        assert record is not None
        baseline = CurrentCompanyCarSpec(
            track=car.track,
            propulsion=current.propulsion,
            value_of_use=FieldValue.usable(record.value_of_use, SourceType.TAX_DATASET),
            participation=current.participation,
            catalogue_id=car.catalogue_id,
        )
    target = ALTERNATIVE[employee_ref]
    if target == "waiver":
        occupation_id = profile.occupation_id.value
        assert occupation_id is not None
        occupation = await p.reference.get_occupation(catalogue, occupation_id)
        assert occupation is not None
        return baseline, WaiverSpec(
            allowance=FieldValue.usable(occupation.waiver_allowance, SourceType.CATALOGUE)
        )
    car = await p.reference.get_vehicle(catalogue, target)
    assert car is not None
    record = await p.reference.get_value_of_use(dataset, car.tax_key)
    assert record is not None and profile.entitlement_group.value is not None
    return baseline, AlternativeVehicleSpec(
        option_key=car.option_key,
        catalogue_id=car.catalogue_id,
        track=car.track,
        propulsion=car.propulsion,
        value_of_use=FieldValue.usable(record.value_of_use, SourceType.TAX_DATASET),
        participation=FieldValue.usable(car.default_participation, SourceType.CATALOGUE),
        monthly_company_cost=car.monthly_company_cost,
        group_budget=FieldValue.usable(
            rules().budgets[profile.entitlement_group.value], SourceType.RULES
        ),
    )


def test_seeded_profiles_reproduce_the_reference_calculations(
    components: Any, clean_database: str
) -> None:
    """Bridge: DB profiles + DB reference data -> existing BL -> Agent Spec §8 values."""
    run(components.service.run(IMPORT_OPERATOR))
    _seed(clean_database)

    async def scenario() -> dict[str, tuple[str, str, str, Decimal]]:
        async with persistence(clean_database) as p:
            results = {}
            for ref, profile in (await _profiles(p)).items():
                baseline, alt = await _scenarios(p, profile, ref)
                outcome = build_comparison(profile.salary, baseline, [alt], rules())
                assert outcome.baseline is not None and outcome.baseline.result is not None
                scenario_outcome = outcome.alternatives[0]
                assert scenario_outcome.status is ScenarioStatus.CALCULATED, ref
                assert scenario_outcome.result is not None and scenario_outcome.delta is not None
                results[ref] = (
                    _three(outcome.baseline.result.net),
                    _three(scenario_outcome.result.net),
                    _three(scenario_outcome.delta),
                    display_round(scenario_outcome.delta),
                )
            return results

    results = run(scenario())
    for ref in EMPLOYEES:
        current_net, alternative_net, delta, shown = results[ref]
        assert (current_net, alternative_net, delta) == SPEC_RAW[ref], ref
        if ref in DELTA_EXCEPTIONS:
            assert shown == DELTA_EXCEPTIONS[ref][1]  # E-003 -1939, E-006 +722
