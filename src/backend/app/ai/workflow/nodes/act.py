"""Deterministic dispatch from the validated intent to business tools.

Readiness, eligibility and every amount come from tool results. A pending representative
request blocks only the dependent option; ready options are calculated and shown.
"""

import re
from typing import Any
from uuid import UUID

from langgraph.runtime import Runtime

from app.ai.tools.gateway import ToolBudgetExceeded
from app.ai.workflow import messages_he as he
from app.ai.workflow.models import Intent, OptionReference
from app.ai.workflow.rendering import comparison_placeholders
from app.ai.workflow.state import (
    ActiveGoal,
    ComposeRequest,
    ConversationState,
    DisplayedOption,
    PendingTask,
    TurnRuntime,
    stable_id,
)
from app.contracts.catalogue import EligibilityStatus, ListOptionsRequest, OptionFilters
from app.contracts.common import (
    EnvelopeStatus,
    FieldPath,
    Freshness,
    OptionCategory,
    RunKind,
    UpdateMode,
)
from app.contracts.comparisons import CompareRequest, ComparisonDetailsRequest, ComparisonResult
from app.contracts.readiness import ReadinessGoal, ReadinessRequest, Selection, SelectionMode
from app.contracts.representatives import CreateQuestionRequest, RepresentativeFieldCode

MAX_ASKS_PER_FIELD = 2
"""After this many unanswered questions a field is treated as unknown (no question loops)."""

REPRESENTATIVE_FIELDS: dict[str, RepresentativeFieldCode] = {
    FieldPath.ENTITLEMENT_GROUP.value: RepresentativeFieldCode.ENTITLEMENT_GROUP,
    FieldPath.CURRENT_PROPULSION.value: RepresentativeFieldCode.VEHICLE_PROPULSION,
    FieldPath.CURRENT_VEHICLE.value: RepresentativeFieldCode.VEHICLE_TAX_KEY,
}
"""Provisional mapping of employee-unknown facts to the representative allowlist (gap G5)."""

_COMPUTE_INTENTS = frozenset({Intent.CALCULATE_OPTIONS, Intent.COMPARE_TO_CURRENT})
_CATALOGUE_ID = re.compile(r"^[A-Z]{2,5}-\d{2,4}$")


def _labels(state: ConversationState, rt: TurnRuntime) -> dict[str, str]:
    labels = {o["optionKey"]: o["label"] for o in state.get("displayed_options", [])}
    if rt.case is not None:
        for alternative in rt.case.alternatives:
            labels.setdefault(alternative.option_key, alternative.catalogue_id or "")
    labels.setdefault("waiver", "ויתור על רכב חברה")
    return labels


def _resolve_refs(
    refs: list[OptionReference], displayed: list[DisplayedOption]
) -> tuple[list[str], bool]:
    """Option keys for the references, and whether any reference could not be resolved."""
    keys: list[str] = []
    unresolved = False
    for ref in refs:
        value = ref.value.strip()
        if ref.kind == "ordinal" and value.isdigit() and 1 <= int(value) <= len(displayed):
            keys.append(displayed[int(value) - 1]["optionKey"])
        elif ref.kind == "catalogue_id" and _CATALOGUE_ID.match(value.upper()):
            keys.append(f"vehicle:{value.upper()}")
        elif ref.kind == "name":
            matches = [o for o in displayed if value.casefold() in o["label"].casefold()]
            if len(matches) == 1:
                keys.append(matches[0]["optionKey"])
            else:
                unresolved = True
        else:
            unresolved = True
    return list(dict.fromkeys(keys)), unresolved


def _error(rt: TurnRuntime, envelope: Any) -> None:
    code = envelope.error.code if envelope.error else None
    rt.reply(he.error_text(code), "error", code=code.value if code else None)


async def _list_options(state: ConversationState, rt: TurnRuntime, updates: dict[str, Any]) -> None:
    assert rt.interpretation is not None
    categories = [OptionCategory.REGULAR, OptionCategory.FAMILY]
    if rt.interpretation.include_waiver:
        categories.append(OptionCategory.WAIVER)
    propulsion = list(rt.interpretation.propulsion) or None
    envelope = await rt.tools.list_eligible_options(
        ListOptionsRequest(
            categories=categories,
            filters=OptionFilters(propulsion=propulsion),
            expected_case_version=rt.case_version,
        )
    )
    if envelope.status is EnvelopeStatus.ERROR or envelope.data is None:
        _error(rt, envelope)
        return
    data = envelope.data
    eligible = [i for i in data.items if i.eligibility is EligibilityStatus.ELIGIBLE]
    updates["displayed_options"] = [
        DisplayedOption(optionKey=i.option_key, label=he.option_label(i)) for i in eligible
    ]
    updates["result_set_id"] = str(data.result_set_id)
    rt.reply(
        he.options_text(
            data.items,
            scope_complete=data.scope_complete,
            unchecked=[c.value for c in data.unchecked_categories],
        ),
        "options",
        resultSetId=str(data.result_set_id),
        optionKeys=[i.option_key for i in eligible],
    )
    missing = [i.field.value for i in data.missing_information if i.field is not None]
    if missing:
        rt.reply(he.ask_for_fields(missing[:2]), "question", fields=missing[:2])


async def _ask_missing(
    state: ConversationState, rt: TurnRuntime, missing: list[str], updates: dict[str, Any]
) -> None:
    """Ask at most two related missing facts; never loop on facts the employee cannot give."""
    asked = dict(state.get("asked_counts", {}))
    unknown = set(updates.get("unknown_fields", state.get("unknown_fields", [])))
    for field in missing:
        if asked.get(field, 0) >= MAX_ASKS_PER_FIELD:
            unknown.add(field)
    to_ask = [f for f in missing if f not in unknown][:2]
    if to_ask:
        for field in to_ask:
            asked[field] = asked.get(field, 0) + 1
        updates["asked_counts"] = asked
        rt.reply(he.ask_for_fields(to_ask), "question", fields=to_ask)
        return
    updates["unknown_fields"] = sorted(unknown)
    escalate = [f for f in missing if f in REPRESENTATIVE_FIELDS]
    if escalate:
        await _create_question(state, rt, escalate[0], None, updates)
    else:
        rt.reply(he.unknown_field_guidance(missing[:2]), "guidance", fields=missing[:2])


async def _create_question(
    state: ConversationState,
    rt: TurnRuntime,
    field: str,
    option_key: str | None,
    updates: dict[str, Any],
) -> None:
    assert rt.case is not None
    code = REPRESENTATIVE_FIELDS[field]
    envelope = await rt.tools.create_representative_question(
        CreateQuestionRequest(
            field_code=code,
            option_key=option_key,
            question_text=f"נדרש מידע לגבי {he.field_label(field)} לצורך חישוב חלופות רכב.",
            idempotency_key=stable_id(rt.case.case_id, "representative", code.value, option_key),
            expected_case_version=rt.case_version,
        )
    )
    if envelope.status is EnvelopeStatus.ERROR or envelope.data is None:
        rt.reply(
            "לא הצלחתי לשלוח את השאלה לנציג כרגע. אפשר לנסות שוב בהמשך.",
            "error",
            code=envelope.error.code.value if envelope.error else None,
        )
        return
    goal = state.get("active_goal")
    pending = dict(updates.get("pending_tasks", state.get("pending_tasks", {})))
    pending[str(envelope.data.request_id)] = PendingTask(
        fieldCode=code.value,
        optionKey=option_key,
        includeCurrent=bool(goal and goal["includeCurrent"]),
        kind=goal["kind"] if goal else "regular",
    )
    updates["pending_tasks"] = pending
    rt.reply(
        he.representative_created_text(he.field_label(field)),
        "representative_request",
        requestId=str(envelope.data.request_id),
    )


async def compute(
    state: ConversationState,
    rt: TurnRuntime,
    *,
    input_id: str,
    selection: Selection,
    include_current: bool,
    kind: RunKind,
    updates: dict[str, Any],
    question: str,
) -> ComparisonResult | None:
    """Readiness gate, then one comparison for every ready option (partial results allowed)."""
    labels = _labels(state, rt)
    readiness_env = await rt.tools.check_calculation_readiness(
        ReadinessRequest(
            goal=ReadinessGoal.COMPARE_TO_CURRENT
            if include_current
            else ReadinessGoal.CALCULATE_NET,
            selection=selection,
            include_current=include_current,
            expected_case_version=rt.case_version,
        )
    )
    if readiness_env.status is EnvelopeStatus.ERROR or readiness_env.data is None:
        _error(rt, readiness_env)
        return None
    readiness = readiness_env.data
    pending = dict(updates.get("pending_tasks", state.get("pending_tasks", {})))
    for item in readiness.pending_representative_fields:
        pending.setdefault(
            str(item.request_id),
            PendingTask(
                fieldCode=item.field_code,
                optionKey=item.option_key,
                includeCurrent=include_current,
                kind=kind.value,
            ),
        )
    if pending != state.get("pending_tasks", {}):
        updates["pending_tasks"] = pending
    missing = [f.value for f in [*readiness.missing_employee_fields, *readiness.ambiguous_fields]]
    pending_labels = [
        labels.get(p.option_key or "", "החלופה") for p in readiness.pending_representative_fields
    ]
    if not readiness.ready_scenario_ids:
        if missing:
            await _ask_missing(state, rt, missing, updates)
        elif pending_labels:
            rt.reply(he.pending_text(pending_labels), "representative_waiting")
        else:
            blocked = [
                f"• {labels.get(b.option_key, b.option_key)}: {he.issue_blocks_text(b.reason)}"
                for b in readiness.blocked_scenarios
            ]
            rt.reply("\n".join(blocked) or he.NO_RESULTS_YET, "blocked")
        return None
    with_current = include_current and readiness.baseline_ready is True
    envelope = await rt.tools.compare_scenarios(
        CompareRequest(
            selection=Selection(
                mode=SelectionMode.EXPLICIT, option_keys=readiness.ready_scenario_ids[:30]
            ),
            include_current=with_current,
            kind=kind,
            expected_case_version=rt.case_version,
            expected_profile_version=rt.profile_version if kind is RunKind.REGULAR else None,
            operation_id=stable_id(input_id, "compare", kind.value),
        )
    )
    if envelope.status is EnvelopeStatus.ERROR or envelope.data is None:
        _error(rt, envelope)
        return None
    result = envelope.data
    updates["latest_run_id"] = str(result.run_id)
    rt.reply(he.comparison_text(result, labels), "comparison", runId=str(result.run_id))
    blocked = [
        f"• {labels.get(b.option_key, b.option_key)}: {he.issue_blocks_text(b.reason)}"
        for b in readiness.blocked_scenarios
        if b.option_key not in readiness.ready_scenario_ids
    ]
    if blocked:
        rt.reply("חלופות שלא חושבו:\n" + "\n".join(blocked), "blocked")
    if include_current and not with_current:
        baseline_missing = [f for f in missing if f.startswith("currentArrangement.")]
        rt.reply(
            "כדי להציג הפרש לעומת היום חסרים פרטים על ההסדר הנוכחי. "
            + (he.ask_for_fields(baseline_missing[:2]) if baseline_missing else ""),
            "question",
            fields=baseline_missing[:2],
        )
    if pending_labels:
        rt.reply(he.pending_text(pending_labels), "representative_waiting")
    values, descriptions = comparison_placeholders(result, labels)
    if values:
        rt.compose = ComposeRequest("comparison", question, values, descriptions)
    return result


def _selection(state: ConversationState, rt: TurnRuntime) -> Selection | None:
    assert rt.interpretation is not None
    displayed = state.get("displayed_options", [])
    keys, unresolved = _resolve_refs(list(rt.interpretation.option_refs), displayed)
    if unresolved:
        rt.reply(he.ASK_WHICH_VEHICLE, "clarification")
        return None
    if keys:
        return Selection(mode=SelectionMode.EXPLICIT, option_keys=keys[:30])
    propulsion = list(rt.interpretation.propulsion) or None
    result_set_id = state.get("result_set_id")
    if result_set_id and not rt.interpretation.all_options and not propulsion:
        return Selection(mode=SelectionMode.RESULT_SET, result_set_id=UUID(result_set_id))
    categories = [OptionCategory.REGULAR, OptionCategory.FAMILY]
    if rt.interpretation.include_waiver:
        categories.append(OptionCategory.WAIVER)
    return Selection(
        mode=SelectionMode.ALL_ELIGIBLE,
        categories=categories,
        filters=OptionFilters(propulsion=propulsion) if propulsion else None,
    )


async def _show_run(
    state: ConversationState, rt: TurnRuntime, updates: dict[str, Any], *, explain: bool
) -> None:
    run_id = state.get("latest_run_id")
    if run_id is None and rt.case is not None and rt.case.recent_runs:
        run_id = str(rt.case.recent_runs[0].run_id)
    if run_id is None:
        rt.reply(he.NO_RESULTS_YET, "info")
        return
    envelope = await rt.tools.get_comparison_details(ComparisonDetailsRequest(run_id=UUID(run_id)))
    if envelope.status is EnvelopeStatus.ERROR or envelope.data is None:
        _error(rt, envelope)
        return
    result = envelope.data.result
    labels = _labels(state, rt)
    stale = result.freshness is Freshness.STALE
    rt.reply(he.history_text(result, labels, stale=stale), "history", runId=run_id, stale=stale)
    if explain:
        values, descriptions = comparison_placeholders(result, labels)
        turn = state.get("turn")
        question = str(turn.get("text") or "") if turn else ""
        rt.compose = ComposeRequest("comparison", question, values, descriptions)


async def _act(state: ConversationState, rt: TurnRuntime, updates: dict[str, Any]) -> None:
    interpretation, extraction = rt.interpretation, rt.extraction
    turn = state.get("turn")
    assert interpretation is not None and extraction is not None and turn is not None
    intent = interpretation.intent
    if extraction.clarifications and intent is not Intent.LIST_OPTIONS:
        rt.reply(he.clarification_text(extraction.clarifications), "clarification")
        return
    if intent is Intent.LIST_OPTIONS:
        await _list_options(state, rt, updates)
        return
    hypothetical_now = bool(rt.applied.get(UpdateMode.HYPOTHETICAL))
    actual_now = bool(rt.applied.get(UpdateMode.ACTUAL_UPDATE))
    previous = state.get("active_goal")
    if intent in _COMPUTE_INTENTS or (hypothetical_now and intent is Intent.UPDATE_FACTS):
        selection = _selection(state, rt)
        if selection is None:
            return
        include_current = intent is Intent.COMPARE_TO_CURRENT or bool(
            intent is Intent.UPDATE_FACTS and previous and previous["includeCurrent"]
        )
        hypothetical = hypothetical_now or bool(
            previous and previous["kind"] == "hypothetical" and not actual_now
        )
        kind = RunKind.HYPOTHETICAL if hypothetical else RunKind.REGULAR
        updates["active_goal"] = ActiveGoal(
            intent=intent.value if intent in _COMPUTE_INTENTS else Intent.CALCULATE_OPTIONS.value,
            includeCurrent=include_current,
            kind=kind.value,
        )
        await compute(
            state,
            rt,
            input_id=turn["inputId"],
            selection=selection,
            include_current=include_current,
            kind=kind,
            updates=updates,
            question=str(turn.get("text") or ""),
        )
        return
    if intent is Intent.EXPLAIN_RESULT:
        await _show_run(state, rt, updates, explain=True)
    elif intent is Intent.SHOW_HISTORY:
        await _show_run(state, rt, updates, explain=False)
    elif intent is Intent.UPDATE_FACTS:
        if not (actual_now or hypothetical_now):
            rt.reply(he.UNCLEAR, "clarification")
    elif intent is Intent.CANCEL:
        cancelled = list(state.get("cancelled_request_ids", []))
        cancelled.extend(r for r in state.get("pending_tasks", {}) if r not in cancelled)
        updates.update(active_goal=None, pending_tasks={}, cancelled_request_ids=cancelled)
        rt.reply(he.CANCELLED, "info")
    elif intent is Intent.ASK_REPRESENTATIVE:
        fields = [f for f in state.get("unknown_fields", []) if f in REPRESENTATIVE_FIELDS]
        if fields:
            await _create_question(state, rt, fields[0], None, updates)
        else:
            rt.reply("על איזה פרט לפנות לנציג התחבורה?", "clarification")
    elif intent is Intent.GENERAL_QUESTION:
        rt.compose = ComposeRequest(
            "general", str(turn.get("text") or ""), fallback=he.GENERAL_FALLBACK
        )
    elif intent is Intent.SMALLTALK:
        rt.reply(he.HELP, "info")
    else:
        rt.reply(he.UNCLEAR, "clarification")


async def act(state: ConversationState, runtime: Runtime[TurnRuntime]) -> dict[str, Any]:
    rt = runtime.context
    updates: dict[str, Any] = {}
    if rt.halt:
        return updates
    try:
        await _act(state, rt, updates)
    except ToolBudgetExceeded:
        rt.reply(he.BUDGET_EXCEEDED, "error", code="TOOL_BUDGET_EXCEEDED")
        rt.compose = None
    return updates


async def handle_answer(state: ConversationState, runtime: Runtime[TurnRuntime]) -> dict[str, Any]:
    """A representative answer: update only the affected option; the active goal is untouched."""
    rt = runtime.context
    turn = state.get("turn")
    assert turn is not None
    request_id = str(turn.get("requestId") or "")
    if request_id in state.get("cancelled_request_ids", []):
        return {}
    task = state.get("pending_tasks", {}).get(request_id)
    if task is None:
        return {}
    updates: dict[str, Any] = {}
    remaining = dict(state.get("pending_tasks", {}))
    remaining.pop(request_id)
    updates["pending_tasks"] = remaining
    label = _labels(state, rt).get(task["optionKey"] or "", he.field_label(task["fieldCode"]))
    rt.reply(he.representative_answer_text(label), "representative_answer", requestId=request_id)
    if task["optionKey"] is None:
        return updates
    try:
        await compute(
            {**state, "pending_tasks": remaining},
            rt,
            input_id=turn["inputId"],
            selection=Selection(mode=SelectionMode.EXPLICIT, option_keys=[task["optionKey"]]),
            include_current=task["includeCurrent"],
            kind=RunKind(task["kind"]),
            updates=updates,
            question="",
        )
    except ToolBudgetExceeded:
        rt.reply(he.BUDGET_EXCEEDED, "error", code="TOOL_BUDGET_EXCEEDED")
    rt.compose = None
    return updates
