"""Input handling: duplicate detection, context loading, interpretation and validated updates."""

import math
from typing import Any
from uuid import UUID

from langgraph.runtime import Runtime

from app.ai.application.ports.llm import LlmMessage, LlmOutcome, LlmTask, LlmTaskKind
from app.ai.workflow import messages_he as he
from app.ai.workflow.extraction import (
    Clarification,
    ClarificationCode,
    normalize_facts,
)
from app.ai.workflow.models import FactMode, Intent, TurnInterpretation
from app.ai.workflow.prompts import INTERPRET_SYSTEM, as_data
from app.ai.workflow.state import (
    ConversationState,
    OccupationCandidateRef,
    TurnRuntime,
    stable_id,
)
from app.contracts.case_inputs import (
    ConfirmProfileFieldsRequest,
    SetChange,
    UpdateInputsRequest,
)
from app.contracts.cases import CaseContext
from app.contracts.catalogue import (
    ResolveOccupationRequest,
    ResolvePurpose,
    ResolveStatus,
    ResolveVehicleRequest,
    VehicleCandidate,
    VehicleQuery,
)
from app.contracts.common import EnvelopeStatus, ErrorCode, FieldPath, UpdateMode
from app.contracts.profile import VehicleRef

HISTORY_MESSAGES = 6
MAX_OCCUPATION_CHOICES = 10
_FIELD_PATHS = frozenset(path.value for path in FieldPath)

_PROFILE_ATTRS: dict[FieldPath, tuple[str, ...]] = {
    FieldPath.BASE_GROSS: ("base_gross",),
    FieldPath.COMMISSIONS_GROSS: ("commissions_gross",),
    FieldPath.BONUS_GROSS: ("bonus_gross",),
    FieldPath.PENSION_EMPLOYEE_RATE: ("pension_employee_rate",),
    FieldPath.STUDY_FUND_EMPLOYEE_RATE: ("study_fund_employee_rate",),
    FieldPath.CREDIT_POINTS: ("credit_points",),
    FieldPath.OCCUPATION_ID: ("occupation_id",),
    FieldPath.ENTITLEMENT_GROUP: ("entitlement_group",),
    FieldPath.QUALIFYING_CHILDREN_COUNT: ("qualifying_children_count",),
    FieldPath.CURRENT_KIND: ("current_arrangement", "kind"),
    FieldPath.CURRENT_VEHICLE: ("current_arrangement", "vehicle"),
    FieldPath.CURRENT_PROPULSION: ("current_arrangement", "propulsion"),
    FieldPath.CURRENT_PARTICIPATION: ("current_arrangement", "participation"),
    FieldPath.CURRENT_FAMILY_DEDUCTION: ("current_arrangement", "family_deduction"),
    FieldPath.CURRENT_FAMILY_DEDUCTION_INCLUDED: (
        "current_arrangement",
        "family_deduction_included_in_participation",
    ),
    FieldPath.CURRENT_ALLOWANCE: ("current_arrangement", "allowance"),
}


def profile_statuses(case: CaseContext) -> dict[str, str]:
    """Field statuses only (no values) - the model never receives salary amounts."""
    statuses: dict[str, str] = {}
    for path, attrs in _PROFILE_ATTRS.items():
        node: Any = case.profile
        for attr in attrs:
            node = getattr(node, attr)
        statuses[path.value] = node.status.value
    return statuses


async def ingest(state: ConversationState, runtime: Runtime[TurnRuntime]) -> dict[str, Any]:
    turn = state.get("turn")
    assert turn is not None
    if turn["inputId"] in state.get("processed_input_ids", []):
        return {"route": "duplicate", "turn": None}  # never checkpoint the turn's text
    return {"route": "continue"}


async def load_context(state: ConversationState, runtime: Runtime[TurnRuntime]) -> dict[str, Any]:
    """Always first: authorizes the conversation and revalidates the account every turn."""
    rt = runtime.context
    envelope = await rt.tools.get_conversation_context()
    if envelope.status is EnvelopeStatus.ERROR or envelope.data is None:
        code = envelope.error.code if envelope.error else None
        rt.reply(he.error_text(code), "error", code=code.value if code else None)
        rt.halt = True
        return {"route": "stop"}
    rt.case = envelope.data
    rt.case_version = envelope.data.case_version
    rt.profile_version = envelope.data.latest_profile_version
    turn = state.get("turn")
    assert turn is not None
    return {"route": "answer" if turn["kind"] == "representative_answered" else "continue"}


def _interpret_task(state: ConversationState, rt: TurnRuntime) -> LlmTask[TurnInterpretation]:
    turn = state.get("turn")
    assert turn is not None and rt.case is not None
    history = turn.get("recentMessages") or []
    messages = [
        LlmMessage("assistant" if m.get("role") == "assistant" else "user", m.get("text", ""))
        for m in history[-HISTORY_MESSAGES:]
    ]
    goal = state.get("active_goal")
    summary = {
        "fieldStatus": profile_statuses(rt.case),
        "displayedOptions": [
            f"{index}. {option['label']}"
            for index, option in enumerate(state.get("displayed_options", []), start=1)
        ],
        "activeGoal": goal["intent"] if goal else None,
        "awaitingConfirmation": list(state.get("awaiting_confirmation", [])),
        "offeredQuestion": state.get("offered_question"),
        "occupationChoices": [
            f"{index}. {c['label']}"
            for index, c in enumerate(state.get("occupation_candidates", []), start=1)
        ],
    }
    messages.append(
        LlmMessage("user", f"STATE={as_data(summary)}\nEMPLOYEE_MESSAGE={turn.get('text') or ''}")
    )
    return LlmTask(
        kind=LlmTaskKind.INTERPRET,
        system_instruction=INTERPRET_SYSTEM,
        messages=tuple(messages),
        output_type=TurnInterpretation,
        max_output_tokens=800,
    )


_LLM_FAILURE_TEXT = {
    LlmOutcome.RATE_LIMITED: he.LLM_RATE_LIMITED,
    LlmOutcome.QUOTA_EXHAUSTED: he.LLM_QUOTA_EXHAUSTED,
    LlmOutcome.BLOCKED_BY_POLICY: he.LLM_QUOTA_EXHAUSTED,
}


async def interpret(state: ConversationState, runtime: Runtime[TurnRuntime]) -> dict[str, Any]:
    rt = runtime.context
    task = _interpret_task(state, rt)
    result = await rt.call_llm(task)
    if result.outcome is LlmOutcome.INVALID_OUTPUT:
        result = await rt.call_llm(task)  # one retry; a second failure is reported, not guessed
    if not result.ok:
        rt.reply(
            _LLM_FAILURE_TEXT.get(result.outcome, he.LLM_UNAVAILABLE),
            "assistant_unavailable",
            outcome=result.outcome.value,
            # Whole seconds: staged payloads accept no floats (a float failed the whole turn).
            retryAfterSeconds=None
            if result.retry_after_seconds is None
            else math.ceil(result.retry_after_seconds),
        )
        rt.halt = True
        return {"route": "stop"}
    rt.interpretation = result.value
    return {"route": "continue"}


def _saved_vehicle(rt: TurnRuntime) -> VehicleRef | None:
    if rt.case is None:
        return None
    saved = rt.case.profile.current_arrangement.vehicle
    return saved.value if saved.status.value == "usable" else None


def _is_candidate(ref: VehicleRef, candidate: VehicleCandidate) -> bool:
    if ref.catalogue_id is not None:
        return ref.catalogue_id == candidate.catalogue_id
    return ref.tax_key == candidate.tax_key


async def _resolve_current_vehicle(rt: TurnRuntime, text: str) -> VehicleRef | str:
    """The identified current car, or the question to ask about it (asked after the results;
    an unidentified car never blocks the rest of the turn). When the description matches the
    car already saved in the profile, the saved car is used: it is never searched again."""
    year = next((word for word in text.split() if word.isdigit() and len(word) == 4), None)
    name = " ".join(word for word in text.split() if word != year)[:80] or text[:80]
    envelope = await rt.tools.resolve_vehicle(
        ResolveVehicleRequest(
            purpose=ResolvePurpose.CURRENT,
            query=VehicleQuery(commercial_name=name, registration_year=year),
        )
    )
    data = envelope.data
    unresolved = he.clarification_text(
        [Clarification(ClarificationCode.VEHICLE_UNRESOLVED, FieldPath.CURRENT_VEHICLE.value)]
    )
    if envelope.status is EnvelopeStatus.ERROR or data is None:
        return unresolved
    saved = _saved_vehicle(rt)
    if saved is not None and any(_is_candidate(saved, c) for c in data.candidates):
        return saved
    if data.status is ResolveStatus.RESOLVED and len(data.candidates) == 1:
        candidate = data.candidates[0]
        if candidate.catalogue_id:
            return VehicleRef(catalogue_id=candidate.catalogue_id)
        return VehicleRef(tax_key=candidate.tax_key)
    if data.status is ResolveStatus.AMBIGUOUS and data.candidates:
        names = [
            f"{c.manufacturer_name} {c.commercial_name} ({c.tax_key.registration_year})"
            for c in data.candidates[:5]
        ]
        return he.vehicle_candidates_text(names)
    return unresolved


async def _resolve_occupation(
    state: ConversationState, rt: TurnRuntime, text: str, updates: dict[str, Any]
) -> str | Clarification | None:
    """An official occupation ID, a clarification, or None when candidates were shown.

    Only `resolved` (an official ID or exact official name) or the employee's own choice from
    the displayed candidates is written; an ambiguous description is never auto-selected.
    """
    unresolved = Clarification(
        ClarificationCode.OCCUPATION_UNRESOLVED, FieldPath.OCCUPATION_ID.value
    )
    choices = state.get("occupation_candidates", [])
    value = text.strip()
    if choices and value.isdigit():
        index = int(value)
        return choices[index - 1]["occupationId"] if 1 <= index <= len(choices) else unresolved
    chosen = [c["occupationId"] for c in choices if c["label"] == value]
    if len(chosen) == 1:
        return chosen[0]
    if not value:
        return unresolved
    envelope = await rt.tools.resolve_occupation(ResolveOccupationRequest(query=value[:120]))
    data = envelope.data
    if envelope.status is EnvelopeStatus.ERROR or data is None:
        return unresolved
    if data.status is ResolveStatus.RESOLVED and len(data.candidates) == 1:
        return data.candidates[0].occupation_id
    if data.status is ResolveStatus.AMBIGUOUS and data.candidates:
        shown = data.candidates[:MAX_OCCUPATION_CHOICES]
        updates["occupation_candidates"] = [
            OccupationCandidateRef(occupationId=c.occupation_id, label=c.name_he) for c in shown
        ]
        rt.reply(he.occupation_candidates_text([c.name_he for c in shown]), "clarification")
        rt.halt = True
        return None
    return unresolved


async def prepare(state: ConversationState, runtime: Runtime[TurnRuntime]) -> dict[str, Any]:
    """Validate proposed facts and resolve described vehicles and occupations through the
    business tools."""
    rt = runtime.context
    assert rt.interpretation is not None
    updates: dict[str, Any] = {}
    turn = state.get("turn")
    message = str(turn.get("text") or "") if turn else ""
    extraction = normalize_facts(list(rt.interpretation.facts), message)
    for mode, text in extraction.occupation_queries.items():
        occupation = await _resolve_occupation(state, rt, text, updates)
        if occupation is None:
            break
        if isinstance(occupation, Clarification):
            extraction.clarifications.append(occupation)
            continue
        updates["occupation_candidates"] = []
        change = SetChange(op="set", field=FieldPath.OCCUPATION_ID, value=occupation)
        target = extraction.actual if mode is FactMode.ACTUAL else extraction.hypothetical
        target.append(change)
    for mode, text in extraction.vehicle_queries.items():
        if rt.halt:
            break
        resolved = await _resolve_current_vehicle(rt, text)
        if isinstance(resolved, str):
            rt.vehicle_question = resolved
            continue
        if resolved == _saved_vehicle(rt):  # the car they already have: nothing to write
            if mode is FactMode.ACTUAL:
                rt.implicit_confirmations.append(FieldPath.CURRENT_VEHICLE.value)
            continue
        change = SetChange(op="set", field=FieldPath.CURRENT_VEHICLE, value=resolved)
        target = extraction.actual if mode is FactMode.ACTUAL else extraction.hypothetical
        target.append(change)
    rt.extraction = extraction
    unknown = sorted(
        set(state.get("unknown_fields", []))
        | {f.value for f in rt.interpretation.unknown_fields if f.value in _FIELD_PATHS}
    )
    return {**updates, "unknown_fields": unknown}


async def _reload_context(rt: TurnRuntime) -> None:
    reloaded = await rt.tools.get_conversation_context()
    if reloaded.data is not None:
        rt.case = reloaded.data
        rt.case_version = reloaded.data.case_version
        rt.profile_version = reloaded.data.latest_profile_version


async def apply_updates(state: ConversationState, runtime: Runtime[TurnRuntime]) -> dict[str, Any]:
    """Version-checked writes: one operation per mode, with deterministic operation IDs."""
    rt = runtime.context
    turn = state.get("turn")
    assert turn is not None and rt.extraction is not None
    if rt.halt:
        return {}
    for mode, changes in (
        (UpdateMode.ACTUAL_UPDATE, rt.extraction.actual),
        (UpdateMode.HYPOTHETICAL, rt.extraction.hypothetical),
    ):
        if not changes:
            continue
        request = UpdateInputsRequest(
            mode=mode,
            expected_case_version=rt.case_version,
            expected_profile_version=rt.profile_version
            if mode is UpdateMode.ACTUAL_UPDATE
            else None,
            operation_id=stable_id(turn["inputId"], "update", mode.value),
            source_message_id=UUID(turn["inputId"]),
            changes=list(changes),
        )
        envelope = await rt.tools.update_conversation_inputs(request)
        if envelope.status is EnvelopeStatus.ERROR or envelope.data is None:
            code = envelope.error.code if envelope.error else None
            if code is ErrorCode.VERSION_CONFLICT:
                await _reload_context(rt)
            rt.reply(he.error_text(code), "error", code=code.value if code else None)
            rt.halt = True
            return {}
        result = envelope.data
        rt.case_version = result.new_case_version
        if result.new_profile_version is not None:
            rt.profile_version = result.new_profile_version
        accepted = [f.value for f in result.accepted_fields]
        rt.applied[mode] = accepted
        # A value the safety net kept out of the profile is reported once, after the results.
        acknowledged = [
            f
            for f in accepted
            if not (mode is UpdateMode.HYPOTHETICAL and f in rt.extraction.not_saved)
        ]
        if acknowledged:
            rt.reply(
                he.updated_text(
                    acknowledged,
                    hypothetical=mode is UpdateMode.HYPOTHETICAL,
                    stale_runs=bool(result.stale_run_ids),
                ),
                "update_confirmation",
                mode=mode.value,
                fields=acknowledged,
            )
        # `ok` means committed, not that every fact is usable: report what the server flagged.
        conflicts = [i.field.value for i in result.conflicts if i.field is not None]
        if result.conflicts:
            rt.reply(he.conflicts_text(conflicts[:2]), "conflict", fields=conflicts[:2])
        missing = [i.field.value for i in result.missing_information if i.field is not None]
        if missing:
            rt.reply(he.still_missing_text(missing[:2]), "info", fields=missing[:2])
    stated = set(rt.applied.get(UpdateMode.ACTUAL_UPDATE, []))
    awaiting = state.get("awaiting_confirmation", [])
    if stated & set(awaiting):  # a corrected value needs no separate confirmation
        rt.answered_confirmation = True
        return {"awaiting_confirmation": [f for f in awaiting if f not in stated]}
    return {}


async def apply_confirmations(
    state: ConversationState, runtime: Runtime[TurnRuntime]
) -> dict[str, Any]:
    """Confirm saved facts (G3) when the employee explicitly confirms facts the advisor asked
    about, and silently when they restate a saved fact with the same value (their saved current
    car). Values never change; facts written in this turn are never confirmed."""
    rt = runtime.context
    turn = state.get("turn")
    assert turn is not None
    interpretation, extraction = rt.interpretation, rt.extraction
    if rt.halt or interpretation is None:
        return {}
    assert extraction is not None
    stated = {c.field.value for c in [*extraction.actual, *extraction.hypothetical]}
    explicit: list[str] = []
    if interpretation.intent is Intent.CONFIRM_FACTS:
        awaiting = [f for f in state.get("awaiting_confirmation", []) if f not in stated]
        requested = {f.value for f in interpretation.confirmed_fields} or set(awaiting)
        explicit = [f for f in awaiting if f in requested]
    implicit = [f for f in rt.implicit_confirmations if f not in stated and f not in explicit]
    fields = [*explicit, *implicit]
    if not fields:
        return {}
    envelope = await rt.tools.confirm_profile_fields(
        ConfirmProfileFieldsRequest(
            expected_case_version=rt.case_version,
            expected_profile_version=rt.profile_version,
            operation_id=stable_id(turn["inputId"], "confirm"),
            source_message_id=UUID(turn["inputId"]),
            fields=[FieldPath(f) for f in fields],
        )
    )
    if envelope.status is EnvelopeStatus.ERROR or envelope.data is None:
        code = envelope.error.code if envelope.error else None
        if code is ErrorCode.VERSION_CONFLICT:
            await _reload_context(rt)
        rt.reply(he.error_text(code), "error", code=code.value if code else None)
        rt.halt = True
        return {}
    result = envelope.data
    rt.case_version = result.new_case_version
    rt.profile_version = result.new_profile_version
    rt.confirmed = [f.value for f in result.confirmed_fields]
    shown = [f for f in rt.confirmed if f in explicit]  # a silent confirmation says nothing
    if shown:
        rt.reply(he.confirmed_text(shown), "confirmation", fields=shown)
    not_confirmable = [
        i.field.value
        for i in result.not_confirmable
        if i.field is not None and i.field.value in explicit
    ]
    if not_confirmable:
        rt.reply(he.ask_for_fields(not_confirmable[:2]), "question", fields=not_confirmable[:2])
    done = set(rt.confirmed) | set(not_confirmable)
    return {
        "awaiting_confirmation": [
            f for f in state.get("awaiting_confirmation", []) if f not in done
        ]
    }
