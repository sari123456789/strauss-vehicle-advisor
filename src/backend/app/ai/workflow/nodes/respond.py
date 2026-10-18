"""Response composition and turn finalization.

The deterministic summary is always produced first. The model may add a short explanation, but
only through placeholders; if its text is not grounded, or the model is unavailable, the
explanation is simply omitted (or replaced by a fixed fallback for general questions).
"""

from typing import Any

from langgraph.runtime import Runtime

from app.ai.application.ports.llm import LlmMessage, LlmTask, LlmTaskKind
from app.ai.application.ports.response_staging import StagedMessage
from app.ai.workflow.models import ComposedText
from app.ai.workflow.prompts import COMPOSE_SYSTEM, GENERAL_SYSTEM, as_data
from app.ai.workflow.rendering import render_grounded
from app.ai.workflow.state import (
    MAX_PROCESSED_INPUTS,
    ConversationState,
    LastOutbox,
    OutboxRef,
    TurnRuntime,
    stable_id,
)


async def compose(state: ConversationState, runtime: Runtime[TurnRuntime]) -> dict[str, Any]:
    rt = runtime.context
    request = rt.compose
    if request is None:
        return {}
    text: str | None = None
    if rt.compose_with_llm:
        if request.kind == "comparison":
            system = COMPOSE_SYSTEM
            data = {"question": request.question, "placeholders": request.descriptions}
        else:
            system = GENERAL_SYSTEM
            data = {"question": request.question}
        result = await rt.call_llm(
            LlmTask(
                kind=LlmTaskKind.COMPOSE,
                system_instruction=system,
                messages=(LlmMessage("user", as_data(data)),),
                output_type=ComposedText,
                max_output_tokens=400,
            )
        )
        if result.ok and result.value is not None:
            text = render_grounded(result.value.text, request.values)
    if text is not None:
        rt.reply(text, "explanation")
    elif request.fallback is not None:
        rt.reply(request.fallback, "info")
    return {}


async def finalize(state: ConversationState, runtime: Runtime[TurnRuntime]) -> dict[str, Any]:
    rt = runtime.context
    turn = state.get("turn")
    assert turn is not None
    input_id = turn["inputId"]
    staged = [
        StagedMessage(
            message_id=str(stable_id(input_id, "assistant", index)),
            kind=reply.kind,
            text=reply.text,
            payload=dict(reply.payload),
        )
        for index, reply in enumerate(rt.replies)
    ]
    if rt.stager is not None:  # durable before the checkpoint is written (exit durability)
        await rt.stager.stage(input_id, staged)
    rt.staged = staged
    refs = [OutboxRef(messageId=m.message_id, contentSha256=m.ref.content_sha256) for m in staged]
    processed = [*state.get("processed_input_ids", []), input_id][-MAX_PROCESSED_INPUTS:]
    return {
        "processed_input_ids": processed,
        "last_outbox": LastOutbox(inputId=input_id, messages=refs),
        "turn": None,
        "route": "done",
    }
