"""B3a on real PostgreSQL and the supplied sources: representative answers, case-local facts,
the integration outbox, audit, authorization/privacy and concurrency.

Scenarios: AC30 (answer outside its period), AC33 (cannot_determine), T36/T37 (effective
dates), decisions Q1-Q4 of the B3a plan, D1 (outbox, at-least-once feed), D2/R1 (audit) and
the global lock order (claim -> profile -> case -> requests -> alternatives).
"""

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.business.application.errors import (
    BusinessError,
    ForbiddenError,
    InvalidInput,
    InvalidStateTransition,
    NotFoundError,
    PayloadMismatch,
    UnauthenticatedError,
    VersionConflict,
)
from app.business.application.identity.authorization import AuthorizationPolicy
from app.business.application.ports.auth import UserAccount
from app.business.application.ports.unit_of_work import TransientConflictError
from app.business.application.representatives.answer_service import (
    RepresentativeAnswerService,
)
from app.business.application.representatives.question_service import (
    RepresentativeQuestionService,
)
from app.business.domain.rules import EntitlementGroup
from app.business.infrastructure.clock import SystemClock
from app.business.infrastructure.database.engine import create_session_factory
from app.business.infrastructure.database.event_feed import SqlBusinessEventFeed
from app.contracts.common import EnvelopeStatus, FieldPath, Freshness, IssueCode
from app.contracts.comparisons import ComparisonDetailsRequest, ScenarioStatus
from app.contracts.representatives import (
    AnswerApplication,
    AnswerOutcome,
    AnswerSubmit,
    CreateQuestionRequest,
    RepresentativeAnsweredEvent,
    RepresentativeRequestLookup,
    RequestStatus,
)
from app.contracts.tool_context import ActorRef, Role, ToolContext
from tests.integration.business.database.case_helpers import new_user
from tests.integration.business.database.db_helpers import run, sql
from tests.integration.business.database.service_helpers import (
    Services,
    actor,
    new_case,
    services,
    set_,
    update,
)
from tests.integration.business.database.test_comparison_services import (
    compare,
    employee,
    readiness,
)

pytestmark = pytest.mark.sources

PARTICIPATION = "alternative.participation"
GROUP = "employee.entitlementGroup"
PROPULSION = "currentArrangement.propulsion"
WAIVER_POLICY = "occupation.waiverPolicy"
TAX_KEY = "currentArrangement.vehicle"


@dataclass(frozen=True)
class World:
    s: Services
    answers: RepresentativeAnswerService
    questions: RepresentativeQuestionService
    representative: ActorRef


async def world(s: Services) -> World:
    policy = AuthorizationPolicy()
    clock = SystemClock()
    rep = actor(await new_user(s.p, Role.REPRESENTATIVE, "נציג/ת"), Role.REPRESENTATIVE)
    return World(
        s,
        RepresentativeAnswerService(s.p.uow, policy, s.p.reference, clock),
        RepresentativeQuestionService(s.p.uow, policy, s.p.reference, clock),
        rep,
    )


async def ask(
    w: World, ctx: ToolContext, field: str, option: str | None = None, *, version: int = 0
) -> UUID:
    result = await w.questions.create_representative_question(
        ctx,
        CreateQuestionRequest.model_validate(
            {
                "fieldCode": field,
                "optionKey": option,
                "questionText": "שאלה לנציג/ת התחבורה",
                "idempotencyKey": str(uuid4()),
                "expectedCaseVersion": version,
            }
        ),
    )
    return result.data.request_id


def answer(
    value: str | None = None,
    *,
    version: int = 0,
    unit: str | None = None,
    start: str | None = None,
    end: str | None = None,
    operation_id: UUID | None = None,
    note: str | None = None,
) -> AnswerSubmit:
    body: dict[str, Any] = {
        "operationId": str(operation_id or uuid4()),
        "expectedRequestVersion": version,
        "outcome": "answered" if value is not None else "cannot_determine",
        "value": value,
        "unit": unit,
        "effectiveFrom": start,
        "effectiveTo": end,
        "note": note,
    }
    return AnswerSubmit.model_validate({k: v for k, v in body.items() if v is not None})


def money(raw: str) -> AnswerSubmit:
    return answer(raw, unit="ILS")


def count(url: str, table: str, request_id: UUID) -> int:
    column = "payload->>'requestId'" if table == "integration_outbox" else "request_id::text"
    rows = sql(url, f"SELECT count(*) FROM business.{table} WHERE {column} = %s", str(request_id))
    return int(rows[0][0])


async def case_version(s: Services, ctx: ToolContext) -> int:
    return (await s.cases.get_context(ctx)).case_version


def snapshot(details: Any) -> Any:
    """The stored input snapshot as plain JSON (typed loosely for assertions)."""
    return details.data.input_snapshot


def scenario(result: Any, key: str) -> Any:
    return next(a for a in result.data.alternatives if a.option_key == key)


# --- applied participation, recalculation and freshness --------------------------------


def test_participation_answer_applies_and_new_calculation_uses_it(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, ctx = await employee(s)
            before = await s.comparisons.compare_scenarios(ctx, compare(["vehicle:CAR-10"]))
            assert scenario(before, "vehicle:CAR-10").status is ScenarioStatus.CALCULATED
            request_id = await ask(w, ctx, PARTICIPATION, "vehicle:CAR-10")
            pending = await s.comparisons.compare_scenarios(ctx, compare(["vehicle:CAR-10"]))
            blocked = scenario(pending, "vehicle:CAR-10")
            assert blocked.status is ScenarioStatus.NEEDS_INFORMATION
            assert blocked.missing_information[0].code is IssueCode.PENDING_REPRESENTATIVE

            view = await w.answers.submit_answer(w.representative, request_id, money("350.00"))
            assert view.outcome is AnswerOutcome.ANSWERED and view.value == "350.00"
            assert view.unit == "ILS" and view.application is AnswerApplication.APPLIED

            status = await s.requests.get_representative_request(
                ctx, RepresentativeRequestLookup(request_id=request_id)
            )
            assert status.data.status is RequestStatus.ANSWERED
            assert status.data.answer == view  # the persisted AnswerView (employee side)
            assert await case_version(s, ctx) == 1

            # The earlier runs become stale but keep their stored values.
            old = await s.comparisons.get_comparison_details(
                ctx, ComparisonDetailsRequest(run_id=before.data.run_id)
            )
            assert old.data.result.freshness is Freshness.STALE
            assert old.data.result.alternatives == before.data.alternatives

            after = await s.comparisons.compare_scenarios(
                ctx, compare(["vehicle:CAR-10"], case_version=1)
            )
            calculated = scenario(after, "vehicle:CAR-10")
            assert calculated.status is ScenarioStatus.CALCULATED
            assert calculated.vehicle is not None
            assert calculated.vehicle.participation_source == "representative"
            assert calculated.breakdown is not None
            assert Decimal(calculated.breakdown.ordinary_participation.raw) == Decimal("350")
            codes = {a.code for a in after.data.assumptions}
            assert "participation.catalogue_default" not in codes
            details = await s.comparisons.get_comparison_details(
                ctx, ComparisonDetailsRequest(run_id=after.data.run_id)
            )
            (stored,) = snapshot(details)["scenarios"]
            assert stored["vehicle"]["participation"] == "350.00"
            assert stored["vehicle"]["participationSource"] == "representative"
            async with s.p.uow() as uow:
                alternative = await uow.alternatives.find_active(ctx.case_id, "vehicle:CAR-10")
            assert alternative is not None and alternative.version == 1
            entry = alternative.inputs["participation"]
            assert isinstance(entry, Mapping)
            assert entry["source"] == "representative"
            assert entry["requestId"] == str(request_id)

    run(go())


@pytest.mark.parametrize(
    ("start", "end", "applied"),
    [
        (None, None, True),  # T36: no date is mandatory
        ("2026-10-01", None, True),  # starts on the reference date
        (None, "2026-10-01", True),  # ends on the reference date
        ("2026-09-01", "2026-10-31", True),
        (None, "2026-09-30", False),  # expired before the calculation month
        ("2026-10-02", None, False),  # T37: effective later - not used now
    ],
)
def test_effective_date_boundaries(
    business_db: str, start: str | None, end: str | None, applied: bool
) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, ctx = await employee(s)
            request_id = await ask(w, ctx, PARTICIPATION, "vehicle:CAR-10")
            view = await w.answers.submit_answer(
                w.representative, request_id, answer("350", unit="ILS", start=start, end=end)
            )
            expected = AnswerApplication.APPLIED if applied else AnswerApplication.NOT_APPLICABLE
            assert view.application is expected
            assert view.effective_from == (None if start is None else date.fromisoformat(start))
            out = await s.comparisons.compare_scenarios(
                ctx, compare(["vehicle:CAR-10"], case_version=1)
            )
            vehicle = scenario(out, "vehicle:CAR-10")
            assert vehicle.status is ScenarioStatus.CALCULATED  # no longer pending either way
            source = "representative" if applied else "catalogue_default"
            assert vehicle.vehicle.participation_source == source
            reason = sql(
                business_db,
                "SELECT application_reason FROM business.representative_answers "
                "WHERE request_id = %s",
                request_id,
            )
            assert reason == [(None if applied else "outside_effective_period",)]

    run(go())


def test_answer_for_a_changed_alternative_is_saved_but_not_applied(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, ctx = await employee(s)
            request_id = await ask(w, ctx, PARTICIPATION, "vehicle:CAR-10")
            async with s.p.uow() as uow:  # the option changed after the question was asked
                alternative = await uow.alternatives.find_active(ctx.case_id, "vehicle:CAR-10")
                assert alternative is not None
                await uow.alternatives.update(alternative)
                await uow.commit()
            view = await w.answers.submit_answer(w.representative, request_id, money("350"))
            assert view.application is AnswerApplication.NOT_APPLICABLE
            async with s.p.uow() as uow:
                current = await uow.alternatives.find_active(ctx.case_id, "vehicle:CAR-10")
            assert current is not None and "participation" not in current.inputs

    run(go())


# --- case-local facts (Q1), precedence and conflicts -----------------------------------


def test_group_fact_is_case_local_and_survives_synchronization(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            who, ctx = await employee(s, occupation=None)  # group ג; no occupation conflict
            other_ctx, _ = await new_case(s, who)
            key = "vehicle:CAR-14"  # a group ד vehicle: ineligible for ג
            earlier = await s.comparisons.compare_scenarios(other_ctx, compare([key]))
            request_id = await ask(w, ctx, GROUP)
            view = await w.answers.submit_answer(
                w.representative, request_id, answer(EntitlementGroup.DALET.value)
            )
            assert view.application is AnswerApplication.APPLIED
            untouched = await s.comparisons.get_comparison_details(
                other_ctx, ComparisonDetailsRequest(run_id=earlier.data.run_id)
            )
            assert untouched.data.result.freshness is Freshness.CURRENT  # other case unaffected
            mine = await s.comparisons.compare_scenarios(ctx, compare([key], case_version=1))
            assert scenario(mine, key).status is ScenarioStatus.CALCULATED
            others = await s.comparisons.compare_scenarios(other_ctx, compare([key]))
            assert scenario(others, key).status is ScenarioStatus.INELIGIBLE
            shared = await s.profiles.get_profile(who)
            assert shared.profile_version == 1  # the shared profile is never changed
            assert shared.entitlement_group.value == EntitlementGroup.GIMEL.value

            # An unrelated actual update synchronizes the case copy; the fact stays applied.
            changed = await s.cases.update_inputs(
                ctx,
                update(
                    "actual_update",
                    await case_version(s, ctx),
                    [set_("payroll.bonusGross", "100.00")],
                    profile_version=1,
                ),
            )
            assert changed.conflicts == []
            synced = await s.comparisons.compare_scenarios(
                ctx, compare([key], case_version=changed.new_case_version, profile_version=2)
            )
            assert scenario(synced, key).status is ScenarioStatus.CALCULATED
            details = await s.comparisons.get_comparison_details(
                ctx, ComparisonDetailsRequest(run_id=synced.data.run_id)
            )
            fact = snapshot(details)["facts"][GROUP]
            assert fact["value"] == "ד" and fact["source"] == "representative"

    run(go())


def test_propulsion_fact_applies_to_the_case(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, ctx = await employee(s)
            request_id = await ask(w, ctx, PROPULSION)
            view = await w.answers.submit_answer(w.representative, request_id, answer("hybrid"))
            assert view.application is AnswerApplication.APPLIED
            out = await s.comparisons.compare_scenarios(
                ctx, compare(["vehicle:CAR-10"], case_version=1)
            )
            details = await s.comparisons.get_comparison_details(
                ctx, ComparisonDetailsRequest(run_id=out.data.run_id)
            )
            fact = snapshot(details)["facts"][PROPULSION]
            assert fact == {**fact, "value": "hybrid", "source": "representative"}

    run(go())


def test_hypothetical_override_beats_the_representative_fact_without_conflict(
    business_db: str,
) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, ctx = await employee(s, occupation=None)
            request_id = await ask(w, ctx, GROUP)
            await w.answers.submit_answer(w.representative, request_id, answer("ד"))
            changed = await s.cases.update_inputs(
                ctx, update("hypothetical", 1, [set_(GROUP, "ב")])
            )
            assert changed.conflicts == []
            key = "vehicle:CAR-14"
            what_if = await s.comparisons.compare_scenarios(
                ctx, compare([key], kind="hypothetical", case_version=2)
            )
            assert scenario(what_if, key).status is ScenarioStatus.INELIGIBLE
            assert not [i for i in what_if.issues if i.code is IssueCode.SOURCE_CONFLICT]
            regular = await s.comparisons.compare_scenarios(ctx, compare([key], case_version=2))
            assert scenario(regular, key).status is ScenarioStatus.CALCULATED

    run(go())


def test_genuine_source_conflict_is_reported_and_the_calculation_proceeds(
    business_db: str,
) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, ctx = await employee(s, occupation=None)  # employee reported ג
            request_id = await ask(w, ctx, GROUP)
            await w.answers.submit_answer(w.representative, request_id, answer("ד"))
            changed = await s.cases.update_inputs(
                ctx, update("actual_update", 1, [set_(GROUP, "ב")], profile_version=1)
            )
            (conflict,) = [c for c in changed.conflicts if c.code is IssueCode.SOURCE_CONFLICT]
            assert conflict.field is FieldPath.ENTITLEMENT_GROUP
            assert conflict.reason == "representative_fact_differs"
            assert conflict.request_id == request_id
            assert changed.new_profile_version == 2  # the actual update itself is saved
            key = "vehicle:CAR-14"
            out = await s.comparisons.compare_scenarios(
                ctx, compare([key], case_version=changed.new_case_version, profile_version=2)
            )
            assert scenario(out, key).status is ScenarioStatus.CALCULATED  # ד still applies
            reported = [i for i in out.issues if i.code is IssueCode.SOURCE_CONFLICT]
            assert [i.reason for i in reported] == ["representative_fact_differs"]
            ready = await s.readiness.check_calculation_readiness(
                ctx, readiness("calculate_net", [key], version=changed.new_case_version)
            )
            assert ready.data.ready_scenario_ids == [key]  # readiness is unchanged
            assert any(i.code is IssueCode.SOURCE_CONFLICT for i in ready.issues)

    run(go())


# --- cannot_determine and unsupported field codes (Q2/Q3) -------------------------------


def test_cannot_determine_blocks_only_its_option(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, ctx = await employee(s)
            request_id = await ask(w, ctx, PARTICIPATION, "vehicle:CAR-10")
            view = await w.answers.submit_answer(w.representative, request_id, answer())
            assert view.outcome is AnswerOutcome.CANNOT_DETERMINE
            assert view.value is None and view.application is None
            out = await s.comparisons.compare_scenarios(
                ctx, compare(["vehicle:CAR-10", "vehicle:CAR-11"], case_version=1)
            )
            blocked = scenario(out, "vehicle:CAR-10")
            assert blocked.status is ScenarioStatus.NEEDS_INFORMATION
            (issue,) = blocked.missing_information
            assert issue.reason == "representative_cannot_determine"
            assert issue.request_id == request_id
            assert scenario(out, "vehicle:CAR-11").status is ScenarioStatus.CALCULATED
            assert out.status is EnvelopeStatus.PARTIAL

    run(go())


@pytest.mark.parametrize(
    ("field", "option", "value"),
    [(WAIVER_POLICY, None, "true"), (WAIVER_POLICY, "waiver", "false"), (TAX_KEY, None, "x")],
)
def test_value_for_an_unsupported_field_code_is_rejected_and_nothing_is_stored(
    business_db: str, field: str, option: str | None, value: str
) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, ctx = await employee(s)
            request_id = await ask(w, ctx, field, option)
            with pytest.raises(InvalidInput) as error:
                await w.answers.submit_answer(w.representative, request_id, answer(value))
            assert error.value.safe_details["reason"] == "field_code_not_answerable_yet"
            view = await w.answers.get_request(w.representative, request_id)
            assert view.status is RequestStatus.OPEN and view.request_version == 0
            for table in ("representative_answers", "integration_outbox", "audit_entries"):
                assert count(business_db, table, request_id) == 0, table
            assert await case_version(s, ctx) == 0
            # cannot_determine is format-independent and remains allowed.
            done = await w.answers.submit_answer(w.representative, request_id, answer())
            assert done.outcome is AnswerOutcome.CANNOT_DETERMINE

    run(go())


@pytest.mark.parametrize(
    ("field", "submit", "detail"),
    [
        (PARTICIPATION, answer("350.5555", unit="ILS"), "value"),
        (PARTICIPATION, answer("-1", unit="ILS"), "value"),
        (PARTICIPATION, answer("100000000", unit="ILS"), "value"),
        (GROUP, answer("ה"), "value"),
        (GROUP, answer("ד", unit="ILS"), "unit"),
        (PROPULSION, answer("diesel"), "value"),
    ],
)
def test_invalid_values_are_rejected(
    business_db: str, field: str, submit: AnswerSubmit, detail: str
) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, ctx = await employee(s)
            option = "vehicle:CAR-10" if field == PARTICIPATION else None
            request_id = await ask(w, ctx, field, option)
            with pytest.raises(InvalidInput) as error:
                await w.answers.submit_answer(w.representative, request_id, submit)
            assert error.value.safe_details["field"] == detail
            assert count(business_db, "representative_answers", request_id) == 0

    run(go())


# --- idempotency, duplicates and versions ----------------------------------------------


def test_replay_duplicate_payload_mismatch_and_stale_versions(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, ctx = await employee(s)
            request_id = await ask(w, ctx, PARTICIPATION, "vehicle:CAR-10")
            with pytest.raises(VersionConflict):
                await w.answers.submit_answer(
                    w.representative, request_id, answer("350", unit="ILS", version=1)
                )
            operation = uuid4()
            first = await w.answers.submit_answer(
                w.representative, request_id, answer("350", unit="ILS", operation_id=operation)
            )
            replay = await w.answers.submit_answer(
                w.representative, request_id, answer("350", unit="ILS", operation_id=operation)
            )
            assert replay == first
            assert count(business_db, "integration_outbox", request_id) == 1  # stable event
            with pytest.raises(PayloadMismatch):
                await w.answers.submit_answer(
                    w.representative, request_id, answer("351", unit="ILS", operation_id=operation)
                )
            with pytest.raises(InvalidStateTransition):  # a second answer (architecture §17)
                await w.answers.submit_answer(w.representative, request_id, money("350"))
            with pytest.raises(NotFoundError):
                await w.answers.submit_answer(w.representative, uuid4(), money("350"))
            assert count(business_db, "representative_answers", request_id) == 1
            assert await case_version(s, ctx) == 1

    run(go())


# --- authorization and privacy (Q4) ----------------------------------------------------


def test_only_active_representatives_and_only_minimal_context(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            who, ctx = await employee(s)
            request_id = await ask(w, ctx, PARTICIPATION, "vehicle:CAR-10")
            for call in (
                w.answers.list_requests(who),
                w.answers.get_request(who, request_id),
                w.answers.submit_answer(who, request_id, money("350")),
            ):
                with pytest.raises(ForbiddenError):
                    await call
            inactive_id = uuid4()
            async with s.p.uow() as uow:
                await uow.users.add(
                    UserAccount(
                        inactive_id,
                        f"r{inactive_id.hex[:16]}",
                        frozenset({Role.REPRESENTATIVE}),
                        active=False,
                    )
                )
                await uow.commit()
            with pytest.raises(UnauthenticatedError):
                await w.answers.get_request(actor(inactive_id, Role.REPRESENTATIVE), request_id)

            view = await w.answers.get_request(w.representative, request_id)
            assert set(view.model_dump(by_alias=True)) == {
                "requestId",
                "requestVersion",
                "status",
                "fieldCode",
                "expectedValueType",
                "questionText",
                "vehicle",
                "entitlementGroup",
                "createdAt",
            }
            assert view.vehicle is not None and view.vehicle.catalogue_id == "CAR-10"
            assert view.vehicle.commercial_name and view.entitlement_group == "ג"
            text = view.model_dump_json(by_alias=True)
            for secret in (str(ctx.case_id), str(ctx.conversation_id), str(who.subject_id)):
                assert secret not in text
            assert "18500" not in text
            queue = await w.answers.list_requests(w.representative, limit=200)
            assert all(r.status is RequestStatus.OPEN for r in queue)
            group_question = await ask(w, ctx, GROUP)
            group_view = await w.answers.get_request(w.representative, group_question)
            assert group_view.entitlement_group is None and group_view.vehicle is None

    run(go())


# --- outbox: atomicity, rollback, redelivery and acknowledgement; audit -----------------


class _FailingOutbox:
    async def add(self, event: object) -> None:
        raise RuntimeError("injected failure before commit")


def test_failure_before_commit_leaves_no_answer_event_or_change(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, ctx = await employee(s)
            request_id = await ask(w, ctx, PARTICIPATION, "vehicle:CAR-10")

            def failing() -> Any:
                uow = s.p.uow()
                uow.outbox = _FailingOutbox()
                return uow

            broken = RepresentativeAnswerService(
                failing, AuthorizationPolicy(), s.p.reference, SystemClock()
            )
            operation = uuid4()
            with pytest.raises(RuntimeError, match="injected"):
                await broken.submit_answer(
                    w.representative, request_id, money_with(operation, "350")
                )
            for table in ("representative_answers", "integration_outbox", "audit_entries"):
                assert count(business_db, table, request_id) == 0, table
            view = await w.answers.get_request(w.representative, request_id)
            assert view.status is RequestStatus.OPEN and view.request_version == 0
            assert await case_version(s, ctx) == 0
            async with s.p.uow() as uow:
                alternative = await uow.alternatives.find_active(ctx.case_id, "vehicle:CAR-10")
            assert alternative is not None and alternative.version == 0
            # The rolled-back claim does not block a retry of the same operation.
            done = await w.answers.submit_answer(
                w.representative, request_id, money_with(operation, "350")
            )
            assert done.application is AnswerApplication.APPLIED

    run(go())


def money_with(operation: UUID, raw: str) -> AnswerSubmit:
    return answer(raw, unit="ILS", operation_id=operation)


def test_feed_redelivers_until_acknowledged_and_audit_is_minimal(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, ctx = await employee(s)
            first = await ask(w, ctx, PARTICIPATION, "vehicle:CAR-10")
            second = await ask(w, ctx, GROUP)
            await w.answers.submit_answer(w.representative, first, money("350"))
            await w.answers.submit_answer(w.representative, second, answer("ד"))
            feed = SqlBusinessEventFeed(create_session_factory(s.p.engine))

            async def mine() -> list[Any]:
                events = await feed.pending(500)
                return [e for e in events if e.payload.case_id == ctx.case_id]

            delivered = await mine()
            assert [e.payload.request_id for e in delivered] == [first, second]
            assert delivered[0].sequence < delivered[1].sequence
            event = delivered[0]
            assert event.event_type == "representative.answered"
            assert event.payload == RepresentativeAnsweredEvent(
                event_id=event.event_id,
                occurred_at=event.payload.occurred_at,
                request_id=first,
                case_id=ctx.case_id,
                alternative_id=event.payload.alternative_id,
                request_version=1,
                case_version_after=1,
            )
            assert event.payload.alternative_id is not None
            assert delivered[1].payload.case_version_after == 2
            assert await mine() == delivered  # at-least-once: still pending
            await feed.acknowledge([event.event_id])
            await feed.acknowledge([event.event_id, uuid4()])  # idempotent, unknown ignored
            assert [e.event_id for e in await mine()] == [delivered[1].event_id]
            await feed.acknowledge([delivered[1].event_id])
            assert await mine() == []

            for statement in (
                "UPDATE business.integration_outbox SET payload = '{}'::jsonb WHERE id = %s",
                "UPDATE business.integration_outbox SET acknowledged_at = now() WHERE id = %s",
                "DELETE FROM business.integration_outbox WHERE id = %s",
            ):
                with pytest.raises(Exception, match="immutable|never deleted"):
                    sql(business_db, statement, event.event_id)

            rows = sql(
                business_db,
                "SELECT actor_subject_id, actor_role, action, field_code, details "
                "FROM business.audit_entries WHERE request_id = %s",
                first,
            )
            ((subject, role, action, field, details),) = rows
            assert subject == w.representative.subject_id and role == "representative"
            assert action == "representative_answer.submitted" and field == PARTICIPATION
            assert details["newValue"] == "350" and details["application"] == "applied"
            assert "18500" not in json.dumps(details, ensure_ascii=False)
            with pytest.raises(Exception, match="insert-only"):
                sql(business_db, "DELETE FROM business.audit_entries WHERE request_id = %s", first)
            with pytest.raises(Exception, match="insert-only"):
                sql(
                    business_db,
                    "UPDATE business.representative_answers SET note = 'x' WHERE request_id = %s",
                    first,
                )

    run(go())


# --- concurrency, lock order and retry ---------------------------------------------------


ROUNDS = 5


def test_concurrent_answer_update_question_and_comparison(business_db: str) -> None:
    """Repeated races on one case: no deadlock, no lost update, consistent versions."""

    async def round_() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, ctx = await employee(s)
            request_id = await ask(w, ctx, PARTICIPATION, "vehicle:CAR-10")
            results = await asyncio.gather(
                w.answers.submit_answer(w.representative, request_id, money("350")),
                s.cases.update_inputs(
                    ctx,
                    update(
                        "actual_update", 0, [set_("payroll.bonusGross", "10")], profile_version=1
                    ),
                ),
                w.questions.create_representative_question(
                    ctx,
                    CreateQuestionRequest.model_validate(
                        {
                            "fieldCode": GROUP,
                            "questionText": "מהי קבוצת הזכאות?",
                            "idempotencyKey": str(uuid4()),
                            "expectedCaseVersion": 0,
                        }
                    ),
                ),
                s.comparisons.compare_scenarios(ctx, compare(["vehicle:CAR-11"])),
                return_exceptions=True,
            )
            answered, updated, asked, compared = results
            assert not isinstance(answered, BaseException), answered
            for outcome in (updated, asked, compared):  # only business version conflicts
                if isinstance(outcome, BaseException):
                    assert isinstance(outcome, VersionConflict), repr(outcome)
            update_ok = not isinstance(updated, BaseException)
            assert await case_version(s, ctx) == 1 + int(update_ok)
            view = await w.answers.get_request(w.representative, request_id)
            assert view.status is RequestStatus.ANSWERED and view.request_version == 1
            async with s.p.uow() as uow:
                alternative = await uow.alternatives.find_active(ctx.case_id, "vehicle:CAR-10")
                profile = await uow.profiles.get(ctx.actor.subject_id)
            assert alternative is not None and alternative.version == 1
            assert "participation" in alternative.inputs  # not lost
            assert profile is not None and profile.version == 1 + int(update_ok)
            assert count(business_db, "representative_answers", request_id) == 1
            assert count(business_db, "integration_outbox", request_id) == 1

    for _ in range(ROUNDS):
        run(round_())


def test_concurrent_answers_to_one_request_commit_exactly_once(business_db: str) -> None:
    async def go() -> None:
        async with services(business_db) as s:
            w = await world(s)
            _who, ctx = await employee(s)
            request_id = await ask(w, ctx, PARTICIPATION, "vehicle:CAR-10")
            other = await ask(w, ctx, GROUP)
            same = uuid4()
            results = await asyncio.gather(
                *(
                    w.answers.submit_answer(w.representative, request_id, money(f"3{i}0"))
                    for i in range(4)
                ),
                w.answers.submit_answer(w.representative, other, money_or_group(same)),
                w.answers.submit_answer(w.representative, other, money_or_group(same)),
                return_exceptions=True,
            )
            distinct, replayed = results[:4], results[4:]
            assert sum(not isinstance(r, BaseException) for r in distinct) == 1
            assert all(
                isinstance(r, InvalidStateTransition)
                for r in distinct
                if isinstance(r, BaseException)
            )
            assert replayed[0] == replayed[1] and not isinstance(replayed[0], BaseException)
            assert count(business_db, "integration_outbox", request_id) == 1
            assert count(business_db, "integration_outbox", other) == 1
            assert await case_version(s, ctx) == 2  # one per committed answer, none lost

    run(go())


def money_or_group(operation: UUID) -> AnswerSubmit:
    return answer("ד", operation_id=operation)


def test_a_real_deadlock_surfaces_as_a_transient_conflict(business_db: str) -> None:
    """The opposite lock order (forbidden by the policy) deadlocks; PostgreSQL aborts one
    transaction and the Unit of Work reports it as `TransientConflictError`."""

    async def go() -> list[object]:
        async with services(business_db) as s:
            _a, first = await employee(s)
            _b, second = await employee(s)

            async def lock(one: UUID, two: UUID) -> None:
                async with s.p.uow() as uow:
                    await uow.cases.lock_by_id(one)
                    await asyncio.sleep(0.3)
                    await uow.cases.lock_by_id(two)
                    await uow.commit()

            return list(
                await asyncio.gather(
                    lock(first.case_id, second.case_id),
                    lock(second.case_id, first.case_id),
                    return_exceptions=True,
                )
            )

    outcomes = run(go())
    failures = [o for o in outcomes if isinstance(o, BaseException)]
    assert len(failures) == 1 and isinstance(failures[0], TransientConflictError)
    assert not isinstance(failures[0], BusinessError)  # never shown as a business conflict
