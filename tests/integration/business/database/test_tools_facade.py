"""Step 4C: `BusinessToolsFacade` on real PostgreSQL - the 13 canonical tools as Envelopes.

Covers contract compliance (every response validates as `Envelope[<result>]` in its JSON form,
trace ID from the tool context), error mapping (no raw exception, no foreign data), T61 (tax
lookup unavailable: explicit failure, no run, retry works), and the employee side of
representative questions (T31, T34, AC14, T84 business part).
"""

import logging
from typing import Any
from uuid import uuid4

import pytest

from app.business.application.cases.case_service import CaseService
from app.business.application.comparisons.comparison_service import ComparisonService
from app.business.application.comparisons.readiness_service import ReadinessService
from app.business.application.identity.authorization import AuthorizationPolicy
from app.business.application.occupations.occupation_service import OccupationService
from app.business.application.ports.reference_data import ReferenceDataUnavailableError
from app.business.application.profiles.profile_service import EmployeeProfileService
from app.business.application.representatives.question_service import (
    RepresentativeQuestionService,
)
from app.business.application.representatives.request_service import (
    RepresentativeRequestService,
)
from app.business.application.tools_facade import BusinessToolsFacade
from app.business.application.vehicles.catalogue_service import CatalogueService
from app.business.application.vehicles.valuation_service import ValuationService
from app.business.domain.rules import EntitlementGroup
from app.business.infrastructure.clock import SystemClock
from app.contracts import catalogue as cat
from app.contracts import comparisons as cmp
from app.contracts import readiness as rd
from app.contracts import representatives as rep
from app.contracts.business_port import CANONICAL_TOOL_NAMES, BusinessToolsPort
from app.contracts.case_inputs import (
    ConfirmProfileFieldsRequest,
    ConfirmProfileFieldsResult,
    UpdateInputsResult,
)
from app.contracts.cases import CaseContext
from app.contracts.catalogue import (
    EligibilityRequest,
    ListOptionsRequest,
    ResolveOccupationRequest,
    ResolveVehicleRequest,
    TaxValueRequest,
)
from app.contracts.common import Envelope, EnvelopeStatus, ErrorCode, IssueCode
from app.contracts.comparisons import ComparisonDetailsRequest, ScenarioStatus
from app.contracts.representatives import CreateQuestionRequest, RepresentativeRequestLookup
from app.contracts.tool_context import Role, ToolContext
from tests.integration.business.database.case_helpers import Persistence, new_user
from tests.integration.business.database.db_helpers import run, sql
from tests.integration.business.database.service_helpers import actor, services, set_, update
from tests.integration.business.database.test_comparison_services import (
    compare,
    employee,
    readiness,
)

pytestmark = pytest.mark.sources


class UnavailableTaxLookup:
    """Test double for T61: the tax-value lookup fails while switched off; all else delegates."""

    def __init__(self, real: Any) -> None:
        self._real = real
        self.down = True

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    async def get_value_of_use(self, *args: Any, **kwargs: Any) -> Any:
        if self.down:
            raise ReferenceDataUnavailableError("tax lookup unavailable")
        return await self._real.get_value_of_use(*args, **kwargs)


def facade(p: Persistence, reference: Any = None) -> BusinessToolsFacade:
    reference = reference or p.reference
    clock = SystemClock()
    policy = AuthorizationPolicy()
    profiles = EmployeeProfileService(p.uow, policy, clock)
    return BusinessToolsFacade(
        cases=CaseService(p.uow, policy, profiles, reference, clock),
        catalogue=CatalogueService(p.uow, policy, reference, clock),
        valuation=ValuationService(p.uow, policy, reference),
        occupations=OccupationService(p.uow, policy, reference),
        readiness=ReadinessService(p.uow, policy, reference, clock),
        comparisons=ComparisonService(p.uow, policy, reference, clock),
        questions=RepresentativeQuestionService(p.uow, policy, reference, clock),
        requests=RepresentativeRequestService(p.uow, policy),
    )


def round_trip(envelope: Envelope[Any], result_type: type) -> Envelope[Any]:
    """The wire form must validate against the contract `Envelope[<result>]`."""
    wire = envelope.model_dump(mode="json", by_alias=True)
    return Envelope[result_type].model_validate(wire)  # type: ignore[valid-type]


def question(
    field: str, option: str | None, *, version: int = 0, key: Any = None, text: str = "?"
) -> CreateQuestionRequest:
    body: dict[str, Any] = {
        "fieldCode": field,
        "questionText": text,
        "idempotencyKey": str(key or uuid4()),
        "expectedCaseVersion": version,
    }
    if option is not None:
        body["optionKey"] = option
    return CreateQuestionRequest.model_validate(body)


def test_the_facade_is_the_business_tools_port(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            tools = facade(s.p)
            assert isinstance(tools, BusinessToolsPort)
            public = {n for n in dir(tools) if not n.startswith("_")}
            assert public == set(CANONICAL_TOOL_NAMES)

    run(scenario())


def test_every_tool_returns_a_contract_envelope(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            tools = facade(s.p)
            _, ctx = await employee(s)
            ctx = ctx.model_copy(update={"trace_id": "turn-42"})
            calls: list[tuple[Any, type]] = [
                (await tools.get_conversation_context(ctx), CaseContext),
                (
                    await tools.list_eligible_options(ctx, ListOptionsRequest()),
                    cat.ListOptionsResult,
                ),
            ]
            calls += [
                (
                    await tools.resolve_vehicle(
                        ctx, ResolveVehicleRequest(purpose="alternative", catalogue_id="CAR-10")
                    ),
                    cat.ResolveVehicleResult,
                ),
                (
                    await tools.get_vehicle_tax_value(ctx, TaxValueRequest(catalogue_id="CAR-10")),
                    cat.TaxValueResult,
                ),
                (
                    await tools.check_eligibility(
                        ctx, EligibilityRequest(expected_case_version=0, option_key="waiver")
                    ),
                    cat.EligibilityResult,
                ),
                (
                    await tools.resolve_occupation(ctx, ResolveOccupationRequest(query="מנהל")),
                    cat.ResolveOccupationResult,
                ),
                (
                    await tools.check_calculation_readiness(
                        ctx, readiness("calculate_net", ["vehicle:CAR-10"])
                    ),
                    rd.ReadinessResult,
                ),
            ]
            compared = await tools.compare_scenarios(ctx, compare(["vehicle:CAR-10"]))
            calls.append((compared, cmp.ComparisonResult))
            assert compared.data is not None
            calls.append(
                (
                    await tools.get_comparison_details(
                        ctx, ComparisonDetailsRequest(run_id=compared.data.run_id)
                    ),
                    cmp.ComparisonDetails,
                )
            )
            asked = await tools.create_representative_question(
                ctx, question("alternative.participation", "vehicle:CAR-11")
            )
            calls.append((asked, rep.CreateQuestionResult))
            assert asked.data is not None
            calls.append(
                (
                    await tools.get_representative_request(
                        ctx, RepresentativeRequestLookup(request_id=asked.data.request_id)
                    ),
                    rep.RequestStatusView,
                )
            )
            calls.append(
                (
                    await tools.update_conversation_inputs(
                        ctx, update("hypothetical", 0, [set_("payroll.bonusGross", "100.00")])
                    ),
                    UpdateInputsResult,
                )
            )
            calls.append(
                (
                    await tools.confirm_profile_fields(
                        ctx,
                        ConfirmProfileFieldsRequest.model_validate(
                            {
                                "expectedCaseVersion": 1,
                                "expectedProfileVersion": 1,
                                "operationId": str(uuid4()),
                                "fields": ["payroll.baseGross"],
                            }
                        ),
                    ),
                    ConfirmProfileFieldsResult,
                )
            )
            assert len(calls) == len(CANONICAL_TOOL_NAMES)
            for envelope, result_type in calls:
                assert envelope.status is not EnvelopeStatus.ERROR, envelope
                assert envelope.trace_id == "turn-42"
                assert envelope.data is not None and isinstance(envelope.data, result_type)
                assert round_trip(envelope, result_type) == envelope
            assert compared.source_versions is not None and compared.case_version == 0

    run(scenario())


def test_errors_are_safe_envelopes(business_db: str, caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            tools = facade(s.p)
            _, ctx = await employee(s)
            _, stranger = await employee(s)
            foreign = ctx.model_copy(update={"case_id": stranger.case_id})
            out = await tools.get_conversation_context(foreign)
            assert out.status is EnvelopeStatus.ERROR and out.data is None
            assert out.error is not None and out.error.code is ErrorCode.NOT_FOUND
            assert out.error.safe_details == {} and out.issues == []  # AC26: no disclosure
            rep_id = await new_user(s.p, Role.REPRESENTATIVE)
            rep = ctx.model_copy(update={"actor": actor(rep_id, Role.REPRESENTATIVE)})
            forbidden = await tools.list_eligible_options(rep, ListOptionsRequest())
            assert forbidden.error is not None and forbidden.error.code is ErrorCode.FORBIDDEN
            stale = await tools.compare_scenarios(ctx, compare(["vehicle:CAR-10"], case_version=9))
            assert stale.error is not None and stale.error.code is ErrorCode.VERSION_CONFLICT
            missing = await tools.get_vehicle_tax_value(ctx, TaxValueRequest(catalogue_id="CAR-99"))
            assert missing.error is not None and missing.error.code is ErrorCode.NOT_FOUND
            assert [i.code for i in missing.issues] == [IssueCode.VEHICLE_NOT_FOUND]
            for envelope in (out, forbidden, stale, missing):
                round_trip(envelope, CaseContext)

            class Broken:
                async def get_context(self, _ctx: ToolContext) -> Any:
                    raise RuntimeError("boom: baseGross=18500")

            broken = facade(s.p)
            broken._cases = Broken()  # type: ignore[assignment]
            with caplog.at_level(logging.ERROR):
                failed = await broken.get_conversation_context(ctx)
            assert failed.error is not None and failed.error.code is ErrorCode.INFRA_UNAVAILABLE
            assert failed.error.safe_details == {}
            assert "18500" not in caplog.text and "RuntimeError" in caplog.text

    run(scenario())


def test_t61_tax_lookup_unavailable_fails_explicitly(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            lookup = UnavailableTaxLookup(s.p.reference)
            tools = facade(s.p, lookup)
            _, ctx = await employee(s)
            op = uuid4()
            out = await tools.compare_scenarios(ctx, compare(["vehicle:CAR-10"], operation_id=op))
            assert out.status is EnvelopeStatus.ERROR and out.data is None  # no numeric result
            assert out.error is not None and out.error.code is ErrorCode.INFRA_UNAVAILABLE
            assert out.error.safe_details == {"reason": "reference_data_unavailable"}
            value = await tools.get_vehicle_tax_value(ctx, TaxValueRequest(catalogue_id="CAR-10"))
            assert value.error is not None and value.error.code is ErrorCode.INFRA_UNAVAILABLE
            runs = sql(
                business_db,
                "SELECT count(*) FROM business.comparison_runs WHERE case_id = %s",
                ctx.case_id,
            )
            assert runs == [(0,)]  # nothing persisted, not even the operation
            lookup.down = False
            retried = await tools.compare_scenarios(
                ctx, compare(["vehicle:CAR-10"], operation_id=op)
            )
            assert retried.status is EnvelopeStatus.OK and retried.data is not None
            assert retried.data.alternatives[0].status is ScenarioStatus.CALCULATED

    run(scenario())


def test_representative_questions_block_only_the_dependent_option(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            tools = facade(s.p)
            _, ctx = await employee(s, group=EntitlementGroup.GIMEL)
            key = uuid4()
            first = await tools.create_representative_question(
                ctx, question("alternative.participation", "vehicle:CAR-10", key=key)
            )
            assert first.data is not None and first.data.created
            replay = await tools.create_representative_question(
                ctx, question("alternative.participation", "vehicle:CAR-10", key=key)
            )
            assert replay.data is not None and not replay.data.created
            assert replay.data.request_id == first.data.request_id
            mismatch = await tools.create_representative_question(
                ctx, question("alternative.participation", "vehicle:CAR-11", key=key)
            )
            assert mismatch.error is not None
            assert mismatch.error.code is ErrorCode.OPERATION_PAYLOAD_MISMATCH
            duplicate = await tools.create_representative_question(  # T34: new key, same fact
                ctx, question("alternative.participation", "vehicle:CAR-10")
            )
            assert duplicate.data is not None and not duplicate.data.created
            assert duplicate.data.request_id == first.data.request_id
            for bad in (
                question("alternative.participation", None),
                question("employee.entitlementGroup", "vehicle:CAR-10"),
                question("occupation.waiverPolicy", "vehicle:CAR-10"),
            ):
                rejected = await tools.create_representative_question(ctx, bad)
                assert rejected.error is not None
                assert rejected.error.code is ErrorCode.INVALID_BUSINESS_INPUT
            unknown = await tools.create_representative_question(
                ctx, question("alternative.participation", "vehicle:CAR-99")
            )
            assert unknown.error is not None
            assert unknown.error.code is ErrorCode.INVALID_BUSINESS_INPUT
            stale = await tools.create_representative_question(
                ctx, question("employee.entitlementGroup", None, version=3)
            )
            assert stale.error is not None and stale.error.code is ErrorCode.VERSION_CONFLICT
            snapshot = sql(
                business_db,
                "SELECT subject_snapshot FROM business.representative_requests WHERE id = %s",
                first.data.request_id,
            )
            assert snapshot == [({"catalogueId": "CAR-10"},)]  # minimal context, no salary
            status = await tools.get_representative_request(
                ctx, RepresentativeRequestLookup(request_id=first.data.request_id)
            )
            assert status.data is not None and status.data.status.value == "open"
            out = await tools.compare_scenarios(ctx, compare(["vehicle:CAR-10", "vehicle:CAR-11"]))
            assert out.status is EnvelopeStatus.PARTIAL and out.data is not None
            first_alt, second_alt = out.data.alternatives
            assert first_alt.status is ScenarioStatus.NEEDS_INFORMATION  # T31: waits
            assert second_alt.status is ScenarioStatus.CALCULATED  # AC14: others proceed
            assert IssueCode.PENDING_REPRESENTATIVE in {i.code for i in out.issues}
            context = await tools.get_conversation_context(ctx)  # T84: case stays usable
            assert context.data is not None
            assert [r.request_id for r in context.data.open_requests] == [first.data.request_id]
            sql(
                business_db,
                "UPDATE business.cases SET lifecycle_status = 'closed' WHERE id = %s",
                ctx.case_id,
            )
            closed = await tools.create_representative_question(
                ctx, question("employee.entitlementGroup", None)
            )
            assert closed.error is not None
            assert closed.error.code is ErrorCode.INVALID_STATE_TRANSITION

    run(scenario())


def test_waiver_and_baseline_questions(business_db: str) -> None:
    async def scenario() -> None:
        async with services(business_db) as s:
            tools = facade(s.p)
            _, ctx = await employee(s)
            waiver = await tools.create_representative_question(
                ctx, question("occupation.waiverPolicy", "waiver")
            )
            vehicle = await tools.create_representative_question(
                ctx, question("currentArrangement.propulsion", None)
            )
            assert waiver.data is not None and vehicle.data is not None
            rows = sql(
                business_db,
                "SELECT field_code, expected_value_type, subject_snapshot "
                "FROM business.representative_requests WHERE case_id = %s ORDER BY field_code",
                ctx.case_id,
            )
            assert rows == [
                (
                    "currentArrangement.propulsion",
                    "propulsion",
                    {"currentVehicle": {"catalogueId": "CAR-09"}},
                ),
                ("occupation.waiverPolicy", "boolean", {"occupationId": "OCC-PRODUCT-MGR"}),
            ]
            ready = await tools.check_calculation_readiness(
                ctx, readiness("compare_to_current", ["vehicle:CAR-10"])
            )
            assert ready.data is not None and ready.data.baseline_ready is False
            assert IssueCode.PENDING_REPRESENTATIVE in {i.code for i in ready.issues}

    run(scenario())
