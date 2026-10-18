"""Step 4B on real PostgreSQL and the supplied sources: readiness, comparisons, run snapshots,
freshness, idempotency, concurrency and authorization.

Scenarios: AC04/AC05/T58 (six reference employees end to end), T08-T10, AC03, AC07, AC14,
T28, T31, T38-T42, T70, AC15, AC26/AC27, AC31; decisions Q2-Q7 and the lock order.
"""

import asyncio
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.bootstrap import build_demo_seed_reader, build_seed_components
from app.business.application.errors import (
    ForbiddenError,
    InvalidStateTransition,
    NotFoundError,
    PayloadMismatch,
    UnauthenticatedError,
    VersionConflict,
)
from app.business.application.seeding.demo_seed_service import employee_subject_id
from app.business.domain.arrangements import ArrangementKind
from app.business.domain.employee_profiles import (
    CurrentArrangementFacts,
    EmployeeProfile,
    SalaryFacts,
)
from app.business.domain.provenance import FieldValue
from app.business.domain.rules import EntitlementGroup
from app.business.domain.vehicles import VehicleRef
from app.contracts.case_inputs import ConfirmProfileFieldsRequest
from app.contracts.catalogue import ListOptionsRequest
from app.contracts.common import EnvelopeStatus, FieldPath, Freshness, IssueCode, OptionCategory
from app.contracts.comparisons import CompareRequest, ComparisonDetailsRequest, ScenarioStatus
from app.contracts.readiness import ReadinessRequest
from app.contracts.tool_context import ActorRef, Role, ToolContext
from tests.integration.business.database.case_helpers import new_user, request, usable
from tests.integration.business.database.conftest import IMPORT_OPERATOR
from tests.integration.business.database.db_helpers import run, sql
from tests.integration.business.database.service_helpers import (
    Services,
    actor,
    new_case,
    services,
    set_,
    update,
)
from tests.unit.business.bl.test_reference_employees import (
    ALTERNATIVE,
    DELTA_EXCEPTIONS,
    SPEC_RAW,
)

pytestmark = pytest.mark.sources

D = Decimal
MATCHING_OCCUPATION = {
    EntitlementGroup.ALEF: "OCC-PROCUREMENT-COORD",
    EntitlementGroup.BET: "OCC-SOFTWARE-DEV",
    EntitlementGroup.GIMEL: "OCC-PRODUCT-MGR",
    EntitlementGroup.DALET: "OCC-DEPT-MGR",
}
CURRENT_CAR_FIELDS = (
    "currentArrangement.kind",
    "currentArrangement.vehicle",
    "currentArrangement.participation",
)


async def employee(
    s: Services,
    *,
    group: EntitlementGroup | None = EntitlementGroup.GIMEL,
    children: int | None = None,
    occupation: str | None = "",
    salary: bool = True,
    current: CurrentArrangementFacts | None = None,
) -> tuple[ActorRef, ToolContext]:
    if occupation == "" and group is not None:
        occupation = MATCHING_OCCUPATION[group]  # no A3 conflict unless a test wants one
    subject = await new_user(s.p)
    facts = EmployeeProfile(
        employee_id=subject,
        version=1,
        salary=SalaryFacts(
            base_gross=usable(D("18500")),
            commissions_gross=usable(D("0")),
            bonus_gross=usable(D("0")),
            pension_employee_rate=usable(D("0.06")),
            study_fund_employee_rate=usable(D("0.025")),
            credit_points=usable(D("2.75")),
        )
        if salary
        else SalaryFacts(base_gross=usable(D("18500"))),
        occupation_id=FieldValue() if not occupation else usable(occupation),
        entitlement_group=FieldValue() if group is None else usable(group),
        qualifying_children_count=FieldValue() if children is None else usable(children),
        current_arrangement=current
        if current is not None
        else CurrentArrangementFacts(
            kind=usable(ArrangementKind.COMPANY_CAR),
            vehicle=usable(VehicleRef(catalogue_id="CAR-09")),
            participation=usable(D("500")),
        ),
    )
    async with s.p.uow() as uow:
        await uow.profiles.add(facts)
        await uow.commit()
    who = actor(subject)
    ctx, _ = await new_case(s, who)
    return who, ctx


def compare(
    keys: list[str] | None = None,
    *,
    current: bool = False,
    kind: str = "regular",
    case_version: int = 0,
    profile_version: int | None = 1,
    operation_id: UUID | None = None,
    selection: dict[str, Any] | None = None,
) -> CompareRequest:
    body: dict[str, Any] = {
        "selection": selection or {"mode": "explicit", "optionKeys": keys},
        "includeCurrent": current,
        "kind": kind,
        "expectedCaseVersion": case_version,
        "operationId": str(operation_id or uuid4()),
    }
    if profile_version is not None:
        body["expectedProfileVersion"] = profile_version
    return CompareRequest.model_validate(body)


def readiness(goal: str, keys: list[str] | None = None, *, version: int = 0) -> ReadinessRequest:
    body: dict[str, Any] = {"goal": goal, "expectedCaseVersion": version}
    if keys is not None:
        body["selection"] = {"mode": "explicit", "optionKeys": keys}
    return ReadinessRequest.model_validate(body)


async def confirm_current(
    s: Services, ctx: ToolContext, case_version: int, profile_version: int, fields: tuple[str, ...]
) -> tuple[int, int]:
    result = await s.cases.confirm_profile_fields(
        ctx,
        ConfirmProfileFieldsRequest.model_validate(
            {
                "expectedCaseVersion": case_version,
                "expectedProfileVersion": profile_version,
                "operationId": str(uuid4()),
                "fields": list(fields),
            }
        ),
    )
    assert [f.value for f in result.confirmed_fields] == list(fields)
    return result.new_case_version, result.new_profile_version


def three(raw: str) -> str:
    return str(D(raw).quantize(D("0.001")))


# --- AC04/AC05/T58: six reference employees end to end ----------------------------------


@pytest.fixture(scope="module")
def seeded(business_db: str) -> str:
    async def seed() -> None:
        components = build_seed_components(business_db)
        try:
            await components.service.seed(IMPORT_OPERATOR, dry_run=False)
        finally:
            await components.engine.dispose()

    run(seed())
    return business_db


@pytest.mark.parametrize("ref", sorted(SPEC_RAW))
def test_reference_employees_end_to_end(seeded: str, ref: str) -> None:
    async def scenario() -> None:
        async with services(seeded) as s:
            namespace = build_demo_seed_reader().read().uuid_namespace
            who = actor(employee_subject_id(namespace, ref))
            ctx, created = await new_case(s, who)
            profile_version = created.salary_profile_version_used
            option = "waiver" if ALTERNATIVE[ref] == "waiver" else f"vehicle:{ALTERNATIVE[ref]}"
            # Q2: a last-reported current arrangement is not confirmed in this conversation
            first = await s.comparisons.compare_scenarios(
                ctx, compare([option], current=True, profile_version=profile_version)
            )
            assert first.data.current is not None
            assert first.data.current.status is ScenarioStatus.NEEDS_INFORMATION
            assert {i.reason for i in first.data.current.missing_information} == {
                "confirmation_required"
            }
            assert first.data.alternatives[0].status is ScenarioStatus.CALCULATED
            assert first.data.alternatives[0].delta is None  # never a definitive delta
            assert first.status is EnvelopeStatus.PARTIAL
            context = await s.cases.get_context(ctx)
            fields = tuple(
                f.value
                for f in context.fields_requiring_confirmation
                if f.value.startswith("currentArrangement.")
            )
            case_version, profile_version = await confirm_current(
                s, ctx, context.case_version, profile_version, fields
            )
            out = await s.comparisons.compare_scenarios(
                ctx,
                compare(
                    [option],
                    current=True,
                    case_version=case_version,
                    profile_version=profile_version,
                ),
            )
            result = out.data
            current_raw, alternative_raw, delta_raw = SPEC_RAW[ref]
            assert result.current is not None and result.current.net is not None
            alternative = result.alternatives[0]
            assert alternative.net is not None and alternative.delta is not None
            assert three(result.current.net.raw) == current_raw
            assert three(alternative.net.raw) == alternative_raw
            assert three(alternative.delta.raw) == delta_raw
            expected = DELTA_EXCEPTIONS.get(ref, (None, None))[1]
            if expected is not None:  # AC05: round the precise delta (-1939 / +722)
                assert D(alternative.delta.display) == expected
            assert out.status is EnvelopeStatus.OK and result.freshness is Freshness.CURRENT
            details = await s.comparisons.get_comparison_details(
                ctx, ComparisonDetailsRequest(run_id=result.run_id)
            )
            assert details.data.result == result  # T40: stored, never recalculated
            snapshot = details.data.input_snapshot
            assert snapshot["schemaVersion"] == 1 and snapshot["factBasis"] == "actual"
            assert snapshot["selection"] == {"optionKeys": [option], "resultSetId": None}

    run(scenario())


# --- partial results, readiness agreement -----------------------------------------------


def test_alternatives_without_baseline_and_blocked_alternatives(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            _, ctx = await employee(s, group=EntitlementGroup.BET, children=None)
            keys = ["vehicle:CAR-05", "vehicle:CAR-14", "vehicle:CAR-19", "vehicle:CAR-06"]
            ready = await s.readiness.check_calculation_readiness(
                ctx, readiness("calculate_net", keys)
            )
            out = await s.comparisons.compare_scenarios(ctx, compare(keys))
            statuses = {a.option_key: a.status for a in out.data.alternatives}
            assert statuses == {
                "vehicle:CAR-05": ScenarioStatus.CALCULATED,  # T08: no baseline needed
                "vehicle:CAR-14": ScenarioStatus.INELIGIBLE,  # group ד for ב
                "vehicle:CAR-19": ScenarioStatus.NEEDS_INFORMATION,  # children unknown
                "vehicle:CAR-06": ScenarioStatus.CALCULATED,
            }
            assert out.data.current is None and out.status is EnvelopeStatus.PARTIAL
            assert all(a.delta is None for a in out.data.alternatives)
            calculated = [k for k, v in statuses.items() if v is ScenarioStatus.CALCULATED]
            assert ready.data.ready_scenario_ids == calculated  # same planner, same answer
            blocked = {b.option_key: b.reason for b in ready.data.blocked_scenarios}
            assert blocked == {
                "vehicle:CAR-14": IssueCode.INELIGIBLE,
                "vehicle:CAR-19": IssueCode.MISSING_INPUT,
            }
            assert ready.data.baseline_ready is None
            assert FieldPath.QUALIFYING_CHILDREN_COUNT in ready.data.missing_employee_fields
            assert FieldPath.BASE_GROSS not in ready.data.missing_employee_fields

    run(scenario())


def test_pending_representative_blocks_only_its_alternative(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            who, ctx = await employee(s)
            out = await s.comparisons.compare_scenarios(
                ctx, compare(["vehicle:CAR-10", "vehicle:CAR-11"])
            )
            assert {a.status for a in out.data.alternatives} == {ScenarioStatus.CALCULATED}
            async with s.p.uow() as uow:
                alternative = await uow.alternatives.find_active(ctx.case_id, "vehicle:CAR-10")
                assert alternative is not None
                await uow.representative_requests.add_or_get_open(
                    request(ctx.case_id, who.subject_id, alt=alternative)
                )
                await uow.commit()
            ready = await s.readiness.check_calculation_readiness(
                ctx, readiness("calculate_net", ["vehicle:CAR-10", "vehicle:CAR-11"])
            )
            assert ready.data.ready_scenario_ids == ["vehicle:CAR-11"]
            assert ready.data.blocked_scenarios[0].reason is IssueCode.PENDING_REPRESENTATIVE
            assert len(ready.data.pending_representative_fields) == 1
            out = await s.comparisons.compare_scenarios(
                ctx, compare(["vehicle:CAR-10", "vehicle:CAR-11"])
            )
            first, second = out.data.alternatives
            assert first.status is ScenarioStatus.NEEDS_INFORMATION  # T31, AC14
            assert first.missing_information[0].code is IssueCode.PENDING_REPRESENTATIVE
            assert second.status is ScenarioStatus.CALCULATED

    run(scenario())


def test_missing_salary_is_never_zero(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            _, ctx = await employee(s, salary=False)
            ready = await s.readiness.check_calculation_readiness(
                ctx, readiness("calculate_net", ["vehicle:CAR-10"])
            )
            assert not ready.data.scope_complete and ready.data.ready_scenario_ids == []
            assert FieldPath.BONUS_GROSS in ready.data.missing_employee_fields  # T12
            out = await s.comparisons.compare_scenarios(ctx, compare(["vehicle:CAR-10"]))
            assert out.data.alternatives[0].status is ScenarioStatus.NEEDS_INFORMATION  # AC07
            assert out.data.alternatives[0].net is None
            assert out.status is EnvelopeStatus.NEEDS_INFORMATION
            listing = await s.readiness.check_calculation_readiness(ctx, readiness("list_regular"))
            assert listing.data.scope_complete  # T11: listing never needs salary

    run(scenario())


def test_listing_readiness_needs_only_the_listing_facts(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            _, ctx = await employee(s, group=None, children=4, salary=False)
            family = await s.readiness.check_calculation_readiness(ctx, readiness("list_family"))
            assert family.data.scope_complete  # the group is not a family-listing requirement
            regular = await s.readiness.check_calculation_readiness(ctx, readiness("list_regular"))
            assert regular.data.missing_employee_fields == [FieldPath.ENTITLEMENT_GROUP]
            net = await s.readiness.check_calculation_readiness(
                ctx, readiness("calculate_net", ["vehicle:CAR-19"])
            )
            assert FieldPath.ENTITLEMENT_GROUP in net.data.missing_employee_fields  # F budget
            _, waiver_ctx = await employee(s, occupation="OCC-FIELD-SALES-REP")
            waiver = await s.readiness.check_calculation_readiness(
                waiver_ctx, readiness("waiver_eligibility")
            )
            assert waiver.data.blocked_scenarios[0].reason is IssueCode.INELIGIBLE
            assert waiver.data.scope_complete  # a business answer, not missing data

    run(scenario())


# --- Q2 / Q3: baseline reliability --------------------------------------------------------


def test_unreliable_baseline_never_yields_a_delta(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            _, ctx = await employee(s)
            ready = await s.readiness.check_calculation_readiness(
                ctx, readiness("compare_to_current", ["vehicle:CAR-10"])
            )
            assert ready.data.baseline_ready is False
            assert ready.data.ready_scenario_ids == ["vehicle:CAR-10"]
            pending = {i.field for i in ready.issues if i.reason == "confirmation_required"}
            assert pending == {
                FieldPath.CURRENT_KIND,
                FieldPath.CURRENT_VEHICLE,
                FieldPath.CURRENT_PARTICIPATION,
            }
            assert not pending & set(ready.data.missing_employee_fields)  # not "missing"
            assert not pending & set(ready.data.ambiguous_fields)  # not "ambiguous"
            # a reported propulsion that contradicts the catalogue is a conflict (Q3)
            await s.cases.update_inputs(
                ctx,
                update(
                    "actual_update",
                    0,
                    [set_("currentArrangement.propulsion", "electric")],
                    profile_version=1,
                ),
            )
            case_version, profile_version = await confirm_current(s, ctx, 1, 2, CURRENT_CAR_FIELDS)
            out = await s.comparisons.compare_scenarios(
                ctx,
                compare(
                    ["vehicle:CAR-10"],
                    current=True,
                    case_version=case_version,
                    profile_version=profile_version,
                ),
            )
            assert out.data.current is not None
            assert out.data.current.status is ScenarioStatus.NEEDS_INFORMATION
            assert out.data.current.missing_information[0].code is IssueCode.SOURCE_CONFLICT
            assert out.data.alternatives[0].status is ScenarioStatus.CALCULATED
            assert out.data.alternatives[0].delta is None

    run(scenario())


def test_current_car_outside_the_catalogue(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            tang = {
                "taxKey": {
                    "taxYear": "2026",
                    "registrationYear": "2025",
                    "vehicleTypeCode": "2",
                    "manufacturerCode": "01014",
                    "modelCode": "00003",
                }
            }
            _, ctx = await employee(s, current=CurrentArrangementFacts())
            await s.cases.update_inputs(
                ctx,
                update(
                    "actual_update",
                    0,
                    [
                        set_("currentArrangement.kind", "company_car"),
                        set_("currentArrangement.vehicle", tang),
                        set_("currentArrangement.participation", "600.00"),
                    ],
                    profile_version=1,
                ),
            )
            ready = await s.readiness.check_calculation_readiness(
                ctx, readiness("compare_to_current", ["vehicle:CAR-10"], version=1)
            )
            assert ready.data.baseline_ready is False  # propulsion is never inferred (D6)
            assert ready.data.missing_employee_fields == [FieldPath.CURRENT_PROPULSION]
            await s.cases.update_inputs(
                ctx,
                update(
                    "actual_update",
                    1,
                    [set_("currentArrangement.propulsion", "petrol")],
                    profile_version=2,
                ),
            )
            out = await s.comparisons.compare_scenarios(
                ctx,
                compare(["vehicle:CAR-10"], current=True, case_version=2, profile_version=3),
            )
            current = out.data.current
            assert current is not None and current.status is ScenarioStatus.CALCULATED  # T28
            assert current.breakdown is not None
            assert D(current.breakdown.value_of_use.raw) == D("6950")
            assert out.data.alternatives[0].delta is not None

    run(scenario())


# --- actual vs hypothetical, freshness, synchronization ----------------------------------


def test_hypothetical_runs_keep_their_baseline_and_regular_runs_go_stale(
    business_db: str,
) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            who, ctx = await employee(s)
            regular = (await s.comparisons.compare_scenarios(ctx, compare(["vehicle:CAR-10"]))).data
            await s.cases.update_inputs(
                ctx, update("hypothetical", 0, [set_("payroll.baseGross", "20000.00")])
            )
            hypothetical = (
                await s.comparisons.compare_scenarios(
                    ctx, compare(["vehicle:CAR-10"], kind="hypothetical", case_version=1)
                )
            ).data
            assert hypothetical.alternatives[0].breakdown is not None
            assert hypothetical.alternatives[0].breakdown.earned_gross.raw == "20000.00"
            assert (await s.profiles.get_profile(who)).base_gross.value == "18500"  # T39
            # an actual correction in another conversation (T38, AC15)
            other, _ = await new_case(s, who)
            await s.cases.update_inputs(
                other,
                update(
                    "actual_update", 0, [set_("payroll.baseGross", "19000.00")], profile_version=1
                ),
            )

            async def freshness(run_id: UUID) -> Freshness:
                out = await s.comparisons.get_comparison_details(
                    ctx, ComparisonDetailsRequest(run_id=run_id)
                )
                return out.data.result.freshness

            assert await freshness(regular.run_id) is Freshness.STALE
            assert await freshness(hypothetical.run_id) is Freshness.CURRENT  # fixed baseline
            with pytest.raises(VersionConflict):  # stale expected profile version
                await s.comparisons.compare_scenarios(
                    ctx, compare(["vehicle:CAR-10"], case_version=1, profile_version=1)
                )
            again = await s.comparisons.compare_scenarios(  # T70: synchronize, then calculate
                ctx, compare(["vehicle:CAR-10"], case_version=1, profile_version=2)
            )
            assert again.data.case_version_used == 2 and again.data.profile_version_used == 2
            breakdown = again.data.alternatives[0].breakdown
            assert breakdown is not None and breakdown.earned_gross.raw == "19000.00"
            assert await freshness(hypothetical.run_id) is Freshness.STALE  # case changed
            old = await s.comparisons.get_comparison_details(
                ctx, ComparisonDetailsRequest(run_id=regular.run_id)
            )
            assert old.data.result.alternatives == regular.alternatives  # history unchanged

    run(scenario())


def test_idempotency_and_payload_mismatch(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            _, ctx = await employee(s)
            op = uuid4()
            first = await s.comparisons.compare_scenarios(
                ctx, compare(["vehicle:CAR-10"], operation_id=op)
            )
            again = await s.comparisons.compare_scenarios(
                ctx, compare(["vehicle:CAR-10"], operation_id=op)
            )
            assert again.data == first.data
            with pytest.raises(PayloadMismatch):  # AC31
                await s.comparisons.compare_scenarios(
                    ctx, compare(["vehicle:CAR-11"], operation_id=op)
                )
            count = sql(
                business_db,
                "SELECT count(*) FROM business.comparison_runs WHERE case_id = %s",
                ctx.case_id,
            )
            assert count == [(1,)]

    run(scenario())


def test_result_set_and_all_eligible_selection(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            _, ctx = await employee(s, group=EntitlementGroup.BET, children=4)
            listed = await s.catalogue.list_eligible_options(
                ctx, ListOptionsRequest(categories=[OptionCategory.FAMILY])
            )
            by_list = await s.comparisons.compare_scenarios(
                ctx,
                compare(
                    selection={"mode": "result_set", "resultSetId": str(listed.data.result_set_id)}
                ),
            )
            assert [a.option_key for a in by_list.data.alternatives] == [
                "vehicle:CAR-19",
                "vehicle:CAR-20",
            ]
            assert not [i for i in by_list.issues if i.code is IssueCode.STALE_REVISION]
            family = by_list.data.alternatives[0].breakdown
            assert family is not None and D(family.family_gap_contribution.raw) == D("1150")
            assert D(family.ordinary_participation.raw) == D("500")  # T54: F plus R
            everything = await s.comparisons.compare_scenarios(
                ctx, compare(selection={"mode": "all_eligible"})
            )
            assert len(everything.data.alternatives) == 10  # 8 regular + 2 family
            runs = sql(
                business_db,
                "SELECT option_result_set_id FROM business.comparison_runs "
                "WHERE case_id = %s AND option_result_set_id IS NOT NULL",
                ctx.case_id,
            )
            assert len(runs) == 2
            with pytest.raises(NotFoundError):
                await s.comparisons.compare_scenarios(
                    ctx, compare(selection={"mode": "result_set", "resultSetId": str(uuid4())})
                )
            with pytest.raises(NotFoundError) as caught:  # Q4: no invented vehicle
                await s.comparisons.compare_scenarios(ctx, compare(["vehicle:CAR-99"]))
            assert caught.value.issues[0].code is IssueCode.VEHICLE_NOT_FOUND

    run(scenario())


def test_list_built_from_other_facts_is_reported(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            _, ctx = await employee(s, group=EntitlementGroup.BET)
            await s.cases.update_inputs(
                ctx, update("hypothetical", 0, [set_("employee.entitlementGroup", "ג")])
            )
            listed = await s.catalogue.list_eligible_options(
                ctx, ListOptionsRequest(categories=[OptionCategory.REGULAR])
            )
            assert len(listed.data.items) == 13  # the list applies the scenario group
            out = await s.comparisons.compare_scenarios(  # a regular run ignores overrides
                ctx,
                compare(
                    selection={"mode": "result_set", "resultSetId": str(listed.data.result_set_id)},
                    case_version=1,
                ),
            )
            reasons = {(i.option_key, i.reason) for i in out.issues}
            assert (None, "result_set_built_from_other_facts") in reasons
            assert ("vehicle:CAR-10", "eligibility_changed_since_list") in reasons
            statuses = {a.option_key: a.status for a in out.data.alternatives}
            assert statuses["vehicle:CAR-10"] is ScenarioStatus.INELIGIBLE  # never combined

    run(scenario())


# --- concurrency and lock order -----------------------------------------------------------


def test_concurrent_comparison_and_profile_writes_never_deadlock(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            _, ctx = await employee(s)
            for _ in range(3):
                context = await s.cases.get_context(ctx)
                version, profile_version = context.case_version, context.latest_profile_version
                outcomes = await asyncio.gather(
                    s.comparisons.compare_scenarios(
                        ctx,
                        compare(
                            ["vehicle:CAR-10", "vehicle:CAR-11"],
                            case_version=version,
                            profile_version=profile_version,
                        ),
                    ),
                    s.cases.update_inputs(
                        ctx,
                        update(
                            "actual_update",
                            version,
                            [set_("payroll.bonusGross", str(100 + version))],
                            profile_version=profile_version,
                        ),
                    ),
                    return_exceptions=True,
                )
                assert all(
                    not isinstance(o, BaseException) or isinstance(o, VersionConflict)
                    for o in outcomes
                ), outcomes
                assert not isinstance(outcomes[1], BaseException)  # the write never loses
                compared = outcomes[0]
                if not isinstance(compared, BaseException):  # it ran first: now stale (T42)
                    details = await s.comparisons.get_comparison_details(
                        ctx, ComparisonDetailsRequest(run_id=compared.data.run_id)
                    )
                    assert details.data.result.freshness is Freshness.STALE

    run(scenario())


# --- authorization and closed cases -------------------------------------------------------


def test_every_4b_operation_is_authorized_by_the_tool_context(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            who, ctx = await employee(s)
            run_id = (
                await s.comparisons.compare_scenarios(ctx, compare(["vehicle:CAR-10"]))
            ).data.run_id
            _, stranger = await employee(s)
            calls: list[Callable[[ToolContext], Awaitable[object]]] = [
                lambda c: s.readiness.check_calculation_readiness(
                    c, readiness("calculate_net", ["vehicle:CAR-10"])
                ),
                lambda c: s.comparisons.compare_scenarios(c, compare(["vehicle:CAR-10"])),
                lambda c: s.comparisons.get_comparison_details(
                    c, ComparisonDetailsRequest(run_id=run_id)
                ),
            ]
            probes = [
                ctx.model_copy(update={"case_id": stranger.case_id}),
                ctx.model_copy(update={"case_id": uuid4()}),
                ctx.model_copy(update={"conversation_id": uuid4()}),
            ]
            for call in calls:
                for probe in probes:
                    with pytest.raises(NotFoundError) as caught:
                        await call(probe)
                    assert caught.value.issues == () and caught.value.safe_details == {}
            with pytest.raises(NotFoundError):  # another case's run is invisible
                await s.comparisons.get_comparison_details(
                    stranger, ComparisonDetailsRequest(run_id=run_id)
                )
            rep = ctx.model_copy(
                update={
                    "actor": actor(await new_user(s.p, Role.REPRESENTATIVE), Role.REPRESENTATIVE)
                }
            )
            for call in calls:
                with pytest.raises(ForbiddenError):
                    await call(rep)
            sql(
                business_db,
                "UPDATE business.cases SET lifecycle_status = 'closed' WHERE id = %s",
                ctx.case_id,
            )
            with pytest.raises(InvalidStateTransition):
                await s.comparisons.compare_scenarios(ctx, compare(["vehicle:CAR-10"]))
            assert (
                await s.comparisons.get_comparison_details(
                    ctx, ComparisonDetailsRequest(run_id=run_id)
                )
            ).data.result.run_id == run_id  # history stays readable
            sql(business_db, "UPDATE auth.users SET active = false WHERE id = %s", who.subject_id)
            for call in calls:
                with pytest.raises(UnauthenticatedError):
                    await call(ctx)

    run(scenario())
