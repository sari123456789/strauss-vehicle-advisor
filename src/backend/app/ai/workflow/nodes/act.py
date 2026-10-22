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
from app.contracts.catalogue import (
    EligibilityStatus,
    ListOptionsRequest,
    OptionFilters,
    OptionItem,
    ResolvePurpose,
    ResolveStatus,
    ResolveVehicleRequest,
    VehicleQuery,
)
from app.contracts.common import (
    EnvelopeStatus,
    ErrorCode,
    FieldPath,
    Freshness,
    Issue,
    IssueCode,
    OptionCategory,
    RunKind,
    UpdateMode,
)
from app.contracts.comparisons import CompareRequest, ComparisonDetailsRequest, ComparisonResult
from app.contracts.readiness import ReadinessGoal, ReadinessRequest, Selection, SelectionMode
from app.contracts.representatives import (
    AnswerOutcome,
    CreateQuestionRequest,
    RepresentativeFieldCode,
    RepresentativeRequestLookup,
    RequestStatus,
)

MAX_ASKS_PER_FIELD = 2
"""After this many unanswered questions a field is treated as unknown (no question loops)."""

REPRESENTATIVE_FIELDS: dict[str, RepresentativeFieldCode] = {
    FieldPath.ENTITLEMENT_GROUP.value: RepresentativeFieldCode.ENTITLEMENT_GROUP,
    FieldPath.CURRENT_PROPULSION.value: RepresentativeFieldCode.VEHICLE_PROPULSION,
    FieldPath.CURRENT_VEHICLE.value: RepresentativeFieldCode.VEHICLE_TAX_KEY,
}
"""Employee-unknown facts that may be escalated to a representative (G5 allowlist).

`occupation.waiverPolicy` is deliberately absent until the expected answer type for a waiver
(boolean versus allowance amount) is agreed with the Business module."""

# Stable `Issue.reason` tokens from the Business module (Step 4B).
CONFIRMATION_REQUIRED = "confirmation_required"
PROPULSION_DIFFERS = "catalogue_propulsion_differs"
LIST_BUILT_FROM_OTHER_FACTS = "result_set_built_from_other_facts"
ELIGIBILITY_CHANGED = "eligibility_changed_since_list"
LIST_RELEASE_CHANGED = "result_set_release_changed"

_COMPUTE_INTENTS = frozenset({Intent.CALCULATE_OPTIONS, Intent.COMPARE_TO_CURRENT})
_CATALOGUE_ID = re.compile(r"^[A-Z]{2,5}-\d{2,4}$")
_DIGITS = re.compile(r"\d")
_NON_WORD = re.compile(r"[^\w]+")
RECENT_FOR_GENERAL = 6


def _labels(state: ConversationState, rt: TurnRuntime) -> dict[str, str]:
    labels = {o["optionKey"]: o["label"] for o in state.get("displayed_options", [])}
    if rt.case is not None:
        for alternative in rt.case.alternatives:
            labels.setdefault(alternative.option_key, alternative.catalogue_id or "")
    labels.setdefault("waiver", "ויתור על רכב חברה")
    labels.update(state.get("option_labels", {}))
    labels.update(rt.labels)
    return labels


def _recent(state: ConversationState) -> list[dict[str, str]]:
    """Recent messages for a general answer, digits masked: the model never sees amounts."""
    turn = state.get("turn")
    history = (turn.get("recentMessages") if turn else None) or []
    return [
        {
            "role": "assistant" if m.get("role") == "assistant" else "employee",
            "text": _DIGITS.sub("#", m.get("text", ""))[:500],
        }
        for m in history[-RECENT_FOR_GENERAL:]
    ]


def _general(state: ConversationState, rt: TurnRuntime, fallback: str) -> None:
    turn = state.get("turn")
    question = str(turn.get("text") or "") if turn else ""
    rt.compose = ComposeRequest("general", question, fallback=fallback, recent=_recent(state))


def _resolve_refs(
    refs: list[OptionReference], displayed: list[DisplayedOption]
) -> tuple[list[str], list[str], bool]:
    """Option keys for the references, names not found in the displayed list (to look up in
    the catalogue), and whether any other reference could not be resolved."""
    keys: list[str] = []
    names: list[str] = []
    unresolved = False
    for ref in refs:
        value = ref.value.strip()
        if ref.kind == "ordinal" and value.isdigit() and 1 <= int(value) <= len(displayed):
            keys.append(displayed[int(value) - 1]["optionKey"])
        elif ref.kind == "catalogue_id" and _CATALOGUE_ID.match(value.upper()):
            keys.append(f"vehicle:{value.upper()}")
        elif ref.kind == "name" and value:
            matches = [o for o in displayed if value.casefold() in o["label"].casefold()]
            if len(matches) == 1:
                keys.append(matches[0]["optionKey"])
            elif matches:
                unresolved = True
            else:
                names.append(value)
        else:
            unresolved = True
    return list(dict.fromkeys(keys)), names, unresolved


def _normalized(text: str | None) -> str:
    return " ".join(_NON_WORD.sub(" ", (text or "").casefold()).split())


def _name_matches(name: str, item: OptionItem) -> bool:
    """The employee's words name this catalogue item: they contain its model name, or are a
    part of its full label (e.g. "ATTO 3", "BYD ATTO 3", "קיה")."""
    wanted = _normalized(name)
    model = _normalized(item.commercial_name)
    label = _normalized(he.option_label(item))
    if not wanted:
        return False
    return (bool(model) and f" {model} " in f" {wanted} ") or wanted in label


async def _resolve_names(
    rt: TurnRuntime, names: list[str], updates: dict[str, Any]
) -> list[str] | None:
    """Option keys for vehicle names not on the displayed list, through the business tools.

    Eligibility comes only from `list_eligible_options`; a name that is not eligible is looked
    up with `resolve_vehicle` (catalogue only) to tell "not eligible" from "not in the
    catalogue". None when the employee was answered instead (choice, not eligible, not found).
    """
    envelope = await rt.tools.list_eligible_options(
        ListOptionsRequest(
            categories=[OptionCategory.REGULAR, OptionCategory.FAMILY],
            expected_case_version=rt.case_version,
        )
    )
    if envelope.status is EnvelopeStatus.ERROR or envelope.data is None:
        _error(rt, envelope, updates)
        return None
    data = envelope.data
    eligible = [i for i in data.items if i.eligibility is EligibilityStatus.ELIGIBLE]
    keys: list[str] = []
    for name in names:
        matches = [i for i in eligible if _name_matches(name, i)]
        exact = [i for i in matches if _normalized(i.commercial_name) == _normalized(name)]
        matches = exact or matches
        if len(matches) == 1:
            keys.append(matches[0].option_key)
            rt.labels[matches[0].option_key] = he.option_label(matches[0])
            continue
        if matches:
            shown = matches[:10]
            updates["displayed_options"] = [
                DisplayedOption(optionKey=i.option_key, label=he.option_label(i)) for i in shown
            ]
            updates["result_set_id"] = str(data.result_set_id)
            rt.reply(
                he.vehicle_choices_text(name, [he.option_label(i) for i in shown]),
                "clarification",
            )
            return None
        await _explain_not_listed(rt, name, data.unchecked_categories, data.missing_information)
        return None
    return keys


async def _explain_not_listed(
    rt: TurnRuntime, name: str, unchecked: list[OptionCategory], missing: list[Issue]
) -> None:
    """A named vehicle that is not among the eligible options: not in the catalogue, not
    eligible, or eligibility unknown because information is missing."""
    found = await rt.tools.resolve_vehicle(
        ResolveVehicleRequest(
            purpose=ResolvePurpose.ALTERNATIVE, query=VehicleQuery(commercial_name=name[:80])
        )
    )
    data = found.data
    in_catalogue = []
    if found.status is not EnvelopeStatus.ERROR and data is not None:
        if data.status is not ResolveStatus.NOT_FOUND:
            in_catalogue = [c for c in data.candidates if c.catalogue_id]
    if not in_catalogue:
        rt.reply(he.vehicle_not_in_catalogue_text(name), "info")
        return
    labels = list(
        dict.fromkeys(f"{c.manufacturer_name} {c.commercial_name}".strip() for c in in_catalogue)
    )[:5]
    fields = [i.field.value for i in missing if i.field is not None]
    if unchecked or fields:
        rt.reply(he.vehicle_eligibility_unknown_text(labels), "info")
        if fields:
            rt.reply(he.ask_for_fields(fields[:2]), "question", fields=fields[:2])
        return
    rt.reply(he.vehicle_not_eligible_text(labels), "info")


def _has_reason(issues: list[Issue], reason: str) -> bool:
    return any(issue.reason == reason for issue in issues)


def _confirmation_fields(issues: list[Issue]) -> list[str]:
    """Saved facts the server needs confirmed in this conversation (not stale results)."""
    fields = [
        i.field.value
        for i in issues
        if i.code is IssueCode.AMBIGUOUS_INPUT
        and i.reason == CONFIRMATION_REQUIRED
        and i.field is not None
    ]
    return list(dict.fromkeys(fields))


def _error(rt: TurnRuntime, envelope: Any, updates: dict[str, Any] | None = None) -> None:
    code = envelope.error.code if envelope.error else None
    if (
        updates is not None
        and code is ErrorCode.VERSION_CONFLICT
        and _has_reason(envelope.issues, LIST_RELEASE_CHANGED)
    ):
        # The stored option list belongs to an older rules release: it can never be reused.
        updates.update(result_set_id=None, displayed_options=[])
        rt.reply(he.OPTIONS_CHANGED, "options_changed")
        return
    rt.reply(he.error_text(code), "error", code=code.value if code else None)


def _ask_confirmation(
    state: ConversationState, rt: TurnRuntime, fields: list[str], updates: dict[str, Any]
) -> None:
    """Ask only about the saved facts the current calculation needs (never a blanket check)."""
    awaiting = updates.get("awaiting_confirmation", state.get("awaiting_confirmation", []))
    updates["awaiting_confirmation"] = list(dict.fromkeys([*awaiting, *fields]))
    rt.reply(he.confirm_request_text(fields), "confirmation_request", fields=fields)


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
        _error(rt, envelope, updates)
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
    # Every comparison in the chat shows the difference from today, so the current arrangement
    # is always required: nothing is calculated until the baseline is ready.
    include_current = True
    labels = _labels(state, rt)
    # Readiness uses the same fact basis as the comparison it prepares (E2: `kind`).
    readiness_env = await rt.tools.check_calculation_readiness(
        ReadinessRequest(
            goal=ReadinessGoal.COMPARE_TO_CURRENT
            if include_current
            else ReadinessGoal.CALCULATE_NET,
            selection=selection,
            include_current=include_current,
            expected_case_version=rt.case_version,
            kind=kind,
        )
    )
    if readiness_env.status is EnvelopeStatus.ERROR or readiness_env.data is None:
        _error(rt, readiness_env, updates)
        return None
    readiness = readiness_env.data
    to_confirm = _confirmation_fields(readiness_env.issues)
    propulsion_differs = _has_reason(readiness_env.issues, PROPULSION_DIFFERS)
    pending = dict(updates.get("pending_tasks", state.get("pending_tasks", {})))
    for item in readiness.pending_representative_fields:
        pending.setdefault(
            str(item.request_id),
            PendingTask(
                fieldCode=item.field_code.value,
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
    if not readiness.ready_scenario_ids or readiness.baseline_ready is not True:
        if missing:
            await _ask_missing(state, rt, missing, updates)
        elif to_confirm:
            _ask_confirmation(state, rt, to_confirm, updates)
        elif propulsion_differs:
            rt.reply(he.PROPULSION_DIFFERS, "question", fields=[FieldPath.CURRENT_PROPULSION.value])
        elif pending_labels:
            rt.reply(he.pending_text(pending_labels), "representative_waiting")
        else:
            blocked = [
                f"• {labels.get(b.option_key, b.option_key)}: {he.issue_blocks_text(b.reason)}"
                for b in readiness.blocked_scenarios
            ]
            rt.reply("\n".join(blocked) or he.NO_RESULTS_YET, "blocked")
        return None
    with_current = readiness.baseline_ready is True
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
        _error(rt, envelope, updates)
        return None
    result = envelope.data
    updates["latest_run_id"] = str(result.run_id)
    full = not state.get("disclaimer_shown")
    text = he.comparison_text(result, labels, full_disclaimer=full)
    rt.reply(text, "comparison", runId=str(result.run_id))
    updates["disclaimer_shown"] = True
    if _has_reason(envelope.issues, LIST_BUILT_FROM_OTHER_FACTS):
        updates["result_set_id"] = None
        rt.reply(he.OPTIONS_CHANGED, "options_changed")
    changed = [
        labels.get(i.option_key, i.option_key)
        for i in envelope.issues
        if i.reason == ELIGIBILITY_CHANGED and i.option_key is not None
    ]
    if changed:
        rt.reply(he.eligibility_changed_text(changed), "options_changed")
    blocked = [
        f"• {labels.get(b.option_key, b.option_key)}: {he.issue_blocks_text(b.reason)}"
        for b in readiness.blocked_scenarios
        if b.option_key not in readiness.ready_scenario_ids
    ]
    if blocked:
        rt.reply("חלופות שלא חושבו:\n" + "\n".join(blocked), "blocked")
    if include_current and not with_current and to_confirm:
        _ask_confirmation(state, rt, to_confirm, updates)  # no delta until confirmed
    elif include_current and not with_current and propulsion_differs:
        rt.reply(he.PROPULSION_DIFFERS, "question", fields=[FieldPath.CURRENT_PROPULSION.value])
    elif include_current and not with_current:
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


def _goal_keys(goal: ActiveGoal | None) -> list[str]:
    return list(goal.get("optionKeys") or []) if goal else []


async def _selection(
    state: ConversationState,
    rt: TurnRuntime,
    updates: dict[str, Any],
    *,
    resume: ActiveGoal | None = None,
) -> Selection | None:
    """The options to calculate. A resumed goal (after a confirmation or a what-if change)
    keeps the employee's explicit selection unless this message names other options. A vehicle
    named before any list was shown is looked up among the eligible catalogue options."""
    assert rt.interpretation is not None
    displayed = state.get("displayed_options", [])
    keys, names, unresolved = _resolve_refs(list(rt.interpretation.option_refs), displayed)
    if unresolved:
        rt.reply(he.ASK_WHICH_VEHICLE, "clarification")
        return None
    if names:
        found = await _resolve_names(rt, names, updates)
        if found is None:
            return None
        keys = list(dict.fromkeys([*keys, *found]))
        updates["option_labels"] = {**state.get("option_labels", {}), **rt.labels}
    if not keys and not rt.interpretation.all_options:
        keys = _goal_keys(resume)
    if keys:
        return Selection(mode=SelectionMode.EXPLICIT, option_keys=keys[:30])
    propulsion = list(rt.interpretation.propulsion) or None
    result_set_id = state.get("result_set_id")
    if result_set_id and not rt.interpretation.all_options and not propulsion:
        return Selection(mode=SelectionMode.RESULT_SET, result_set_id=UUID(result_set_id))
    categories = [OptionCategory.REGULAR, OptionCategory.FAMILY]
    if rt.interpretation.include_waiver:
        categories.append(OptionCategory.WAIVER)
    # Car names for the result text (no option list was shown in this conversation).
    listing = await rt.tools.list_eligible_options(
        ListOptionsRequest(categories=categories, expected_case_version=rt.case_version)
    )
    if listing.data is not None:
        for item in listing.data.items:
            rt.labels.setdefault(item.option_key, he.option_label(item))
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
    full = not state.get("disclaimer_shown")
    text = he.history_text(result, labels, stale=stale, full_disclaimer=full)
    rt.reply(text, "history", runId=run_id, stale=stale)
    updates["disclaimer_shown"] = True
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
    if intent is not Intent.ASK_REPRESENTATIVE and state.get("representative_help_shown"):
        updates["representative_help_shown"] = False
    if extraction.clarifications and intent is not Intent.LIST_OPTIONS:
        rt.reply(he.clarification_text(extraction.clarifications), "clarification")
        return
    if intent is Intent.LIST_OPTIONS:
        await _list_options(state, rt, updates)
        return
    hypothetical_now = bool(rt.applied.get(UpdateMode.HYPOTHETICAL))
    actual_now = bool(rt.applied.get(UpdateMode.ACTUAL_UPDATE))
    previous = state.get("active_goal")
    resume_after_confirm = bool(rt.confirmed and previous and intent is Intent.CONFIRM_FACTS)
    if (
        intent in _COMPUTE_INTENTS
        or (hypothetical_now and intent is Intent.UPDATE_FACTS)
        or resume_after_confirm
    ):
        resuming = intent in (Intent.UPDATE_FACTS, Intent.CONFIRM_FACTS)
        selection = await _selection(state, rt, updates, resume=previous if resuming else None)
        if selection is None:
            return
        include_current = intent is Intent.COMPARE_TO_CURRENT or bool(
            intent in (Intent.UPDATE_FACTS, Intent.CONFIRM_FACTS)
            and previous
            and previous["includeCurrent"]
        )
        hypothetical = hypothetical_now or bool(
            previous and previous["kind"] == "hypothetical" and not actual_now
        )
        kind = RunKind.HYPOTHETICAL if hypothetical else RunKind.REGULAR
        if intent in _COMPUTE_INTENTS:
            goal_intent = intent.value
        elif resume_after_confirm and previous:
            goal_intent = previous["intent"]
        else:
            goal_intent = Intent.CALCULATE_OPTIONS.value
        goal = ActiveGoal(intent=goal_intent, includeCurrent=include_current, kind=kind.value)
        if selection.mode is SelectionMode.EXPLICIT and selection.option_keys:
            goal["optionKeys"] = list(selection.option_keys)
        updates["active_goal"] = goal
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
    elif intent is Intent.DATA_SOURCES:
        rt.reply(he.data_sources_text(_profile_sources(rt)), "info")
    elif intent is Intent.UPDATE_FACTS:
        if not (actual_now or hypothetical_now):
            rt.reply(he.UNCLEAR, "clarification")
    elif intent is Intent.CONFIRM_FACTS:
        if not rt.confirmed and not rt.replies:
            rt.reply(he.NOTHING_TO_CONFIRM, "info")
    elif intent is Intent.CANCEL:
        cancelled = list(state.get("cancelled_request_ids", []))
        cancelled.extend(r for r in state.get("pending_tasks", {}) if r not in cancelled)
        updates.update(
            active_goal=None,
            pending_tasks={},
            cancelled_request_ids=cancelled,
            awaiting_confirmation=[],
            occupation_candidates=[],
        )
        rt.reply(he.CANCELLED, "info")
    elif intent is Intent.ASK_REPRESENTATIVE:
        named = [f.value for f in interpretation.unknown_fields]
        known = [*named, *state.get("unknown_fields", [])]
        fields = [f for f in dict.fromkeys(known) if f in REPRESENTATIVE_FIELDS]
        if fields:
            await _create_question(state, rt, fields[0], None, updates)
        elif state.get("representative_help_shown"):
            rt.reply(he.REPRESENTATIVE_HELP_AGAIN, "clarification")
        else:
            rt.reply(he.REPRESENTATIVE_HELP, "clarification")
            updates["representative_help_shown"] = True
    elif intent is Intent.GENERAL_QUESTION:
        _general(state, rt, he.GENERAL_FALLBACK)
    elif intent is Intent.SMALLTALK:  # a greeting or thanks gets a natural, fitting reply
        _general(state, rt, he.HELP)
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


def _baseline_selection(state: ConversationState) -> Selection:
    keys = _goal_keys(state.get("active_goal"))
    if keys:
        return Selection(mode=SelectionMode.EXPLICIT, option_keys=keys[:30])
    result_set_id = state.get("result_set_id")
    if result_set_id:
        return Selection(mode=SelectionMode.RESULT_SET, result_set_id=UUID(result_set_id))
    return Selection(
        mode=SelectionMode.ALL_ELIGIBLE,
        categories=[OptionCategory.REGULAR, OptionCategory.FAMILY],
    )


async def _handle_answer(
    state: ConversationState, rt: TurnRuntime, request_id: str, task: PendingTask
) -> dict[str, Any]:
    turn = state.get("turn")
    assert turn is not None
    # The event is only a wake-up signal: the request's actual status comes from the server.
    envelope = await rt.tools.get_representative_request(
        RepresentativeRequestLookup(request_id=UUID(request_id))
    )
    remaining = dict(state.get("pending_tasks", {}))
    remaining.pop(request_id, None)
    if envelope.status is EnvelopeStatus.ERROR or envelope.data is None:
        code = envelope.error.code if envelope.error else None
        # An unknown or foreign request is dropped silently; anything else stays pending.
        return {"pending_tasks": remaining} if code is ErrorCode.NOT_FOUND else {}
    view = envelope.data
    if view.status is RequestStatus.OPEN:
        return {}
    updates: dict[str, Any] = {"pending_tasks": remaining}
    if view.status is RequestStatus.CANCELLED:
        return updates
    label = _labels(state, rt).get(task["optionKey"] or "", he.field_label(task["fieldCode"]))
    if view.status is RequestStatus.CANNOT_DETERMINE or (
        view.answer is not None and view.answer.outcome is AnswerOutcome.CANNOT_DETERMINE
    ):
        rt.reply(
            he.representative_undetermined_text(label),
            "representative_answer",
            requestId=request_id,
        )
        return updates
    rt.reply(
        he.representative_answer_text(
            label,
            task["fieldCode"],
            view.answer,
            recalculated=task["optionKey"] is not None or task["includeCurrent"],
        ),
        "representative_answer",
        requestId=request_id,
    )
    if task["optionKey"] is not None:
        selection = Selection(mode=SelectionMode.EXPLICIT, option_keys=[task["optionKey"]])
    elif task["includeCurrent"]:
        selection = _baseline_selection(state)  # a baseline fact: refresh the comparison
    else:
        return updates
    await compute(
        {**state, "pending_tasks": remaining},
        rt,
        input_id=turn["inputId"],
        selection=selection,
        include_current=task["includeCurrent"],
        kind=RunKind(task["kind"]),
        updates=updates,
        question="",
    )
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
    try:
        updates = await _handle_answer(state, rt, request_id, task)
    except ToolBudgetExceeded:
        rt.reply(he.BUDGET_EXCEEDED, "error", code="TOOL_BUDGET_EXCEEDED")
    rt.compose = None
    return updates


_SOURCE_FIELDS = (
    FieldPath.BASE_GROSS,
    FieldPath.COMMISSIONS_GROSS,
    FieldPath.BONUS_GROSS,
    FieldPath.PENSION_EMPLOYEE_RATE,
    FieldPath.STUDY_FUND_EMPLOYEE_RATE,
    FieldPath.CREDIT_POINTS,
    FieldPath.OCCUPATION_ID,
    FieldPath.ENTITLEMENT_GROUP,
    FieldPath.QUALIFYING_CHILDREN_COUNT,
    FieldPath.CURRENT_KIND,
    FieldPath.CURRENT_VEHICLE,
)


def _profile_sources(rt: TurnRuntime) -> list[tuple[str, str, str | None]]:
    """(field, status, source) of the saved profile facts; values are never read."""
    assert rt.case is not None
    profile = rt.case.profile
    arrangement = profile.current_arrangement
    nodes: dict[FieldPath, Any] = {
        FieldPath.BASE_GROSS: profile.base_gross,
        FieldPath.COMMISSIONS_GROSS: profile.commissions_gross,
        FieldPath.BONUS_GROSS: profile.bonus_gross,
        FieldPath.PENSION_EMPLOYEE_RATE: profile.pension_employee_rate,
        FieldPath.STUDY_FUND_EMPLOYEE_RATE: profile.study_fund_employee_rate,
        FieldPath.CREDIT_POINTS: profile.credit_points,
        FieldPath.OCCUPATION_ID: profile.occupation_id,
        FieldPath.ENTITLEMENT_GROUP: profile.entitlement_group,
        FieldPath.QUALIFYING_CHILDREN_COUNT: profile.qualifying_children_count,
        FieldPath.CURRENT_KIND: arrangement.kind,
        FieldPath.CURRENT_VEHICLE: arrangement.vehicle,
    }
    result: list[tuple[str, str, str | None]] = []
    for path in _SOURCE_FIELDS:
        node = nodes[path]
        result.append((path.value, node.status.value, node.source.value if node.source else None))
    return result
