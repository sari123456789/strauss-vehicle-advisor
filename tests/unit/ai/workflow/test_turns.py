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
from app.ai.workflow.models import (
    FactField,
    FactMode,
    Intent,
    OptionReference,
    ProposedFact,
)
from app.ai.workflow.runner import LangGraphTurnRunner
from app.contracts.case_inputs import UpdateInputsRequest
from app.contracts.common import UpdateMode
from app.contracts.comparisons import CompareRequest
from app.contracts.readiness import ReadinessRequest, SelectionMode
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

    def use(self, scenario: str) -> None:
        self.fake = FakeBusinessPort(scenario)

    def __getattr__(self, name: str) -> Any:
        async def call(ctx: ToolContext, *args: Any) -> Any:
            self.calls.append((name, args[0] if args else None))
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
    assert "אינה עדכנית" in text_of(outcome)


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
    assert "עומס זמני" in text_of(outcome)


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
