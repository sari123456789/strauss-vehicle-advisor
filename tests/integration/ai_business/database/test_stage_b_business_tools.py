"""Stage B (gateway level): the AI's `BusinessToolGateway` over the REAL `BusinessToolsFacade`.

Covers scenarios 1-3 and 5-8 without LangGraph: canonical release + demo data, catalogue and
eligibility, readiness and missing information, exact Decimal results for the six reference
employees, actual vs hypothetical isolation, an unconfirmed baseline giving delta=null, and
immutable history, replay idempotency, ownership and authorization.
"""

from decimal import Decimal, localcontext
from uuid import uuid4

import pytest

from app.ai.tools.gateway import BusinessToolGateway
from app.contracts.case_inputs import SetChange, UpdateInputsRequest
from app.contracts.catalogue import (
    EligibilityReason,
    EligibilityRequest,
    EligibilityStatus,
    ListOptionsRequest,
)
from app.contracts.common import (
    EnvelopeStatus,
    ErrorCode,
    FieldPath,
    Freshness,
    RunKind,
    UpdateMode,
)
from app.contracts.comparisons import ComparisonDetailsRequest, ScenarioStatus
from app.contracts.readiness import ReadinessGoal, ReadinessRequest, Selection, SelectionMode
from tests.integration.ai_business.database.conftest import canonical_sources
from tests.integration.ai_business.database.stage_b_helpers import (
    business,
    compare,
    confirm_current_arrangement,
    demo_subject,
    open_case,
    runs_of,
)
from tests.integration.business.database.case_helpers import (
    latest_release,
    new_user,
    persistence,
    profile,
)
from tests.integration.business.database.db_helpers import run, sql
from tests.unit.business.bl.test_reference_employees import (
    ALTERNATIVE,
    DELTA_EXCEPTIONS,
    SPEC_RAW,
)

pytestmark = [pytest.mark.db, pytest.mark.sources]
D = Decimal
THREE = D("0.001")
GROUP_LAST_REGULAR = {"א": 4, "ב": 8, "ג": 13, "ד": 18}


def test_canonical_release_and_demo_data_are_loaded(stage_b_db: str) -> None:
    async def scenario() -> None:
        expected = {e["kind"]: e["sha256"] for e in canonical_sources()}
        async with persistence(stage_b_db) as p:
            release = await latest_release(p)
        assert release.identity.workbook_sha256 == expected["challenge_workbook_xlsx"]
        assert release.identity.tax_dataset_sha256 == expected["vehicle_tax_dataset_csv"]
        assert release.identity.rules_sha256 == expected["business_rules_json"]
        assert release.identity.challenge_pdf_sha256 == expected["challenge_instructions_pdf"]
        logins = sql(stage_b_db, "SELECT login FROM auth.users WHERE login LIKE 'e00%%'")
        assert sorted(str(r[0]) for r in logins) == [f"e00{i}" for i in range(1, 7)]

    run(scenario())


def test_catalogue_and_eligibility_through_the_gateway(stage_b_db: str) -> None:
    async def scenario() -> None:
        async with business(stage_b_db) as services:
            ctx = await open_case(services, demo_subject("E-001"))
            gateway = BusinessToolGateway(services.tools, ctx)
            context = (await gateway.get_conversation_context()).data
            assert context is not None and context.profile.entitlement_group.value is not None
            group = context.profile.entitlement_group.value
            listed = await gateway.list_eligible_options(ListOptionsRequest())
            assert listed.status in {EnvelopeStatus.OK, EnvelopeStatus.PARTIAL}
            assert listed.data is not None
            regular = [
                i.catalogue_id
                for i in listed.data.items
                if i.category.value == "regular" and i.eligibility is EligibilityStatus.ELIGIBLE
            ]
            assert regular == [f"CAR-{n:02d}" for n in range(1, GROUP_LAST_REGULAR[group] + 1)]
            higher = await gateway.check_eligibility(
                EligibilityRequest(expected_case_version=0, option_key="vehicle:CAR-14")
            )
            assert higher.data is not None
            if group != "ד":
                assert higher.data.status is EligibilityStatus.INELIGIBLE
                assert higher.data.reasons == [EligibilityReason.GROUP_HIGHER]
            assert gateway.calls == [
                "get_conversation_context",
                "list_eligible_options",
                "check_eligibility",
            ]

    run(scenario())


def test_readiness_reports_missing_salary_and_nothing_is_calculated(stage_b_db: str) -> None:
    async def scenario() -> None:
        async with persistence(stage_b_db) as p:
            subject = await new_user(p)
            async with p.uow() as uow:
                await uow.profiles.add(profile(subject))  # bonus, study fund, credits missing
                await uow.commit()
        async with business(stage_b_db) as services:
            ctx = await open_case(services, subject)
            gateway = BusinessToolGateway(services.tools, ctx)
            ready = await gateway.check_calculation_readiness(
                ReadinessRequest(
                    goal=ReadinessGoal.CALCULATE_NET,
                    selection=Selection(
                        mode=SelectionMode.EXPLICIT, option_keys=["vehicle:CAR-10"]
                    ),
                    expected_case_version=0,
                )
            )
            assert ready.data is not None and not ready.data.scope_complete
            assert ready.data.ready_scenario_ids == []
            assert {
                FieldPath.BONUS_GROSS,
                FieldPath.STUDY_FUND_EMPLOYEE_RATE,
                FieldPath.CREDIT_POINTS,
            } <= set(ready.data.missing_employee_fields)
            out = await gateway.compare_scenarios(
                compare(["vehicle:CAR-10"], case_version=0, profile_version=1)
            )
            assert out.data is not None
            assert out.data.alternatives[0].status is ScenarioStatus.NEEDS_INFORMATION
            assert out.data.alternatives[0].net is None  # missing is never zero

    run(scenario())


@pytest.mark.parametrize("ref", sorted(SPEC_RAW))
def test_reference_employees_exact_decimals_through_the_gateway(stage_b_db: str, ref: str) -> None:
    async def scenario() -> None:
        async with business(stage_b_db) as services:
            ctx = await open_case(services, demo_subject(ref))
            await confirm_current_arrangement(services, ctx)
            gateway = BusinessToolGateway(services.tools, ctx)
            context = (await gateway.get_conversation_context()).data
            assert context is not None
            option = "waiver" if ALTERNATIVE[ref] == "waiver" else f"vehicle:{ALTERNATIVE[ref]}"
            out = await gateway.compare_scenarios(
                compare(
                    [option],
                    case_version=context.case_version,
                    profile_version=context.latest_profile_version,
                    include_current=True,
                )
            )
            assert out.status is EnvelopeStatus.OK and out.data is not None
            current, alternative = out.data.current, out.data.alternatives[0]
            assert current is not None and current.net is not None
            assert alternative.net is not None and alternative.delta is not None
            current_raw, alternative_raw, delta_raw = SPEC_RAW[ref]
            assert D(current.net.raw).quantize(THREE) == D(current_raw)
            assert D(alternative.net.raw).quantize(THREE) == D(alternative_raw)
            assert D(alternative.delta.raw).quantize(THREE) == D(delta_raw)
            with localcontext() as exact:  # the engine's precision (architecture §12)
                exact.prec = 40
                precise = D(alternative.net.raw) - D(current.net.raw)
            assert D(alternative.delta.raw) == precise  # delta of precise nets, not displays
            if ref in DELTA_EXCEPTIONS:  # AC05: -1939 / +722 from the precise delta
                assert int(alternative.delta.display) == DELTA_EXCEPTIONS[ref][1]

    run(scenario())


def test_hypothetical_values_stay_in_their_case(stage_b_db: str) -> None:
    async def scenario() -> None:
        async with business(stage_b_db) as services:
            subject = demo_subject("E-004")
            ctx = await open_case(services, subject)
            gateway = BusinessToolGateway(services.tools, ctx)
            before = (await gateway.get_conversation_context()).data
            assert before is not None
            updated = await gateway.update_conversation_inputs(
                UpdateInputsRequest(
                    mode=UpdateMode.HYPOTHETICAL,
                    expected_case_version=0,
                    operation_id=uuid4(),
                    changes=[SetChange(op="set", field=FieldPath.BASE_GROSS, value="25000.00")],
                )
            )
            assert updated.data is not None and updated.data.new_profile_version is None
            out = await gateway.compare_scenarios(
                compare(
                    ["vehicle:CAR-11"],
                    case_version=1,
                    profile_version=None,
                    kind=RunKind.HYPOTHETICAL,
                )
            )
            assert out.data is not None and out.data.kind is RunKind.HYPOTHETICAL
            breakdown = out.data.alternatives[0].breakdown
            assert breakdown is not None
            earned = D(breakdown.earned_gross.raw)
            regular = await gateway.compare_scenarios(
                compare(
                    ["vehicle:CAR-11"],
                    case_version=1,
                    profile_version=before.latest_profile_version,
                )
            )
            assert regular.data is not None and regular.data.alternatives[0].breakdown is not None
            assert earned - D(regular.data.alternatives[0].breakdown.earned_gross.raw) == (
                D("25000.00") - D(str(before.profile.base_gross.value))
            )  # only the scenario sees the new base salary
            after = (await gateway.get_conversation_context()).data
            assert after is not None and after.profile == before.profile  # profile untouched
            fresh = await open_case(services, subject)
            new_context = (await services.tools.get_conversation_context(fresh)).data
            assert new_context is not None and new_context.hypothetical_overrides == []

    run(scenario())


def test_unconfirmed_current_arrangement_yields_no_delta(stage_b_db: str) -> None:
    async def scenario() -> None:
        async with business(stage_b_db) as services:
            ctx = await open_case(services, demo_subject("E-001"))
            gateway = BusinessToolGateway(services.tools, ctx)
            ready = await gateway.check_calculation_readiness(
                ReadinessRequest(
                    goal=ReadinessGoal.COMPARE_TO_CURRENT,
                    selection=Selection(
                        mode=SelectionMode.EXPLICIT, option_keys=["vehicle:CAR-10"]
                    ),
                    include_current=True,
                    expected_case_version=0,
                )
            )
            assert ready.data is not None and ready.data.baseline_ready is False
            assert ready.data.ready_scenario_ids == ["vehicle:CAR-10"]
            reasons = {(i.code.value, i.reason) for i in ready.issues}
            assert ("AMBIGUOUS_INPUT", "confirmation_required") in reasons
            context = (await gateway.get_conversation_context()).data
            assert context is not None  # versions are read, never assumed (other tests confirm)
            out = await gateway.compare_scenarios(
                compare(
                    ["vehicle:CAR-10"],
                    case_version=context.case_version,
                    profile_version=context.latest_profile_version,
                    include_current=True,
                )
            )
            assert out.status is EnvelopeStatus.PARTIAL and out.data is not None
            assert out.data.current is not None
            assert out.data.current.status is ScenarioStatus.NEEDS_INFORMATION
            assert out.data.alternatives[0].status is ScenarioStatus.CALCULATED
            assert out.data.alternatives[0].delta is None

    run(scenario())


def test_history_replay_ownership_and_authorization(stage_b_db: str) -> None:
    async def scenario() -> None:
        async with business(stage_b_db) as services:
            ctx = await open_case(services, demo_subject("E-003"))
            gateway = BusinessToolGateway(services.tools, ctx)
            operation = uuid4()
            context = (await gateway.get_conversation_context()).data
            assert context is not None
            versions = {
                "case_version": context.case_version,
                "profile_version": context.latest_profile_version,
            }
            request = compare(["vehicle:CAR-06"], operation_id=operation, **versions)
            first = await gateway.compare_scenarios(request)
            again = await gateway.compare_scenarios(request)  # the same operation, replayed
            assert first.data is not None and again.data == first.data
            assert runs_of(stage_b_db, ctx) == 1
            mismatch = await gateway.compare_scenarios(
                compare(["vehicle:CAR-05"], operation_id=operation, **versions)
            )
            assert mismatch.error is not None
            assert mismatch.error.code is ErrorCode.OPERATION_PAYLOAD_MISMATCH
            details = await gateway.get_comparison_details(
                ComparisonDetailsRequest(run_id=first.data.run_id)
            )
            assert details.data is not None and details.data.result == first.data
            assert details.data.input_snapshot["schemaVersion"] == 1
            other = await open_case(services, demo_subject("E-005"))
            foreign = ctx.model_copy(update={"case_id": other.case_id})
            denied = await BusinessToolGateway(services.tools, foreign).get_comparison_details(
                ComparisonDetailsRequest(run_id=first.data.run_id)
            )
            assert denied.error is not None and denied.error.code is ErrorCode.NOT_FOUND
            assert denied.error.safe_details == {} and denied.data is None
            invented = ctx.model_copy(update={"case_id": uuid4()})
            missing = await BusinessToolGateway(services.tools, invented).compare_scenarios(request)
            assert missing.error is not None and missing.error.code is ErrorCode.NOT_FOUND
            assert runs_of(stage_b_db, ctx) == 1
            # A later actual correction in another conversation leaves the saved run unchanged
            # but marks it stale.
            second = await open_case(services, demo_subject("E-003"))
            corrected = await BusinessToolGateway(
                services.tools, second
            ).update_conversation_inputs(
                UpdateInputsRequest(
                    mode=UpdateMode.ACTUAL_UPDATE,
                    expected_case_version=0,
                    expected_profile_version=context.latest_profile_version,
                    operation_id=uuid4(),
                    changes=[SetChange(op="set", field=FieldPath.BONUS_GROSS, value="321.00")],
                )
            )
            assert corrected.status is EnvelopeStatus.OK
            later = await gateway.get_comparison_details(
                ComparisonDetailsRequest(run_id=first.data.run_id)
            )
            assert later.data is not None
            assert later.data.result.freshness is Freshness.STALE
            assert later.data.result.alternatives == first.data.alternatives

    run(scenario())
