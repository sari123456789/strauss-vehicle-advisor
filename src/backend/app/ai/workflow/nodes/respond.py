"""Response composition and turn finalization.

The deterministic summary is always produced first. The model may add a short explanation, but
only through placeholders; if its text is not grounded, or the model is unavailable, the
explanation is simply omitted (or replaced by a fixed fallback for general questions).
"""

import logging
import re
from typing import Any

from langgraph.runtime import Runtime

from app.ai.application.ports.llm import LlmMessage, LlmTask, LlmTaskKind
from app.ai.application.ports.response_staging import StagedMessage
from app.ai.workflow import messages_he as he
from app.contracts.catalogue import ListOptionsRequest
from app.contracts.common import OptionCategory
from app.ai.workflow.models import ComposedText, VoicedReplies
from app.ai.workflow.prompts import COMPOSE_SYSTEM, GENERAL_SYSTEM, VOICE_SYSTEM, as_data
from app.ai.workflow.rendering import render_grounded, render_voiced, to_draft
from app.ai.workflow.state import (
    MAX_PROCESSED_INPUTS,
    ConversationState,
    LastOutbox,
    OutboxRef,
    TurnRuntime,
    stable_id,
)


def _readable(text: str) -> bool:
    """Hebrew or Latin letters only: a stray token in another script (seen live) is dropped."""
    return all(not ch.isalpha() or "\u0590" <= ch <= "\u05ff" or ch.isascii() for ch in text)


logger = logging.getLogger(__name__)

_CATALOGUE_ID = re.compile(r"CAR-\d+")
_RECENT_FOR_VOICE = 6
_DIGITS_IN_RECENT = re.compile(r"\d")


async def compose(state: ConversationState, runtime: Runtime[TurnRuntime]) -> dict[str, Any]:
    rt = runtime.context
    request = rt.compose
    if request is not None and request.kind == "general":
        # A general question: the model writes the answer itself (unchanged behaviour).
        await _compose_general(rt, request)
        return {}
    if rt.compose_with_llm and rt.replies and await _voice(state, rt, request):
        return {}
    if request is not None:
        await _compose_general(rt, request)  # the voice was unavailable: old explanation path
    return {}


async def _voice(state: ConversationState, rt: TurnRuntime, request: Any) -> bool:
    """Rewrite this turn's template replies in the advisor's tone, in one model call.

    The code decided what to say and every number; the model only words it. A reply whose
    rewrite is not safe (invented digit, missing amount, unknown placeholder) keeps its template.
    Returns False when the model was not used at all, so the caller can fall back."""
    logger.info("voice start replies=%d chars=%d", len(rt.replies), sum(len(r.text) for r in rt.replies))
    drafts = [to_draft(reply.text) for reply in rt.replies]
    turn = state.get("turn") or {}
    recent = [
        {
            "role": "assistant" if m.get("role") == "assistant" else "employee",
            "text": _DIGITS_IN_RECENT.sub("#", m.get("text", ""))[:400],
        }
        for m in (turn.get("recentMessages") or [])[-_RECENT_FOR_VOICE:]
    ]
    data: dict[str, Any] = {
        "employee_message": str(turn.get("text") or ""),
        "RECENT": recent,
        "drafts": [draft for draft, _values, _required in drafts],
    }
    if request is not None and request.kind == "comparison":
        data["may_add_one_short_reason"] = (
            "After a result you may add one short sentence on what mainly drives the difference "
            "(the car's value of use raises the tax; the monthly participation compared with "
            "today), inside the result's text, never as a separate text."
        )
    result = await rt.call_llm(
        LlmTask(
            kind=LlmTaskKind.COMPOSE,
            system_instruction=VOICE_SYSTEM,
            messages=(LlmMessage("user", as_data(data)),),
            output_type=VoicedReplies,
            max_output_tokens=1500,
        )
    )
    if not result.ok or result.value is None or len(result.value.texts) != len(rt.replies):
        logger.info(
            "voice unavailable outcome=%s texts=%s replies=%d",
            result.outcome.value,
            None if result.value is None else len(result.value.texts),
            len(rt.replies),
        )
        return False
    kept: list[str] = []
    for reply, (draft, values, required), text in zip(
        rt.replies, drafts, result.value.texts, strict=True
    ):
        voiced = render_voiced(text, values, required, draft) if _readable(text) else None
        if voiced and "תלוש" in reply.text and "תלוש" not in voiced:
            # The estimate disclaimer is a requirement, not a style choice: keep it.
            voiced = f"{voiced}\n{he.SHORT_DISCLAIMER}"
        if voiced:
            reply.text = voiced
        else:
            kept.append(reply.kind)
    logger.info("voice applied=%d kept=%s", len(rt.replies) - len(kept), kept)
    return True


async def _vehicle_name(rt: TurnRuntime, values: dict[str, str]) -> None:
    """Replace a bare catalogue id ("CAR-09") of the current car with its catalogue name."""
    current = values.get("fact:currentVehicle", "")
    if not _CATALOGUE_ID.fullmatch(current):
        return
    envelope = await rt.tools.list_eligible_options(
        ListOptionsRequest(
            categories=[OptionCategory.REGULAR, OptionCategory.FAMILY],
            expected_case_version=rt.case_version,
        )
    )
    for item in envelope.data.items if envelope.data is not None else []:
        if item.catalogue_id == current:
            values["fact:currentVehicle"] = he.option_label(item)
            return


async def _compose_general(rt: TurnRuntime, request: Any) -> None:
    text: str | None = None
    if request.kind == "general" and request.values:
        try:
            await _vehicle_name(rt, request.values)
        except Exception:  # the name is a nicety; the id still answers the question
            logger.info("vehicle name lookup failed")
    if rt.compose_with_llm:
        data: dict[str, Any]
        if request.kind == "comparison":
            system = COMPOSE_SYSTEM
            data = {"question": request.question, "placeholders": request.descriptions}
        else:
            system = GENERAL_SYSTEM
            data = {
                "question": request.question,
                "RECENT": request.recent,
                "SAVED": request.descriptions,
            }
        result = await rt.call_llm(
            LlmTask(
                kind=LlmTaskKind.COMPOSE,
                system_instruction=system,
                messages=(LlmMessage("user", as_data(data)),),
                output_type=ComposedText,
                max_output_tokens=400,
            )
        )
        if result.ok and result.value is not None and _readable(result.value.text):
            text = render_grounded(result.value.text, request.values)
    if text is not None:
        rt.reply(text, "explanation")
    elif request.fallback is not None:
        rt.reply(request.fallback, "info")


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
