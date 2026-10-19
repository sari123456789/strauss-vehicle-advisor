"""Stage A: real Business case lifecycle and context through the AI conversation workflow."""

import re
from uuid import UUID, uuid4

import pytest

from app.ai.application import errors
from app.ai.application.conversation_service import ConversationView
from app.ai.application.ports.graph_runner import InputKind, TurnInput, TurnOutcome, TurnStatus
from app.ai.workflow.models import Intent
from app.contracts.common import EnvelopeStatus, ErrorCode, FieldPath
from app.contracts.tool_context import ActorRef, Role, ToolContext
from tests.fakes.ai.fake_llm import interpretation
from tests.integration.ai_business.conftest import Stack, trace
from tests.integration.ai_business.db_guard import snapshot, sql

MONTH = "2026-10"
RAW_CODES = re.compile(r"\b(40[0-9]|500|503)\b|NOT_FOUND|FORBIDDEN|trace", re.I)


async def start(stack: Stack) -> tuple[ActorRef, ConversationView]:
    actor = await stack.new_actor()
    view = await stack.conversations.start_conversation(
        actor, client_request_id=uuid4(), calculation_month=MONTH
    )
    return actor, view


async def turn(
    stack: Stack,
    actor: ActorRef,
    view: ConversationView,
    intent: Intent,
    text: str,
    *,
    client_message_id: UUID | None = None,
) -> tuple[TurnOutcome, ToolContext, UUID]:
    """One real user turn: durable input, server-built binding, LangGraph run, history."""
    submitted = await stack.conversations.submit_user_message(
        actor, view.conversation_id, client_message_id=client_message_id or uuid4(), text=text
    )
    binding = await stack.conversations.turn_binding(actor, view.conversation_id, trace_id=trace())
    if not submitted.duplicate:
        stack.llm.interpret(interpretation(intent))
    outcome = await stack.runner.run_turn(
        thread_id=binding.thread_id,
        tool_context=binding.tool_context,
        turn=TurnInput(submitted.input_id, InputKind.USER_MESSAGE, text=text),
    )
    if outcome.messages:
        await stack.conversations.record_assistant_messages(
            view.conversation_id, submitted.input_id, outcome.messages
        )
    return outcome, binding.tool_context, submitted.input_id


def text_of(outcome: TurnOutcome) -> str:
    return "\n".join(m.text for m in outcome.messages)


# --- Case lifecycle ---------------------------------------------------------------------------


async def test_case_is_created_through_the_real_lifecycle_port(stack: Stack) -> None:
    actor = await stack.new_actor()
    request_id = uuid4()
    view = await stack.conversations.start_conversation(
        actor, client_request_id=request_id, calculation_month=MONTH
    )
    assert view.is_active and view.case_id is not None
    rows = sql(
        stack.url,
        "SELECT id FROM business.cases WHERE conversation_id = %s",
        view.conversation_id,
    )
    assert rows == [(view.case_id,)]
    again = await stack.conversations.start_conversation(
        actor, client_request_id=request_id, calculation_month=MONTH
    )
    assert (again.conversation_id, again.case_id) == (view.conversation_id, view.case_id)
    assert (
        sql(
            stack.url,
            "SELECT id FROM business.cases WHERE conversation_id = %s",
            view.conversation_id,
        )
        == rows
    )  # the replayed request created no second case


async def test_the_real_facade_returns_the_saved_profile_and_echoes_the_trace(
    stack: Stack,
) -> None:
    actor, view = await start(stack)
    binding = await stack.conversations.turn_binding(actor, view.conversation_id, trace_id="it-1")
    envelope = await stack.business.tools.get_conversation_context(binding.tool_context)
    assert envelope.status is EnvelopeStatus.OK and envelope.data is not None
    assert envelope.trace_id == "it-1"
    context = envelope.data
    assert (context.case_id, context.conversation_id) == (view.case_id, view.conversation_id)
    assert context.profile.base_gross.value == "18500.00"
    assert context.salary_profile_version_used == context.latest_profile_version == 1
    assert context.fields_requiring_confirmation  # saved but not yet confirmed in this case
    assert set(context.fields_requiring_confirmation) <= set(FieldPath)


# --- LangGraph turns through the real facade --------------------------------------------------


async def test_a_langgraph_turn_reads_context_through_the_real_facade(stack: Stack) -> None:
    actor, view = await start(stack)
    outcome, ctx, _ = await turn(stack, actor, view, Intent.SMALLTALK, "שלום")
    assert outcome.status is TurnStatus.COMPLETED
    assert stack.spy.names() == ["get_conversation_context"]
    name, seen = stack.spy.calls[0]
    assert seen == ctx
    assert seen.actor == actor
    assert (seen.conversation_id, seen.case_id) == (view.conversation_id, view.case_id)
    assert outcome.messages and not RAW_CODES.search(text_of(outcome))


async def test_explain_turn_uses_the_real_case_context_and_calls_no_write_tool(
    stack: Stack,
) -> None:
    actor, view = await start(stack)
    outcome, _, _ = await turn(stack, actor, view, Intent.EXPLAIN_RESULT, "תסביר לי את החישוב")
    assert stack.spy.names() == ["get_conversation_context"]
    assert outcome.messages[-1].kind == "info"  # no runs yet in the real case
    assert "עדיין לא ביצענו חישוב" in text_of(outcome)


# --- Authorization ----------------------------------------------------------------------------


async def test_another_employee_cannot_read_the_case(stack: Stack) -> None:
    owner, view = await start(stack)
    other = await stack.new_actor()
    with pytest.raises(errors.ConversationAccessError):
        await stack.conversations.turn_binding(other, view.conversation_id)
    assert view.case_id is not None
    forged = ToolContext(
        actor=other, conversation_id=view.conversation_id, case_id=view.case_id, trace_id="it-2"
    )
    envelope = await stack.business.tools.get_conversation_context(forged)
    assert envelope.status is EnvelopeStatus.ERROR and envelope.data is None
    assert envelope.error is not None and envelope.error.code is ErrorCode.NOT_FOUND
    # Even with a forged context the workflow stops before any model call, in natural Hebrew.
    outcome = await stack.runner.run_turn(
        thread_id=str(uuid4()),
        tool_context=forged,
        turn=TurnInput(uuid4(), InputKind.USER_MESSAGE, text="תראה לי את התיק"),
    )
    assert stack.llm.tasks == []
    assert stack.spy.names() == ["get_conversation_context"]
    assert outcome.messages and not RAW_CODES.search(text_of(outcome))
    assert owner != other


async def test_a_representative_cannot_read_an_employee_case(stack: Stack) -> None:
    _, view = await start(stack)
    representative = await stack.new_actor(Role.REPRESENTATIVE, with_profile=False)
    assert view.case_id is not None
    envelope = await stack.business.tools.get_conversation_context(
        ToolContext(
            actor=representative,
            conversation_id=view.conversation_id,
            case_id=view.case_id,
            trace_id="it-3",
        )
    )
    assert envelope.status is EnvelopeStatus.ERROR and envelope.data is None
    assert envelope.error is not None and envelope.error.code is ErrorCode.FORBIDDEN


# --- Persistence boundary and replay ----------------------------------------------------------


async def test_a_read_only_turn_writes_only_to_agent(stack: Stack) -> None:
    actor, view = await start(stack)
    business_before = snapshot(stack.url, ("business", "auth"))
    messages_before = sql(
        stack.url,
        "SELECT count(*) FROM agent.messages WHERE conversation_id = %s",
        view.conversation_id,
    )[0][0]
    outcome, _, _ = await turn(stack, actor, view, Intent.SMALLTALK, "שלום")
    assert snapshot(stack.url, ("business", "auth")) == business_before
    messages_after = sql(
        stack.url,
        "SELECT count(*) FROM agent.messages WHERE conversation_id = %s",
        view.conversation_id,
    )[0][0]
    assert messages_after == messages_before + 1 + len(outcome.messages)  # user + assistant
    checkpoints = sql(
        stack.url,
        "SELECT count(*) FROM agent.checkpoints WHERE thread_id = %s",
        str(view.thread_id),
    )[0][0]
    assert checkpoints > 0


async def test_duplicate_input_does_not_repeat_business_calls(stack: Stack) -> None:
    actor, view = await start(stack)
    message_id = uuid4()
    first, _, input_id = await turn(
        stack, actor, view, Intent.SMALLTALK, "שלום", client_message_id=message_id
    )
    calls = len(stack.spy.calls)
    second, _, replayed_id = await turn(
        stack, actor, view, Intent.SMALLTALK, "שלום", client_message_id=message_id
    )
    assert replayed_id == input_id
    assert second.status is TurnStatus.DUPLICATE
    assert len(stack.spy.calls) == calls  # no Business call on replay
    assert [r.message_id for r in second.message_refs] == [
        str(m.message_id) for m in first.messages
    ]
