"""Stage B2: a complete employee conversation that drives the real Business comparison.

E-001's regular replacement (COROLLA CROSS -> ATTO 3) runs as real LangGraph turns with a
scripted model over the real `BusinessToolsFacade`, on an isolated copy of E-001's profile
(the canonical seeded profiles are never updated or confirmed). Every amount the employee sees
must equal a display value returned by the Business module.
"""

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID, uuid4

from app.ai.application.conversation_service import ConversationView
from app.ai.application.ports.graph_runner import InputKind, TurnInput, TurnOutcome, TurnStatus
from app.ai.workflow import messages_he as he
from app.ai.workflow.models import Intent, OptionReference, TurnInterpretation
from app.contracts.common import EnvelopeStatus, ErrorCode, IssueCode, RunKind
from app.contracts.comparisons import ComparisonDetailsRequest, ComparisonResult, ScenarioStatus
from app.contracts.readiness import SelectionMode
from app.contracts.tool_context import ActorRef
from tests.fakes.ai.fake_llm import interpretation
from tests.integration.ai_business.conftest import Stack, reference_subject, trace
from tests.integration.ai_business.db_guard import snapshot, sql

MONTH = "2026-10"
ATTO_3 = "vehicle:CAR-10"
SPEC_RAW = ("11031.819", "11692.817", "660.998")
"""Agent Spec §8 for E-001: exact current net, alternative net and difference."""
AMOUNT = re.compile(r"[+-]?\d{1,3}(?:,\d{3})+|[+-]?\d{4,}")
RAW_CODES = re.compile(r"\b(40[0-9]|500|503)\b|NOT_FOUND|FORBIDDEN|VERSION_CONFLICT|trace", re.I)


async def say(
    stack: Stack,
    actor: ActorRef,
    view: ConversationView,
    meaning: TurnInterpretation,
    text: str,
    *,
    client_message_id: UUID | None = None,
) -> tuple[TurnOutcome, list[str]]:
    """One real turn; returns the outcome and the Business tools it invoked, in order."""
    submitted = await stack.conversations.submit_user_message(
        actor, view.conversation_id, client_message_id=client_message_id or uuid4(), text=text
    )
    binding = await stack.conversations.turn_binding(actor, view.conversation_id, trace_id=trace())
    if not submitted.duplicate:
        stack.llm.interpret(meaning)
    before = len(stack.spy.calls)
    outcome = await stack.runner.run_turn(
        thread_id=binding.thread_id,
        tool_context=binding.tool_context,
        turn=TurnInput(submitted.input_id, InputKind.USER_MESSAGE, text=text),
    )
    if outcome.messages:
        await stack.conversations.record_assistant_messages(
            view.conversation_id, submitted.input_id, outcome.messages
        )
    return outcome, stack.spy.names()[before:]


def text_of(outcome: TurnOutcome) -> str:
    return "\n".join(m.text for m in outcome.messages)


def shown_amounts(outcome: TurnOutcome) -> set[str]:
    """Numbers shown to the employee, excluding the fixed disclaimer (it names table years)."""
    text = text_of(outcome).replace(he.DISCLAIMER, "")
    return {a.replace(",", "").lstrip("+") for a in AMOUNT.findall(text)}


def business_displays(results: list[Any]) -> set[str]:
    """Every whole-shekel value the Business module returned in comparison results."""
    values: set[str] = set()
    for envelope in results:
        result: ComparisonResult | None = envelope.data
        if result is None:
            continue
        if result.current is not None and result.current.net is not None:
            values.add(result.current.net.display.lstrip("+-"))
        for scenario in result.alternatives:
            for amount in (scenario.net, scenario.delta):
                if amount is not None:
                    values.add(amount.display.lstrip("+-"))
    return values


def canonical_profiles(url: str) -> list[tuple[Any, ...]]:
    """Version and content digest of the six seeded reference profiles."""
    subjects = [reference_subject(f"E-00{n}") for n in range(1, 7)]
    return sql(
        url,
        "SELECT employee_id, version, md5(fields::text) FROM business.employee_profiles "
        "WHERE employee_id = ANY(%s) ORDER BY employee_id",
        subjects,
    )


def canonical_fields(url: str, employee_ref: str) -> Any:
    return sql(
        url,
        "SELECT fields -> 'currentArrangement' FROM business.employee_profiles "
        "WHERE employee_id = %s",
        reference_subject(employee_ref),
    )[0][0]


def own_profile(url: str, subject: UUID) -> tuple[int, Any]:
    """The clone's own stored profile row: version and current arrangement."""
    [(version, current)] = sql(
        url,
        "SELECT version, fields -> 'currentArrangement' FROM business.employee_profiles "
        "WHERE employee_id = %s",
        subject,
    )
    return int(version), current


def three(raw: str) -> Decimal:
    return Decimal(raw).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


async def test_b2_full_conversation_compares_through_the_real_business(stack: Stack) -> None:
    canonical_before = canonical_profiles(stack.url)
    actor = await stack.clone_reference_employee("E-001")
    assert actor.subject_id != reference_subject("E-001")
    assert own_profile(stack.url, actor.subject_id) == (1, canonical_fields(stack.url, "E-001"))
    view = await stack.conversations.start_conversation(
        actor, client_request_id=uuid4(), calculation_month=MONTH
    )
    business_before = snapshot(stack.url, ("auth",))
    transcript: list[tuple[str, str]] = []
    """Every accepted user message and every user-visible assistant response, in order."""

    async def turn(
        meaning: TurnInterpretation, text: str, **kw: Any
    ) -> tuple[TurnOutcome, list[str]]:
        outcome, tools = await say(stack, actor, view, meaning, text, **kw)
        if outcome.status is TurnStatus.COMPLETED:
            transcript.append(("user", text))
            transcript.extend(("assistant", m.text) for m in outcome.messages)
        return outcome, tools

    # Turn 1: the real catalogue.
    listed, tools1 = await turn(interpretation(Intent.LIST_OPTIONS), "אילו רכבים מתאימים לי?")
    assert tools1 == ["get_conversation_context", "list_eligible_options"]
    assert "ATTO 3" in text_of(listed)

    # Turn 2: compare ATTO 3 to today, before the saved baseline is confirmed.
    ask = interpretation(
        Intent.COMPARE_TO_CURRENT,
        option_refs=[OptionReference(kind="catalogue_id", value="CAR-10")],
    )
    before, tools2 = await turn(ask, "כמה אקבל עם ATTO 3 לעומת היום?")
    first_request = stack.spy.requests("check_calculation_readiness")[-1]
    assert first_request.selection.mode is SelectionMode.EXPLICIT
    assert first_request.selection.option_keys == [ATTO_3]
    assert tools2 == [
        "get_conversation_context",
        "check_calculation_readiness",
        "compare_scenarios",
    ]
    readiness = stack.spy.results("check_calculation_readiness")[-1]
    reported = sorted(
        i.field.value
        for i in readiness.issues
        if i.code is IssueCode.AMBIGUOUS_INPUT
        and i.reason == "confirmation_required"
        and i.field is not None
    )
    assert reported, "the real readiness reports the unconfirmed current arrangement"
    first_compare = stack.spy.results("compare_scenarios")[-1].data
    assert first_compare is not None and first_compare.current is None  # no baseline requested
    [alternative] = first_compare.alternatives
    assert alternative.status is ScenarioStatus.CALCULATED and alternative.delta is None
    text = text_of(before)
    [summary] = [m.text for m in before.messages if m.kind == "comparison"]
    assert "הנטו המשוער היום" not in summary and "(הפרש" not in summary  # nothing invented
    assert "11,693 ₪" in summary  # the alternative's Business value is shown
    assert "11,032" not in text and "661" not in text
    asks = [m for m in before.messages if m.kind == "confirmation_request"]
    assert len(asks) == 1 and sorted(asks[0].payload["fields"]) == reported
    assert shown_amounts(before) <= business_displays(stack.spy.results("compare_scenarios"))

    # Turn 3: the employee explicitly confirms; the goal resumes with the real comparison.
    confirm_id = uuid4()
    after, tools3 = await turn(
        interpretation(Intent.CONFIRM_FACTS),
        "כן, הפרטים האלה עדיין נכונים",
        client_message_id=confirm_id,
    )
    assert tools3 == [
        "get_conversation_context",
        "confirm_profile_fields",
        "check_calculation_readiness",
        "compare_scenarios",
    ]
    confirmed = stack.spy.results("confirm_profile_fields")[-1]
    assert confirmed.status is EnvelopeStatus.OK and confirmed.data is not None
    assert sorted(f.value for f in confirmed.data.confirmed_fields) == reported
    assert sorted(f.value for f in stack.spy.requests("confirm_profile_fields")[-1].fields) == (
        reported
    )
    # The resumed goal is exactly the requested option against the current arrangement.
    resumed = stack.spy.requests("check_calculation_readiness")[-1]
    assert resumed.selection.mode is SelectionMode.EXPLICIT
    assert resumed.selection.option_keys == [ATTO_3] and resumed.include_current is True
    request = stack.spy.requests("compare_scenarios")[-1]
    assert request.selection.mode is SelectionMode.EXPLICIT
    assert request.selection.option_keys == [ATTO_3]
    assert request.include_current is True and request.kind is RunKind.REGULAR
    assert request.expected_case_version == confirmed.data.new_case_version
    assert request.expected_profile_version == confirmed.data.new_profile_version
    compared = stack.spy.results("compare_scenarios")[-1].data
    assert compared is not None and compared.kind is RunKind.REGULAR
    assert compared.current is not None and compared.current.net is not None
    assert [a.option_key for a in compared.alternatives] == [ATTO_3]
    atto = compared.alternatives[0]
    assert atto.net is not None and atto.delta is not None
    [result_text] = [m.text for m in after.messages if m.kind == "comparison"]
    # one alternative shown (as a sentence), not the whole catalogue
    assert result_text.count("הנטו המשוער שלך יוצא") == 1 and "•" not in result_text
    current_raw, alternative_raw, delta_raw = SPEC_RAW
    assert three(compared.current.net.raw) == Decimal(current_raw)
    assert three(atto.net.raw) == Decimal(alternative_raw)
    assert three(atto.delta.raw) == Decimal(delta_raw)
    text = text_of(after)
    assert "היום הנטו המשוער שלך הוא בערך 11,032 ₪ בחודש." in text
    assert "יוצא בערך 11,693 ₪ בחודש (+661 ₪ לעומת היום)." in text
    assert shown_amounts(after) <= business_displays(stack.spy.results("compare_scenarios"))
    assert not RAW_CODES.search(text)
    run_id = compared.run_id

    # Persistence of the immutable Business run.
    assert sql(stack.url, "SELECT id FROM business.comparison_runs WHERE id = %s", run_id) == [
        (run_id,)
    ]
    assert snapshot(stack.url, ("auth",)) == business_before  # no accounts were changed

    # Replay of the confirmation input: no Business call, no second run.
    runs = sql(stack.url, "SELECT count(*) FROM business.comparison_runs")[0][0]
    calls = len(stack.spy.calls)
    replay, tools_replay = await turn(
        interpretation(Intent.CONFIRM_FACTS),
        "כן, הפרטים האלה עדיין נכונים",
        client_message_id=confirm_id,
    )
    assert replay.status is TurnStatus.DUPLICATE and tools_replay == []
    assert len(stack.spy.calls) == calls
    assert sql(stack.url, "SELECT count(*) FROM business.comparison_runs")[0][0] == runs

    # Turn 4: history shows the stored run without recalculating.
    history, tools4 = await turn(interpretation(Intent.SHOW_HISTORY), "תראה לי שוב את ההשוואה")
    assert tools4 == ["get_conversation_context", "get_comparison_details"]
    details = stack.spy.results("get_comparison_details")[-1].data
    assert details is not None and details.result == compared  # immutable
    assert "11,032 ₪" in text_of(history) and "+661 ₪" in text_of(history)

    # Conversation history: all four accepted user messages and every visible response, in
    # order, retrievable through the AI application service (the replay added nothing).
    saved = await stack.conversations.history(actor, view.conversation_id)
    assert [(m.role, m.text) for m in saved] == transcript
    assert [m.sequence for m in saved] == sorted(m.sequence for m in saved)
    assert sum(1 for role, _ in transcript if role == "user") == 4
    assert {"options", "comparison", "confirmation_request", "confirmation", "history"} <= {
        m.kind for m in saved if m.role == "assistant"
    }

    # Ownership of the data: the clone changed, the canonical reference profiles did not.
    version, _ = own_profile(stack.url, actor.subject_id)
    assert version > 1  # the confirmation was recorded on the clone's own profile
    assert canonical_profiles(stack.url) == canonical_before

    # Ownership: another employee cannot read the run, even with a forged context.
    other = await stack.clone_reference_employee("E-002")
    binding = await stack.conversations.turn_binding(actor, view.conversation_id)
    forged = binding.tool_context.model_copy(update={"actor": other})
    denied = await stack.business.tools.get_comparison_details(
        forged, ComparisonDetailsRequest(run_id=run_id)
    )
    assert denied.status is EnvelopeStatus.ERROR and denied.data is None
    assert denied.error is not None and denied.error.code is ErrorCode.NOT_FOUND

    print(  # reported in the Stage B2 summary
        "B2 tools",
        tools1,
        tools2,
        tools3,
        tools4,
        "\nB2 confirm",
        reported,
        "\nB2 values",
        compared.current.net.model_dump(),
        atto.net.model_dump(),
        atto.delta.model_dump(),
        "\nB2 text turn2:\n" + text_of(before),
        "\nB2 text turn3:\n" + text_of(after),
    )
