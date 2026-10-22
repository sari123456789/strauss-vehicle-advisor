"""Multi-turn workflow behavior with the contract fake and a scripted LLM (no network)."""

import re
from typing import Any
from uuid import UUID, uuid4

from app.ai.application.ports.graph_runner import (
    HistoryMessage,
    InputKind,
    TurnInput,
    TurnOutcome,
    TurnStatus,
)
from app.ai.application.ports.llm import LlmOutcome, LlmTaskKind
from app.ai.infrastructure.llm.guarded import DisabledLlm
from app.ai.workflow import messages_he as he
from app.ai.workflow.models import (
    FactField,
    FactMode,
    Intent,
    OptionReference,
    ProposedFact,
)
from app.ai.workflow.nodes.act import SALARY_FIELDS
from app.ai.workflow.runner import LangGraphTurnRunner
from app.contracts.case_inputs import (
    ConfirmProfileFieldsRequest,
    ConfirmProfileFieldsResult,
    SetChange,
    UpdateInputsRequest,
    UpdateInputsResult,
)
from app.contracts.catalogue import ResolveOccupationResult, ResolveVehicleResult
from app.contracts.common import Envelope, FieldPath, UpdateMode
from app.contracts.comparisons import CompareRequest, ComparisonResult
from app.contracts.readiness import ReadinessRequest, ReadinessResult, SelectionMode
from app.contracts.representatives import RequestStatusView
from app.contracts.tool_context import ActorRef, Origin, Role, ToolContext
from tests.fakes.ai.fake_llm import ScriptedLlm, failure, interpretation
from tests.fakes.fake_business_port import FakeBusinessPort

PENDING_REQUEST = UUID("c0000000-0000-4000-8000-000000000050")
RAW_CODES = re.compile(r"\b(40[0-9]|409|500|503)\b|NOT_FOUND|VERSION_CONFLICT|trace", re.I)


class SwitchablePort:
    """Delegates to a `FakeBusinessPort` whose scenario can change between turns."""

    def __init__(self, scenario: str = "success") -> None:
        self.fake = FakeBusinessPort(scenario)
        self.calls: list[tuple[str, Any]] = []
        self.overrides: dict[str, Envelope[Any]] = {}
        """Test-local responses for situations the shared fixtures do not cover."""

    def use(self, scenario: str) -> None:
        self.fake = FakeBusinessPort(scenario)

    def override(self, tool: str, result_type: type[Any], raw: dict[str, Any]) -> None:
        self.overrides[tool] = Envelope[result_type].model_validate(  # type: ignore[valid-type]
            {"traceId": "test-trace", **raw}
        )

    def __getattr__(self, name: str) -> Any:
        async def call(ctx: ToolContext, *args: Any) -> Any:
            self.calls.append((name, args[0] if args else None))
            if name in self.overrides:
                return self.overrides[name]
            return await getattr(self.fake, name)(ctx, *args)

        return call

    def tools(self) -> list[str]:
        return [name for name, _ in self.calls]

    def requests(self, name: str) -> list[Any]:
        return [request for tool, request in self.calls if tool == name]


CTX = ToolContext(
    actor=ActorRef(subject_id=uuid4(), roles=frozenset({Role.EMPLOYEE}), origin=Origin.WORKER),
    conversation_id=UUID("c0000000-0000-4000-8000-000000000002"),
    case_id=UUID("c0000000-0000-4000-8000-000000000001"),
    trace_id="trace-1",
)


def make(
    scenario: str = "success", **options: Any
) -> tuple[LangGraphTurnRunner, SwitchablePort, ScriptedLlm]:
    port, llm = SwitchablePort(scenario), ScriptedLlm()
    return LangGraphTurnRunner(port, llm, **options), port, llm  # type: ignore[arg-type]


async def say(runner: LangGraphTurnRunner, text: str, input_id: UUID | None = None) -> TurnOutcome:
    turn = TurnInput(input_id or uuid4(), InputKind.USER_MESSAGE, text=text)
    return await runner.run_turn(thread_id="thread-1", tool_context=CTX, turn=turn)


def text_of(outcome: TurnOutcome) -> str:
    return "\n".join(m.text for m in outcome.messages)


def assert_employee_safe(outcome: TurnOutcome) -> None:
    assert outcome.messages, "every turn answers the employee"
    assert not RAW_CODES.search(text_of(outcome))


def fact(
    field: FactField, value: str, mode: FactMode = FactMode.ACTUAL, quote: str = ""
) -> ProposedFact:
    return ProposedFact(field=field, value=value, mode=mode, quote=quote or value)


# --- Catalogue browsing ------------------------------------------------------------------------


async def test_listing_vehicles_asks_no_salary_questions() -> None:
    runner, port, llm = make()
    llm.interpret(interpretation(Intent.LIST_OPTIONS))
    outcome = await say(runner, "איזה רכבים מתאימים לקבוצת הזכאות שלי?")
    assert port.tools() == ["get_conversation_context", "list_eligible_options"]
    assert "ATTO 3" in text_of(outcome) and "MG4" in text_of(outcome)
    assert "שכר" not in text_of(outcome)
    assert llm.calls(LlmTaskKind.COMPOSE) == []  # lists are rendered by templates
    state = await runner.conversation_state("thread-1")
    assert [o["optionKey"] for o in state["displayed_options"]] == [
        "vehicle:CAR-10",
        "vehicle:CAR-06",
    ]
    assert_employee_safe(outcome)


async def test_the_model_never_receives_salary_values() -> None:
    runner, _, llm = make()
    llm.interpret(interpretation(Intent.LIST_OPTIONS))
    await say(runner, "תראה לי רכבים")
    prompt = " ".join(m.text for m in llm.tasks[0].messages) + llm.tasks[0].system_instruction
    assert "18500" not in prompt and "usable" in prompt  # field statuses only
    assert str(CTX.case_id) not in prompt and str(CTX.actor.subject_id) not in prompt


async def test_ordinal_reference_resolves_against_the_displayed_list() -> None:
    runner, port, llm = make()
    llm.interpret(
        interpretation(Intent.LIST_OPTIONS),
        interpretation(
            Intent.CALCULATE_OPTIONS, option_refs=[OptionReference(kind="ordinal", value="2")]
        ),
    )
    llm.compose("הנטו המשוער הוא {{net:vehicle:CAR-10}}.")
    await say(runner, "אילו רכבים יש?")
    outcome = await say(runner, "כמה נטו אקבל עם השני?")
    readiness: ReadinessRequest = port.requests("check_calculation_readiness")[0]
    assert readiness.selection is not None
    assert readiness.selection.mode is SelectionMode.EXPLICIT
    assert readiness.selection.option_keys == ["vehicle:CAR-06"]
    assert readiness.include_current is False
    assert_employee_safe(outcome)


# --- Calculations ----------------------------------------------------------------------------


async def test_net_calculation_amounts_come_from_the_tool_result() -> None:
    runner, port, llm = make()
    llm.interpret(interpretation(Intent.COMPARE_TO_CURRENT, all_options=True))
    llm.compose("לפי החישוב, {{delta:vehicle:CAR-10}} לעומת היום.")
    outcome = await say(runner, "מה עדיף לי, הרכב הנוכחי או רכב אחר?")
    compare: CompareRequest = port.requests("compare_scenarios")[0]
    assert compare.include_current is True and compare.kind.value == "regular"
    assert compare.expected_case_version == 3 and compare.expected_profile_version == 5
    text = text_of(outcome)
    assert "11,693 ₪" in text and "11,032 ₪" in text and "+661 ₪" in text
    assert outcome.messages[-1].kind == "explanation"
    assert "לפי החישוב, +661 ₪ לעומת היום." in text
    assert outcome.llm_calls == 2
    assert_employee_safe(outcome)


async def test_model_prose_with_invented_numbers_is_dropped() -> None:
    runner, _, llm = make()
    llm.interpret(interpretation(Intent.CALCULATE_OPTIONS, all_options=True))
    llm.compose("תקבל בערך 12,500 ₪ נטו!")
    outcome = await say(runner, "כמה ישתנה לי הנטו אם אעבור לרכב חשמלי?")
    assert "12,500" not in text_of(outcome)
    assert all(m.kind != "explanation" for m in outcome.messages)
    assert "11,693 ₪" in text_of(outcome)  # the deterministic summary is still shown


async def test_compose_can_be_disabled_to_save_llm_calls() -> None:
    runner, _, llm = make(compose_with_llm=False)
    llm.interpret(interpretation(Intent.CALCULATE_OPTIONS, all_options=True))
    outcome = await say(runner, "תחשב לי נטו לכל הרכבים")
    assert outcome.llm_calls == 1 and "11,693 ₪" in text_of(outcome)


async def test_missing_salary_fact_is_asked_and_never_calculated() -> None:
    runner, port, llm = make("missing")
    llm.interpret(interpretation(Intent.CALCULATE_OPTIONS, all_options=True))
    outcome = await say(runner, "תחשב לי נטו לכל הרכבים")
    assert "compare_scenarios" not in port.tools()
    assert "הבונוס החודשי ברוטו" in text_of(outcome)
    assert outcome.messages[0].payload["fields"] == ["payroll.bonusGross"]
    assert_employee_safe(outcome)


async def test_a_field_is_not_asked_forever() -> None:
    runner, _, llm = make("missing")
    llm.interpret(*[interpretation(Intent.CALCULATE_OPTIONS, all_options=True)] * 3)
    await say(runner, "תחשב")
    await say(runner, "תחשב")
    third = await say(runner, "תחשב")
    assert third.messages[0].kind == "guidance"  # explains where to find it instead of re-asking


async def test_partial_results_and_pending_representative_do_not_block() -> None:
    runner, port, llm = make("blocked")
    llm.interpret(interpretation(Intent.CALCULATE_OPTIONS, all_options=True))
    outcome = await say(runner, "תחשב לי את כל החלופות")
    text = text_of(outcome)
    assert "11,693 ₪" in text
    assert "ממתין לתשובת נציג" in text and "לא זמין לפי הכללים" in text
    compare: CompareRequest = port.requests("compare_scenarios")[0]
    assert compare.selection.option_keys == ["vehicle:CAR-10"]
    state = await runner.conversation_state("thread-1")
    assert state["pending_tasks"][str(PENDING_REQUEST)]["optionKey"] == "vehicle:CAR-11"


async def test_representative_answer_updates_only_the_affected_option() -> None:
    runner, port, llm = make("blocked")
    llm.interpret(
        interpretation(Intent.CALCULATE_OPTIONS, all_options=True),
        interpretation(Intent.LIST_OPTIONS),
    )
    await say(runner, "תחשב לי את כל החלופות")
    await say(runner, "תראה לי רכבים חשמליים")  # the employee moved on
    goal_before = (await runner.conversation_state("thread-1"))["active_goal"]
    model_calls_before = len(llm.tasks)
    port.use("success")
    answer_request(port, "answered")
    answered = await runner.run_turn(
        thread_id="thread-1",
        tool_context=CTX,
        turn=TurnInput(uuid4(), InputKind.REPRESENTATIVE_ANSWERED, request_id=PENDING_REQUEST),
    )
    assert answered.messages[0].kind == "representative_answer"
    readiness: ReadinessRequest = port.requests("check_calculation_readiness")[-1]
    assert readiness.selection is not None and readiness.selection.option_keys == ["vehicle:CAR-11"]
    state = await runner.conversation_state("thread-1")
    assert state["active_goal"] == goal_before and state["pending_tasks"] == {}
    assert len(llm.tasks) == model_calls_before  # no model call for the asynchronous update


async def test_answer_to_a_cancelled_request_is_ignored() -> None:
    runner, port, llm = make("blocked")
    llm.interpret(
        interpretation(Intent.CALCULATE_OPTIONS, all_options=True), interpretation(Intent.CANCEL)
    )
    await say(runner, "תחשב לי את כל החלופות")
    await say(runner, "עזוב, תבטל")
    calls_before = len(port.calls)
    outcome = await runner.run_turn(
        thread_id="thread-1",
        tool_context=CTX,
        turn=TurnInput(uuid4(), InputKind.REPRESENTATIVE_ANSWERED, request_id=PENDING_REQUEST),
    )
    assert outcome.messages == ()
    assert port.tools()[calls_before:] == ["get_conversation_context"]


# --- Actual versus hypothetical --------------------------------------------------------------


async def test_actual_correction_uses_both_versions_and_a_stable_operation_id() -> None:
    runner, port, llm = make()
    llm.interpret(interpretation(Intent.UPDATE_FACTS, facts=[fact(FactField.BONUS_GROSS, "1000")]))
    input_id = uuid4()
    outcome = await say(runner, "טעיתי, הבונוס שלי הוא 1000 בחודש", input_id)
    request: UpdateInputsRequest = port.requests("update_conversation_inputs")[0]
    assert request.mode is UpdateMode.ACTUAL_UPDATE
    assert (request.expected_case_version, request.expected_profile_version) == (3, 5)
    assert request.source_message_id == input_id
    replay, replay_port, replay_llm = make()
    replay_llm.interpret(
        interpretation(Intent.UPDATE_FACTS, facts=[fact(FactField.BONUS_GROSS, "1000")])
    )
    await say(replay, "טעיתי, הבונוס שלי הוא 1000 בחודש", input_id)
    assert (
        replay_port.requests("update_conversation_inputs")[0].operation_id == request.operation_id
    )
    assert "compare_scenarios" not in port.tools()
    assert "חישובים קודמים" in text_of(outcome)  # stale results are disclosed


async def test_hypothetical_never_updates_the_profile_and_runs_a_hypothetical_comparison() -> None:
    runner, port, llm = make()
    llm.interpret(
        interpretation(
            Intent.CALCULATE_OPTIONS,
            all_options=True,
            facts=[fact(FactField.BASE_GROSS, "20000", FactMode.HYPOTHETICAL)],
        )
    )
    await say(runner, "מה יקרה אם המשכורת שלי תעלה ל-20000?")
    update: UpdateInputsRequest = port.requests("update_conversation_inputs")[0]
    assert update.mode is UpdateMode.HYPOTHETICAL and update.expected_profile_version is None
    compare: CompareRequest = port.requests("compare_scenarios")[0]
    assert compare.kind.value == "hypothetical" and compare.expected_profile_version is None


async def test_ambiguous_actual_or_hypothetical_is_clarified_before_any_write() -> None:
    runner, port, llm = make()
    llm.interpret(
        interpretation(
            Intent.CALCULATE_OPTIONS,
            facts=[fact(FactField.BASE_GROSS, "20000", FactMode.AMBIGUOUS)],
        )
    )
    outcome = await say(runner, "תבדוק עם משכורת 20000")
    assert "update_conversation_inputs" not in port.tools()
    assert "compare_scenarios" not in port.tools()
    assert "מה יקרה אם" in text_of(outcome)


async def test_net_salary_is_never_written_as_gross() -> None:
    runner, port, llm = make()
    llm.interpret(
        interpretation(
            Intent.UPDATE_FACTS,
            facts=[fact(FactField.BASE_GROSS, "12000", quote="אני מקבל 12 אלף נטו")],
        )
    )
    outcome = await say(runner, "אני מקבל 12 אלף נטו")
    assert "update_conversation_inputs" not in port.tools()
    assert "ברוטו" in text_of(outcome)


async def test_version_conflict_reloads_and_explains_in_hebrew() -> None:
    runner, port, llm = make("stale")
    llm.interpret(interpretation(Intent.UPDATE_FACTS, facts=[fact(FactField.BONUS_GROSS, "1000")]))
    outcome = await say(runner, "הבונוס שלי 1000")
    assert port.tools() == [
        "get_conversation_context",
        "update_conversation_inputs",
        "get_conversation_context",
    ]
    assert "עודכנו בינתיים" in text_of(outcome)
    assert_employee_safe(outcome)


async def test_current_vehicle_description_is_resolved_by_the_business_tools() -> None:
    runner, port, llm = make()
    llm.interpret(
        interpretation(Intent.UPDATE_FACTS, facts=[fact(FactField.CURRENT_VEHICLE, "ATTO 3 2025")])
    )
    await say(runner, "יש לי ATTO 3 משנת 2025")
    resolve = port.requests("resolve_vehicle")[0]
    assert resolve.query.commercial_name == "ATTO 3" and resolve.query.registration_year == "2025"
    update: UpdateInputsRequest = port.requests("update_conversation_inputs")[0]
    assert update.changes[0].value.catalogue_id == "CAR-10"


# --- History, explanations and general questions ---------------------------------------------


async def test_previous_comparison_is_shown_as_stale_without_recalculation() -> None:
    runner, port, llm = make("stale")
    llm.interpret(interpretation(Intent.SHOW_HISTORY))
    outcome = await say(runner, "תראה לי את ההשוואה הקודמת")
    assert "compare_scenarios" not in port.tools()
    assert "אינו עדכני" in text_of(outcome)


async def test_general_question_keeps_the_goal_and_falls_back_safely() -> None:
    runner, _, llm = make()
    llm.interpret(interpretation(Intent.GENERAL_QUESTION))
    llm.compose('כל נקודת זיכוי שווה 242 ש"ח.')  # contains a number -> rejected
    outcome = await say(runner, "מה זה נקודות זיכוי?")
    assert "242" not in text_of(outcome)
    assert outcome.messages[-1].kind == "info"


# --- Failures, budgets, duplicates and authorization -----------------------------------------


async def test_foreign_conversation_is_rejected_before_any_model_call() -> None:
    runner, port, llm = make("unauthorized")
    outcome = await say(runner, "תראה לי את התיק של העובד השני")
    assert llm.tasks == [] and port.tools() == ["get_conversation_context"]
    assert "לא מצאתי" in text_of(outcome)
    assert_employee_safe(outcome)


async def test_quota_exhaustion_becomes_a_natural_message() -> None:
    runner, port, llm = make()
    llm.script(LlmTaskKind.INTERPRET, failure(LlmOutcome.QUOTA_EXHAUSTED, 3600))
    outcome = await say(runner, "שלום")
    assert port.tools() == ["get_conversation_context"]
    assert outcome.messages[0].payload["outcome"] == "quota_exhausted"
    assert_employee_safe(outcome)


async def test_rate_limit_is_reported_with_retry_hint() -> None:
    runner, _, llm = make()
    llm.script(LlmTaskKind.INTERPRET, failure(LlmOutcome.RATE_LIMITED, 30))
    outcome = await say(runner, "שלום")
    assert outcome.messages[0].payload == {"outcome": "rate_limited", "retryAfterSeconds": 30}
    assert he.LLM_RATE_LIMITED in text_of(outcome)


async def test_invalid_model_output_is_retried_once_then_reported() -> None:
    runner, _, llm = make()
    llm.script(
        LlmTaskKind.INTERPRET,
        failure(LlmOutcome.INVALID_OUTPUT),
        failure(LlmOutcome.INVALID_OUTPUT),
    )
    outcome = await say(runner, "???")
    assert outcome.llm_calls == 2 and len(llm.tasks) == 2
    assert_employee_safe(outcome)


async def test_workflow_runs_without_any_llm_configured() -> None:
    port = SwitchablePort()
    runner = LangGraphTurnRunner(port, DisabledLlm())  # type: ignore[arg-type]
    outcome = await say(runner, "שלום")
    assert outcome.messages and outcome.llm_calls == 1
    assert_employee_safe(outcome)


async def test_tool_budget_produces_an_explicit_outcome() -> None:
    runner, _, llm = make(max_tool_calls=2)
    llm.interpret(interpretation(Intent.CALCULATE_OPTIONS, all_options=True))
    outcome = await say(runner, "תחשב")
    assert outcome.tool_calls == 2
    assert outcome.messages[-1].payload["code"] == "TOOL_BUDGET_EXCEEDED"


async def test_duplicate_input_is_not_processed_again() -> None:
    runner, port, llm = make()
    llm.interpret(interpretation(Intent.LIST_OPTIONS))
    input_id = uuid4()
    first = await say(runner, "תראה רכבים", input_id)
    calls = len(port.calls)
    second = await say(runner, "תראה רכבים", input_id)
    assert second.status is TurnStatus.DUPLICATE
    assert len(port.calls) == calls and len(llm.tasks) == 1
    # the checkpoint keeps references only; the text is published from durable staging
    assert second.messages == ()
    assert [r.message_id for r in second.message_refs] == [
        str(m.message_id) for m in first.messages
    ]


async def test_message_ids_are_deterministic_across_replays() -> None:
    input_id = uuid4()
    outcomes = []
    for _ in range(2):
        runner, _, llm = make()
        llm.interpret(interpretation(Intent.LIST_OPTIONS))
        outcomes.append(await say(runner, "תראה רכבים", input_id))
    assert [m.message_id for m in outcomes[0].messages] == [
        m.message_id for m in outcomes[1].messages
    ]


async def test_recent_history_is_bounded_in_the_prompt() -> None:
    runner, _, llm = make()
    llm.interpret(interpretation(Intent.SMALLTALK))
    history = tuple(HistoryMessage("user", f"הודעה {i}") for i in range(20))
    turn = TurnInput(uuid4(), InputKind.USER_MESSAGE, text="שלום", recent_messages=history)
    await runner.run_turn(thread_id="thread-1", tool_context=CTX, turn=turn)
    assert len(llm.tasks[0].messages) == 7  # six history messages plus the new one


# --- G1-G5: representative status, occupations and confirmations ------------------------------

PARTICIPATION = "currentArrangement.participation"


def answer_request(port: SwitchablePort, status: str, outcome: str | None = None) -> None:
    answer: dict[str, Any] | None = None
    if outcome is not None:
        answer = {"outcome": outcome, "answeredAt": "2026-10-16T08:00:00Z"}
        if outcome == "answered":
            answer |= {"value": "350.00", "unit": "ILS"}
    port.override(
        "get_representative_request",
        RequestStatusView,
        {
            "status": "ok",
            "data": {
                "requestId": str(PENDING_REQUEST),
                "status": status,
                "fieldCode": "alternative.participation",
                "optionKey": "vehicle:CAR-11",
                "answer": answer,
                "createdAt": "2026-10-15T08:06:00Z",
                "updatedAt": "2026-10-16T08:00:00Z",
            },
        },
    )


async def answer_turn(runner: LangGraphTurnRunner) -> TurnOutcome:
    return await runner.run_turn(
        thread_id="thread-1",
        tool_context=CTX,
        turn=TurnInput(uuid4(), InputKind.REPRESENTATIVE_ANSWERED, request_id=PENDING_REQUEST),
    )


async def pending_runner() -> tuple[LangGraphTurnRunner, SwitchablePort]:
    runner, port, llm = make("blocked")
    llm.interpret(interpretation(Intent.CALCULATE_OPTIONS, all_options=True))
    await say(runner, "תחשב לי את כל החלופות")
    port.use("success")
    return runner, port


async def test_answer_event_is_verified_and_an_open_request_stays_pending() -> None:
    runner, port = await pending_runner()
    calls_before = len(port.calls)
    outcome = await answer_turn(runner)  # the shared fixture reports the request as still open
    assert outcome.messages == ()
    assert port.tools()[calls_before:] == ["get_conversation_context", "get_representative_request"]
    assert port.requests("get_representative_request")[0].request_id == PENDING_REQUEST
    state = await runner.conversation_state("thread-1")
    assert str(PENDING_REQUEST) in state["pending_tasks"]


async def test_undetermined_answer_explains_and_calculates_nothing() -> None:
    runner, port = await pending_runner()
    answer_request(port, "cannot_determine", "cannot_determine")
    calls_before = len(port.calls)
    outcome = await answer_turn(runner)
    assert "לא יכול היה לקבוע" in text_of(outcome)
    assert "compare_scenarios" not in port.tools()[calls_before:]
    assert (await runner.conversation_state("thread-1"))["pending_tasks"] == {}
    assert_employee_safe(outcome)


async def test_unknown_or_foreign_request_is_dropped_silently() -> None:
    runner, port = await pending_runner()
    port.override(
        "get_representative_request",
        RequestStatusView,
        {"status": "error", "error": {"code": "NOT_FOUND", "traceId": "test-trace"}},
    )
    outcome = await answer_turn(runner)
    assert outcome.messages == ()
    assert (await runner.conversation_state("thread-1"))["pending_tasks"] == {}


async def test_pending_field_code_is_checkpointed_as_a_plain_string() -> None:
    runner, _ = await pending_runner()
    task = (await runner.conversation_state("thread-1"))["pending_tasks"][str(PENDING_REQUEST)]
    assert type(task["fieldCode"]) is str and task["fieldCode"] == "alternative.participation"


def needs_confirmation(port: SwitchablePort) -> None:
    port.override(
        "check_calculation_readiness",
        ReadinessResult,
        {
            "status": "partial",
            "caseVersion": 3,
            "issues": [
                {
                    "code": "AMBIGUOUS_INPUT",
                    "field": PARTICIPATION,
                    "reason": "confirmation_required",
                }
            ],
            "data": {
                "readyScenarioIds": ["vehicle:CAR-10"],
                "baselineReady": False,
                "scopeComplete": True,
            },
        },
    )


def confirms(port: SwitchablePort) -> None:
    port.override(
        "confirm_profile_fields",
        ConfirmProfileFieldsResult,
        {
            "status": "ok",
            "data": {
                "confirmedFields": [PARTICIPATION],
                "newCaseVersion": 4,
                "newProfileVersion": 6,
            },
        },
    )


async def test_unconfirmed_baseline_asks_only_that_fact_and_shows_no_delta() -> None:
    runner, port, llm = make()
    needs_confirmation(port)
    llm.interpret(interpretation(Intent.COMPARE_TO_CURRENT, all_options=True))
    llm.compose("החישוב מוכן.")
    outcome = await say(runner, "מה עדיף לי לעומת היום?")
    compare: CompareRequest = port.requests("compare_scenarios")[0]
    assert compare.include_current is False  # never a definitive delta from an unconfirmed fact
    asks = [m for m in outcome.messages if m.kind == "confirmation_request"]
    assert len(asks) == 1 and asks[0].payload["fields"] == [PARTICIPATION]
    assert "שכר" not in asks[0].text  # targeted: no blanket profile questionnaire
    assert "confirm_profile_fields" not in port.tools()  # nothing is confirmed implicitly
    state = await runner.conversation_state("thread-1")
    assert state["awaiting_confirmation"] == [PARTICIPATION]


async def test_explicit_confirmation_confirms_asked_facts_and_resumes_the_goal() -> None:
    runner, port, llm = make()
    needs_confirmation(port)
    llm.interpret(
        interpretation(Intent.COMPARE_TO_CURRENT, all_options=True),
        interpretation(Intent.CONFIRM_FACTS),
    )
    await say(runner, "מה עדיף לי לעומת היום?")
    del port.overrides["check_calculation_readiness"]
    confirms(port)
    input_id = uuid4()
    outcome = await say(runner, "כן, זה עדיין נכון", input_id)
    confirm: ConfirmProfileFieldsRequest = port.requests("confirm_profile_fields")[0]
    assert [f.value for f in confirm.fields] == [PARTICIPATION]
    assert (confirm.expected_case_version, confirm.expected_profile_version) == (3, 5)
    assert confirm.source_message_id == input_id
    compare: CompareRequest = port.requests("compare_scenarios")[-1]
    assert compare.include_current is True
    assert (compare.expected_case_version, compare.expected_profile_version) == (4, 6)
    assert any(m.kind == "confirmation" for m in outcome.messages)
    assert (await runner.conversation_state("thread-1"))["awaiting_confirmation"] == []


async def test_confirmation_resumes_exactly_the_explicitly_selected_option() -> None:
    runner, port, llm = make()
    llm.interpret(
        interpretation(Intent.LIST_OPTIONS),
        interpretation(
            Intent.COMPARE_TO_CURRENT,
            option_refs=[OptionReference(kind="catalogue_id", value="CAR-06")],
        ),
        interpretation(Intent.CONFIRM_FACTS),
    )
    await say(runner, "אילו רכבים יש?")  # stores a result set for the whole list
    needs_confirmation(port)
    await say(runner, "כמה אקבל עם MG4 לעומת היום?")
    goal = (await runner.conversation_state("thread-1"))["active_goal"]
    assert goal["optionKeys"] == ["vehicle:CAR-06"] and goal["includeCurrent"] is True
    del port.overrides["check_calculation_readiness"]
    confirms(port)
    await say(runner, "כן, נכון")
    # The AI's own selection; `compare_scenarios` then receives what readiness reports ready
    # (a canned list in this fake; the real-Business test asserts the compare arguments).
    readiness: ReadinessRequest = port.requests("check_calculation_readiness")[-1]
    assert readiness.selection is not None
    assert readiness.selection.mode is SelectionMode.EXPLICIT  # never the earlier result set
    assert readiness.selection.option_keys == ["vehicle:CAR-06"]
    assert readiness.include_current is True
    assert port.requests("compare_scenarios")[-1].include_current is True


async def test_a_goal_without_an_explicit_selection_keeps_the_previous_behavior() -> None:
    runner, port, llm = make()
    needs_confirmation(port)
    llm.interpret(
        interpretation(Intent.COMPARE_TO_CURRENT, all_options=True),
        interpretation(Intent.CONFIRM_FACTS),
    )
    await say(runner, "מה עדיף לי לעומת היום?")
    assert "optionKeys" not in (await runner.conversation_state("thread-1"))["active_goal"]
    del port.overrides["check_calculation_readiness"]
    confirms(port)
    await say(runner, "כן")
    readiness: ReadinessRequest = port.requests("check_calculation_readiness")[-1]
    assert readiness.selection is not None
    assert readiness.selection.mode is SelectionMode.ALL_ELIGIBLE


async def test_confirmation_operation_id_is_stable_across_replays() -> None:
    input_id, operation_ids = uuid4(), []
    for _ in range(2):
        runner, port, llm = make()
        needs_confirmation(port)
        confirms(port)
        llm.interpret(
            interpretation(Intent.COMPARE_TO_CURRENT, all_options=True),
            interpretation(Intent.CONFIRM_FACTS),
        )
        await say(runner, "מה עדיף לי לעומת היום?", UUID(int=1))
        await say(runner, "כן", input_id)
        operation_ids.append(port.requests("confirm_profile_fields")[0].operation_id)
    assert operation_ids[0] == operation_ids[1]


async def test_nothing_is_confirmed_without_a_pending_question() -> None:
    runner, port, llm = make()
    llm.interpret(interpretation(Intent.CONFIRM_FACTS))
    outcome = await say(runner, "כן, הכל נכון")
    assert "confirm_profile_fields" not in port.tools()
    assert outcome.messages[-1].kind == "info"


async def test_a_value_stated_in_the_same_turn_is_never_confirmed() -> None:
    runner, port, llm = make()
    needs_confirmation(port)
    llm.interpret(
        interpretation(Intent.COMPARE_TO_CURRENT, all_options=True),
        interpretation(
            Intent.CONFIRM_FACTS,
            facts=[fact(FactField.CURRENT_PARTICIPATION, "500", FactMode.HYPOTHETICAL)],
        ),
    )
    await say(runner, "מה עדיף לי לעומת היום?")
    await say(runner, "נכון, אבל מה אם ההשתתפות תהיה 500?")
    assert "confirm_profile_fields" not in port.tools()
    update: UpdateInputsRequest = port.requests("update_conversation_inputs")[0]
    assert update.mode is UpdateMode.HYPOTHETICAL


async def test_confirmation_version_conflict_reloads_and_asks_again() -> None:
    runner, port, llm = make()
    needs_confirmation(port)
    llm.interpret(
        interpretation(Intent.COMPARE_TO_CURRENT, all_options=True),
        interpretation(Intent.CONFIRM_FACTS),
    )
    await say(runner, "מה עדיף לי לעומת היום?")
    port.override(
        "confirm_profile_fields",
        ConfirmProfileFieldsResult,
        {"status": "error", "error": {"code": "VERSION_CONFLICT", "traceId": "test-trace"}},
    )
    calls_before = len(port.calls)
    outcome = await say(runner, "כן")
    assert port.tools()[calls_before:] == [
        "get_conversation_context",
        "confirm_profile_fields",
        "get_conversation_context",
    ]
    assert "עודכנו בינתיים" in text_of(outcome)
    assert (await runner.conversation_state("thread-1"))["awaiting_confirmation"] == [PARTICIPATION]
    assert_employee_safe(outcome)


async def test_listing_and_opening_never_trigger_confirmation() -> None:
    runner, port, llm = make()
    llm.interpret(interpretation(Intent.SMALLTALK), interpretation(Intent.LIST_OPTIONS))
    await say(runner, "שלום")
    await say(runner, "אילו רכבים מתאימים לי?")
    assert "confirm_profile_fields" not in port.tools()
    assert "check_calculation_readiness" not in port.tools()


async def test_resolved_occupation_is_saved_through_update_inputs() -> None:
    runner, port, llm = make()
    llm.interpret(
        interpretation(Intent.UPDATE_FACTS, facts=[fact(FactField.OCCUPATION_ID, "מפתחת תוכנה")])
    )
    await say(runner, "אני מפתחת תוכנה")
    assert port.requests("resolve_occupation")[0].query == "מפתחת תוכנה"
    update: UpdateInputsRequest = port.requests("update_conversation_inputs")[0]
    assert update.mode is UpdateMode.ACTUAL_UPDATE
    change = update.changes[0]
    assert isinstance(change, SetChange)
    assert change.field.value == "employee.occupationId" and change.value == "OCC-SOFTWARE-DEV"


async def test_ambiguous_occupation_is_never_auto_selected() -> None:
    runner, port, llm = make()
    port.override(
        "resolve_occupation",
        ResolveOccupationResult,
        {
            "status": "ok",
            "data": {
                "status": "ambiguous",
                "candidates": [{"occupationId": "OCC-SOFTWARE-DEV", "nameHe": "מפתח תוכנה"}],
            },
        },
    )
    llm.interpret(
        interpretation(Intent.UPDATE_FACTS, facts=[fact(FactField.OCCUPATION_ID, "מתכנת")]),
        interpretation(Intent.UPDATE_FACTS, facts=[fact(FactField.OCCUPATION_ID, "1")]),
    )
    first = await say(runner, "אני מתכנת")
    assert "update_conversation_inputs" not in port.tools()  # even with a single candidate
    assert "מפתח תוכנה" in text_of(first)
    await say(runner, "הראשון")
    assert port.tools().count("resolve_occupation") == 1  # the choice is resolved by code
    update: UpdateInputsRequest = port.requests("update_conversation_inputs")[0]
    assert isinstance(update.changes[0], SetChange)
    assert update.changes[0].value == "OCC-SOFTWARE-DEV"
    assert (await runner.conversation_state("thread-1"))["occupation_candidates"] == []


async def test_unknown_occupation_asks_again_without_writing() -> None:
    runner, port, llm = make()
    port.override(
        "resolve_occupation",
        ResolveOccupationResult,
        {"status": "needs_information", "data": {"status": "not_found"}},
    )
    llm.interpret(
        interpretation(Intent.UPDATE_FACTS, facts=[fact(FactField.OCCUPATION_ID, "אסטרונאוט")])
    )
    outcome = await say(runner, "אני אסטרונאוט")
    assert "update_conversation_inputs" not in port.tools()
    assert "לא מצאתי עיסוק" in text_of(outcome)


async def test_option_list_from_an_older_release_is_dropped() -> None:
    runner, port, llm = make()
    llm.interpret(interpretation(Intent.LIST_OPTIONS), interpretation(Intent.CALCULATE_OPTIONS))
    await say(runner, "אילו רכבים יש?")
    port.override(
        "compare_scenarios",
        ComparisonResult,
        {
            "status": "error",
            "error": {"code": "VERSION_CONFLICT", "traceId": "test-trace"},
            "issues": [{"code": "STALE_REVISION", "reason": "result_set_release_changed"}],
        },
    )
    outcome = await say(runner, "תחשב את כולן")
    assert outcome.messages[-1].kind == "options_changed"
    state = await runner.conversation_state("thread-1")
    assert state["result_set_id"] is None and state["displayed_options"] == []
    assert_employee_safe(outcome)


async def test_committed_update_with_conflicts_is_not_treated_as_usable() -> None:
    runner, port, llm = make()
    port.override(
        "update_conversation_inputs",
        UpdateInputsResult,
        {
            "status": "ok",
            "data": {
                "acceptedFields": ["currentArrangement.propulsion"],
                "conflicts": [
                    {
                        "code": "SOURCE_CONFLICT",
                        "field": "currentArrangement.propulsion",
                        "reason": "catalogue_propulsion_differs",
                    }
                ],
                "newCaseVersion": 4,
                "newProfileVersion": 6,
            },
        },
    )
    llm.interpret(
        interpretation(Intent.UPDATE_FACTS, facts=[fact(FactField.CURRENT_PROPULSION, "electric")])
    )
    outcome = await say(runner, "הרכב שלי חשמלי")
    assert any(m.kind == "conflict" for m in outcome.messages)
    assert "סתירה" in text_of(outcome)


# --- one confirmation of saved salary facts per conversation -------------------------------
# Rules (approved 2026-10-22): ask once, only when a calculation would rest entirely on salary
# facts saved in an earlier conversation; never when the employee gives salary facts here;
# a comparison asks about today's arrangement in the same message; never a second time.


async def saved_salary(port: SwitchablePort, fields: tuple[str, ...] = SALARY_FIELDS) -> None:
    """The case context reports these salary facts as saved but not stated in this case."""
    envelope = await port.fake.get_conversation_context(CTX)
    assert envelope.data is not None
    data = envelope.data.model_copy(
        update={"fields_requiring_confirmation": [FieldPath(f) for f in fields]}
    )
    port.overrides["get_conversation_context"] = envelope.model_copy(update={"data": data})


def accepts(port: SwitchablePort, *fields: str) -> None:
    port.override(
        "update_conversation_inputs",
        UpdateInputsResult,
        {
            "status": "ok",
            "data": {"acceptedFields": list(fields), "newCaseVersion": 4, "newProfileVersion": 6},
        },
    )


def confirms_salary(port: SwitchablePort, *extra: str) -> None:
    port.override(
        "confirm_profile_fields",
        ConfirmProfileFieldsResult,
        {
            "status": "ok",
            "data": {
                "confirmedFields": [*SALARY_FIELDS, *extra],
                "newCaseVersion": 4,
                "newProfileVersion": 6,
            },
        },
    )


async def test_net_from_saved_salary_asks_one_confirmation_before_calculating() -> None:
    runner, port, llm = make()
    await saved_salary(port)
    llm.interpret(interpretation(Intent.CALCULATE_OPTIONS, all_options=True))
    outcome = await say(runner, "כמה יהיה לי נטו אם אקח BYD?")
    asks = [m for m in outcome.messages if m.kind == "confirmation_request"]
    assert len(asks) == 1 and asks[0].payload["fields"] == list(SALARY_FIELDS)
    assert "compare_scenarios" not in port.tools()
    assert "18500" not in text_of(outcome) and "18,500" not in text_of(outcome)  # names only
    assert_employee_safe(outcome)


async def test_yes_resumes_the_calculation_and_the_question_is_never_repeated() -> None:
    runner, port, llm = make()
    await saved_salary(port)
    llm.interpret(
        interpretation(Intent.CALCULATE_OPTIONS, all_options=True),
        interpretation(Intent.CONFIRM_FACTS),
        interpretation(Intent.CALCULATE_OPTIONS, all_options=True),
    )
    await say(runner, "כמה יהיה לי נטו אם אקח BYD?")
    confirms_salary(port)
    outcome = await say(runner, "כן")
    assert "compare_scenarios" in port.tools()
    assert not any(m.kind == "confirmation_request" for m in outcome.messages)
    again = await say(runner, "ומה עם כל הרכבים?")
    assert not any(m.kind == "confirmation_request" for m in again.messages)
    assert len(port.requests("compare_scenarios")) == 2


async def test_salary_given_in_this_conversation_needs_no_confirmation() -> None:
    runner, port, llm = make()
    await saved_salary(port)
    accepts(port, "payroll.baseGross")
    llm.interpret(
        interpretation(
            Intent.CALCULATE_OPTIONS,
            all_options=True,
            facts=[fact(FactField.BASE_GROSS, "19000")],
        )
    )
    outcome = await say(runner, "השכר שלי עלה ל-19000, כמה יהיה לי נטו עם BYD?")
    assert not any(m.kind == "confirmation_request" for m in outcome.messages)
    assert "compare_scenarios" in port.tools()
    notes = [m for m in outcome.messages if m.kind == "info" and "fields" in m.payload]
    assert len(notes) == 1  # the other saved facts are named once, under the result
    assert "payroll.baseGross" not in notes[0].payload["fields"]


async def test_hypothetical_salary_counts_as_given_and_needs_no_confirmation() -> None:
    runner, port, llm = make()
    await saved_salary(port)
    accepts(port, "payroll.baseGross")
    llm.interpret(
        interpretation(
            Intent.CALCULATE_OPTIONS,
            all_options=True,
            facts=[fact(FactField.BASE_GROSS, "20000", FactMode.HYPOTHETICAL)],
        )
    )
    outcome = await say(runner, "מה יקרה אם המשכורת שלי תעלה ל-20000?")
    assert not any(m.kind == "confirmation_request" for m in outcome.messages)
    assert port.requests("compare_scenarios")[0].kind.value == "hypothetical"


async def test_an_unanswered_confirmation_is_not_asked_again() -> None:
    runner, port, llm = make()
    await saved_salary(port)
    llm.interpret(
        interpretation(Intent.CALCULATE_OPTIONS, all_options=True),
        interpretation(Intent.CALCULATE_OPTIONS, all_options=True),
        interpretation(Intent.CALCULATE_OPTIONS, all_options=True),
    )
    await say(runner, "כמה יהיה לי נטו אם אקח BYD?")
    second = await say(runner, "פשוט תחשב בבקשה")
    assert not any(m.kind == "confirmation_request" for m in second.messages)
    assert "compare_scenarios" in port.tools()
    noted = [m for m in second.messages if m.kind == "info" and "fields" in m.payload]
    assert len(noted) == 1  # transparent about the saved facts used
    third = await say(runner, "ועוד פעם")
    assert not any("fields" in m.payload and m.kind == "info" for m in third.messages)


async def test_comparison_asks_salary_and_current_arrangement_in_one_message() -> None:
    runner, port, llm = make()
    await saved_salary(port)
    needs_confirmation(port)
    llm.interpret(interpretation(Intent.COMPARE_TO_CURRENT, all_options=True))
    outcome = await say(runner, "אם אחליף מהקורולה ל-BYD כמה ישתנה לי הנטו?")
    asks = [m for m in outcome.messages if m.kind == "confirmation_request"]
    assert len(asks) == 1
    assert asks[0].payload["fields"] == [*SALARY_FIELDS, PARTICIPATION]
    assert "compare_scenarios" not in port.tools()


async def test_a_correction_answers_the_confirmation_and_calculates_right_away() -> None:
    runner, port, llm = make()
    await saved_salary(port)
    llm.interpret(
        interpretation(Intent.CALCULATE_OPTIONS, all_options=True),
        interpretation(Intent.UPDATE_FACTS, facts=[fact(FactField.BASE_GROSS, "19000")]),
    )
    await say(runner, "כמה יהיה לי נטו אם אקח BYD?")
    accepts(port, "payroll.baseGross")
    outcome = await say(runner, "השכר עלה ל-19000")
    assert "compare_scenarios" in port.tools()
    assert not any(m.kind == "confirmation_request" for m in outcome.messages)


async def test_listing_vehicles_never_asks_to_confirm_salary() -> None:
    runner, port, llm = make()
    await saved_salary(port)
    llm.interpret(interpretation(Intent.LIST_OPTIONS))
    outcome = await say(runner, "אילו רכבים יש לי?")
    assert not any(m.kind == "confirmation_request" for m in outcome.messages)


async def test_yes_keeps_the_waiver_and_drive_filter_of_the_question() -> None:
    runner, port, llm = make()
    await saved_salary(port)
    llm.interpret(
        interpretation(
            Intent.CALCULATE_OPTIONS,
            all_options=True,
            include_waiver=True,
            propulsion=["electric"],
        ),
        interpretation(Intent.CONFIRM_FACTS),
    )
    await say(runner, "כמה אקבל אם אוותר על הרכב, או עם רכב חשמלי?")
    confirms_salary(port)
    await say(runner, "כן")
    readiness: ReadinessRequest = port.requests("check_calculation_readiness")[-1]
    assert readiness.selection is not None
    assert "waiver" in [c.value for c in readiness.selection.categories or []]
    assert readiness.selection.filters is not None
    assert [p.value for p in readiness.selection.filters.propulsion or []] == ["electric"]


# --- the current car mentioned in a message, and conditional salary statements -------------

VEHICLE = "currentArrangement.vehicle"
COROLLA_CROSS = {
    "taxKey": {
        "taxYear": "2026",
        "registrationYear": "2025",
        "vehicleTypeCode": "1",
        "manufacturerCode": "00413",
        "modelCode": "00296",
    },
    "manufacturerName": "טויוטה יפן",
    "commercialName": "COROLLA CROSS",
    "modelDescription": "ZVG12L-KHXGBW",
    "valueOfUse": "4070",
    "catalogueId": "CAR-09",
}
OLDER_COROLLA_CROSS = {
    **COROLLA_CROSS,
    "taxKey": {**COROLLA_CROSS["taxKey"], "registrationYear": "2023"},  # type: ignore[dict-item]
    "valueOfUse": "3900",
    "catalogueId": None,
}


def current_car_search(port: SwitchablePort, status: str, *candidates: dict[str, Any]) -> None:
    port.override(
        "resolve_vehicle",
        ResolveVehicleResult,
        {"status": "ok", "data": {"status": status, "candidates": list(candidates)}},
    )


def confirms_vehicle(port: SwitchablePort) -> None:
    port.override(
        "confirm_profile_fields",
        ConfirmProfileFieldsResult,
        {
            "status": "ok",
            "data": {"confirmedFields": [VEHICLE], "newCaseVersion": 4, "newProfileVersion": 6},
        },
    )


async def test_the_saved_current_car_is_not_searched_again_and_counts_as_stated() -> None:
    runner, port, llm = make()
    current_car_search(port, "ambiguous", COROLLA_CROSS, OLDER_COROLLA_CROSS)
    confirms_vehicle(port)
    llm.interpret(
        interpretation(
            Intent.COMPARE_TO_CURRENT,
            option_refs=[OptionReference(kind="catalogue_id", value="CAR-10")],
            facts=[fact(FactField.CURRENT_VEHICLE, "COROLLA CROSS", quote="מהקורולה קרוס")],
        )
    )
    outcome = await say(runner, "אם אחליף מהקורולה קרוס ל-ATTO 3, כמה ישתנה לי הנטו?")
    assert "update_conversation_inputs" not in port.tools()  # same car: nothing is written
    confirm: ConfirmProfileFieldsRequest = port.requests("confirm_profile_fields")[0]
    assert [f.value for f in confirm.fields] == [VEHICLE]
    assert not any(m.kind == "confirmation" for m in outcome.messages)  # silent
    assert "לאיזה מהם" not in text_of(outcome)  # never "which year?" for the saved car
    assert "compare_scenarios" in port.tools()


async def test_an_unidentified_current_car_does_not_block_the_calculation() -> None:
    runner, port, llm = make()
    current_car_search(port, "not_found")
    llm.interpret(
        interpretation(
            Intent.COMPARE_TO_CURRENT,
            option_refs=[OptionReference(kind="catalogue_id", value="CAR-10")],
            facts=[fact(FactField.CURRENT_VEHICLE, "ZZZ 9", quote="מהזזז 9")],
        )
    )
    outcome = await say(runner, "אם אחליף מהזזז 9 ל-ATTO 3, כמה ישתנה לי הנטו?")
    compare: CompareRequest = port.requests("compare_scenarios")[0]
    assert compare.include_current is False  # never a delta against the old saved car
    assert any(m.kind == "comparison" for m in outcome.messages)
    assert outcome.messages[-1].kind == "question"
    assert outcome.messages[-1].payload["fields"] == [VEHICLE]
    state = await runner.conversation_state("thread-1")
    assert VEHICLE in state["awaiting_confirmation"]
    assert state["active_goal"]["includeCurrent"] is True  # kept for the answer


async def test_answering_which_car_resumes_the_comparison_with_today() -> None:
    runner, port, llm = make()
    current_car_search(port, "not_found")
    llm.interpret(
        interpretation(
            Intent.COMPARE_TO_CURRENT,
            option_refs=[OptionReference(kind="catalogue_id", value="CAR-10")],
            facts=[fact(FactField.CURRENT_VEHICLE, "ZZZ 9", quote="מהזזז 9")],
        ),
        interpretation(
            Intent.UPDATE_FACTS,
            facts=[fact(FactField.CURRENT_VEHICLE, "COROLLA CROSS 2023", quote="קורולה קרוס 2023")],
        ),
    )
    await say(runner, "אם אחליף מהזזז 9 ל-ATTO 3, כמה ישתנה לי הנטו?")
    current_car_search(port, "resolved", OLDER_COROLLA_CROSS)
    accepts(port, VEHICLE)
    await say(runner, "קורולה קרוס 2023")
    compare: CompareRequest = port.requests("compare_scenarios")[-1]
    assert compare.include_current is True


async def test_a_salary_in_a_conditional_question_is_calculated_but_never_saved() -> None:
    runner, port, llm = make()
    accepts(port, "payroll.baseGross")
    llm.interpret(
        interpretation(
            Intent.CALCULATE_OPTIONS,
            all_options=True,
            # The model wrongly marks it actual; the sentence is a condition.
            facts=[fact(FactField.BASE_GROSS, "20000", quote="השכר שלי השתנה ל20000")],
        )
    )
    outcome = await say(runner, "ואם השכר שלי השתנה ל20000?")
    update: UpdateInputsRequest = port.requests("update_conversation_inputs")[0]
    assert update.mode is UpdateMode.HYPOTHETICAL and update.expected_profile_version is None
    assert port.requests("compare_scenarios")[0].kind.value == "hypothetical"
    assert "מה אם" not in text_of(outcome)  # plain words, no "what-if" jargon
    assert outcome.messages[-1].kind == "info"
    assert outcome.messages[-1].payload["fields"] == ["payroll.baseGross"]
    assert "לא עדכנתי את" in outcome.messages[-1].text


async def test_a_plain_salary_update_before_a_conditional_car_question_is_saved() -> None:
    runner, port, llm = make()
    accepts(port, "payroll.baseGross")
    llm.interpret(
        interpretation(
            Intent.CALCULATE_OPTIONS,
            all_options=True,
            facts=[fact(FactField.BASE_GROSS, "20000", quote="השכר שלי עלה ל-20000")],
        )
    )
    outcome = await say(runner, "השכר שלי עלה ל-20000. אם אקח ATTO 3, כמה יהיה לי נטו?")
    update: UpdateInputsRequest = port.requests("update_conversation_inputs")[0]
    assert update.mode is UpdateMode.ACTUAL_UPDATE
    assert "לא עדכנתי את" not in text_of(outcome)
