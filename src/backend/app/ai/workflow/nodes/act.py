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
from app.ai.workflow.nodes.understand import profile_statuses
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
    Propulsion,
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

SALARY_FIELDS = (
    FieldPath.BASE_GROSS.value,
    FieldPath.COMMISSIONS_GROSS.value,
    FieldPath.BONUS_GROSS.value,
    FieldPath.PENSION_EMPLOYEE_RATE.value,
    FieldPath.STUDY_FUND_EMPLOYEE_RATE.value,
    FieldPath.CREDIT_POINTS.value,
)
"""Salary facts every net calculation uses, whether or not it compares with today."""

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
    values, descriptions = _saved_facts(rt)
    rt.compose = ComposeRequest(
        "general",
        question,
        values=values,
        descriptions=descriptions,
        fallback=fallback,
        recent=_recent(state),
    )


_SAVED_FACTS = {
    "fact:currentKind": (FieldPath.CURRENT_KIND.value, "סוג ההסדר הנוכחי של העובד"),
    "fact:currentVehicle": (FieldPath.CURRENT_VEHICLE.value, "הרכב שיש לעובד היום"),
    "fact:currentPropulsion": (FieldPath.CURRENT_PROPULSION.value, "סוג ההנעה של הרכב הנוכחי"),
    "fact:currentParticipation": (
        FieldPath.CURRENT_PARTICIPATION.value,
        "ההשתתפות החודשית של העובד ברכב הנוכחי",
    ),
    "fact:group": (FieldPath.ENTITLEMENT_GROUP.value, "קבוצת הזכאות של העובד"),
}


def _saved_facts(rt: TurnRuntime) -> tuple[dict[str, str], dict[str, str]]:
    """The employee's saved car-related details as placeholders, so a general answer can state
    them ("which car do I have?") without the model ever writing a value. No salary data."""
    fields = [path for path, _desc in _SAVED_FACTS.values()]
    shown = _saved_values(rt, fields)
    values: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    for name, (path, description) in _SAVED_FACTS.items():
        if path in shown:
            values[name] = shown[path]
            descriptions[name] = description
    return values, descriptions


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
    in_catalogue = []
    # The employee often adds the maker ("TESLA MODEL 3"); the catalogue's commercial name is
    # the model alone ("MODEL 3"), so try without the first word too.
    words = name.split()
    queries = [name] + ([" ".join(words[1:])] if len(words) > 1 else [])
    for query in queries:
        found = await rt.tools.resolve_vehicle(
            ResolveVehicleRequest(
                purpose=ResolvePurpose.ALTERNATIVE, query=VehicleQuery(commercial_name=query[:80])
            )
        )
        data = found.data
        if found.status is not EnvelopeStatus.ERROR and data is not None:
            if data.status is not ResolveStatus.NOT_FOUND:
                in_catalogue = [c for c in data.candidates if c.catalogue_id]
        if in_catalogue:
            break
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
    state: ConversationState,
    rt: TurnRuntime,
    fields: list[str],
    updates: dict[str, Any],
    *,
    text: str | None = None,
) -> None:
    """Ask only about the saved facts the current calculation needs (never a blanket check)."""
    awaiting = updates.get("awaiting_confirmation", state.get("awaiting_confirmation", []))
    updates["awaiting_confirmation"] = list(dict.fromkeys([*awaiting, *fields]))
    if text is None and fields and set(fields) <= set(awaiting):
        # Already asked with the full list: a short reminder instead of the same block again.
        rt.reply(he.CONFIRM_AGAIN, "confirmation_request", fields=fields)
        return
    rt.reply(
        text or he.confirm_request_text(fields, _saved_values(rt, fields)),
        "confirmation_request",
        fields=fields,
    )


def _saved_salary(rt: TurnRuntime) -> tuple[list[str], bool]:
    """(saved salary facts a calculation would use although the employee neither stated nor
    confirmed them in this conversation, whether the employee already gave salary facts here).

    Statuses only: salary values are never read. Facts written or confirmed in this turn count
    as given, since the case context was loaded before them. Without a loaded case context
    nothing is known to be saved-only, so nothing is asked or noted.
    """
    if rt.case is None:
        return [], True
    statuses = profile_statuses(rt.case)
    unconfirmed = {f.value for f in rt.case.fields_requiring_confirmation}
    given_now = {
        *rt.applied.get(UpdateMode.ACTUAL_UPDATE, []),
        *rt.applied.get(UpdateMode.HYPOTHETICAL, []),
        *rt.confirmed,
        *(o.field.value for o in rt.case.hypothetical_overrides),
    }
    saved = [f for f in SALARY_FIELDS if f in unconfirmed and f not in given_now]
    given = any(
        f in given_now or (statuses.get(f) == "usable" and f not in unconfirmed)
        for f in SALARY_FIELDS
    )
    return saved, given


def _saved_values(rt: TurnRuntime, fields: list[str]) -> dict[str, str]:
    """Display values of saved profile fields, read from the case context (never computed)."""
    if rt.case is None:
        return {}
    profile = rt.case.profile
    values: dict[str, str] = {}
    for field in fields:
        group, _, name = field.partition(".")
        snake = "".join(f"_{c.lower()}" if c.isupper() else c for c in name)
        holder = profile.current_arrangement if group == "currentArrangement" else profile
        stored = getattr(holder, snake, None)
        value = getattr(stored, "value", None)
        vehicle_label = None
        if field == FieldPath.CURRENT_VEHICLE.value and value is not None:
            catalogue_id = getattr(value, "catalogue_id", None)
            vehicle_label = _labels_for_catalogue(rt).get(catalogue_id or "")
        text = he.saved_value_text(field, value, vehicle_label)
        if text:
            values[field] = text
    return values


def _labels_for_catalogue(rt: TurnRuntime) -> dict[str, str]:
    return {
        key.removeprefix("vehicle:"): label
        for key, label in rt.labels.items()
        if key.startswith("vehicle:")
    }


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
    answered = False
    # Giving up the car: say plainly whether this employee's occupation allows it.
    if rt.interpretation.include_waiver:
        waiver = [i for i in data.items if i.category is OptionCategory.WAIVER]
        if waiver and waiver[0].eligibility is EligibilityStatus.ELIGIBLE:
            rt.reply(he.WAIVER_ALLOWED, "info")
        elif waiver and waiver[0].eligibility is EligibilityStatus.INELIGIBLE:
            rt.reply(he.WAIVER_NOT_ALLOWED, "info")
        answered = bool(waiver)
    # A car the employee named ("do you have ATTO 3, am I eligible?"): answer it directly.
    named_labels: list[str] = []
    for ref in rt.interpretation.option_refs:
        if ref.kind != "name":
            continue
        answered = True
        named = [i for i in data.items if _name_matches(ref.value, i)]
        if any(i.eligibility is EligibilityStatus.ELIGIBLE for i in named):
            label = he.option_label(named[0])
            named_labels.append(label)
            rt.reply(he.named_car_eligible_text(label), "info")
        elif named:
            rt.reply(he.named_car_not_eligible_text(he.option_label(named[0])), "info")
        else:
            # Not among the listed options: not in the catalogue, or in another group.
            await _explain_not_listed(
                rt, ref.value, data.unchecked_categories, data.missing_information
            )
    if answered:
        # A direct answer, then one next step; the full list stays behind the button only.
        rt.reply(
            he.offer_named_text(named_labels),
            "options",
            resultSetId=str(data.result_set_id),
            optionKeys=[i.option_key for i in eligible],
        )
        return
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


GENERAL_FIELD = RepresentativeFieldCode.GENERAL_QUESTION.value


async def _create_general_question(
    state: ConversationState, rt: TurnRuntime, question: str, updates: dict[str, Any]
) -> None:
    """Send an open company-car question to the representative. Digits are masked so no salary
    or personal number reaches the representative."""
    assert rt.case is not None
    text = _DIGITS.sub("#", question).strip()[:1000] or question[:1000]
    envelope = await rt.tools.create_representative_question(
        CreateQuestionRequest(
            field_code=RepresentativeFieldCode.GENERAL_QUESTION,
            option_key=None,
            question_text=text,
            idempotency_key=stable_id(rt.case.case_id, "representative", GENERAL_FIELD, text),
            expected_case_version=rt.case_version,
        )
    )
    updates["offered_question"] = None
    if envelope.status is EnvelopeStatus.ERROR or envelope.data is None:
        rt.reply(
            "לא הצלחתי לשלוח את השאלה לנציג כרגע. אפשר לנסות שוב בהמשך.",
            "error",
            code=envelope.error.code.value if envelope.error else None,
        )
        return
    pending = dict(updates.get("pending_tasks", state.get("pending_tasks", {})))
    pending[str(envelope.data.request_id)] = PendingTask(
        fieldCode=GENERAL_FIELD, optionKey=None, includeCurrent=False, kind="regular"
    )
    updates["pending_tasks"] = pending
    rt.reply(
        he.general_question_sent_text(),
        "representative_request",
        requestId=str(envelope.data.request_id),
    )


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
    employee_request: bool = True,
) -> ComparisonResult | None:
    """Readiness gate, then one comparison for every ready option (partial results allowed).

    `employee_request=False` (a representative answer resumes the work) never asks to confirm
    saved salary facts; the result then says which saved facts it used.

    `include_current` follows the question: "what would my net be with X" needs the salary facts
    only; "how would my net change compared to today" also needs the current arrangement. A
    baseline that is not ready blocks only the difference from today, never the net of a ready
    alternative (agent spec §2.5 and §6).
    """
    labels = _labels(state, rt)
    # Product decision (2026-10-22): the chat's purpose is the monthly DIFFERENCE from today, so
    # every calculation includes the current arrangement and nothing is shown until the baseline
    # is ready (no net-only results). This narrows agent spec §2.5/§6.
    include_current = True
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
    saved_salary, salary_given = _saved_salary(rt)
    if (
        employee_request
        and readiness.ready_scenario_ids
        and saved_salary
        and not salary_given
        and not any(f in SALARY_FIELDS for f in missing)
        and not state.get("salary_confirmation_asked")
    ):
        # Once per conversation, and only when the result would rest entirely on salary facts
        # saved in an earlier conversation. A comparison asks about today's arrangement in the
        # same message, so the employee answers once.
        baseline_missing = (
            [f for f in missing if f.startswith("currentArrangement.")] if include_current else []
        )
        fields = list(dict.fromkeys([*saved_salary, *(to_confirm if include_current else [])]))
        _ask_confirmation(
            state, rt, fields, updates, text=he.confirm_saved_text(fields, baseline_missing)
        )
        updates["salary_confirmation_asked"] = True
        return None
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
        _error(rt, envelope, updates)
        return None
    result = envelope.data
    updates["latest_run_id"] = str(result.run_id)
    full = not state.get("disclaimer_shown")
    text = he.comparison_text(result, labels, full_disclaimer=full)
    ranked = he.ranking(result)
    if ranked:
        rt.reply(text, "comparison", runId=str(result.run_id), optionKeys=ranked)
    else:
        rt.reply(text, "comparison", runId=str(result.run_id))
    updates["disclaimer_shown"] = True
    noted = list(state.get("saved_facts_noted", []))
    unnoted = [f for f in saved_salary if f not in noted]
    if unnoted:  # calculated with saved facts the employee did not restate: say so, once
        rt.reply(he.saved_facts_note(unnoted), "info", fields=unnoted)
        updates["saved_facts_noted"] = [*noted, *unnoted]
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
    # No automatic explanation: the result stands on its own; the advisor explains only when the
    # employee asks ("למה?", "תסביר"), through the explain_result intent.
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
    # A resumed goal (e.g. after "yes") keeps its waiver option and drive-type filter.
    resumed_propulsion = resume.get("propulsion", []) if resume else []
    propulsion = list(rt.interpretation.propulsion) or [Propulsion(p) for p in resumed_propulsion]
    include_waiver = rt.interpretation.include_waiver or bool(
        resume and resume.get("includeWaiver")
    )
    if include_waiver and not rt.interpretation.all_options and not propulsion:
        # "I want to give up the car": compare giving it up with today, not every car.
        rt.labels.setdefault("waiver", "ויתור על רכב חברה")
        return Selection(mode=SelectionMode.EXPLICIT, option_keys=["waiver"])
    result_set_id = state.get("result_set_id")
    if result_set_id and not rt.interpretation.all_options and not propulsion:
        return Selection(mode=SelectionMode.RESULT_SET, result_set_id=UUID(result_set_id))
    categories = [OptionCategory.REGULAR, OptionCategory.FAMILY]
    if include_waiver:
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
    offered = state.get("offered_question")
    if offered:
        if interpretation.accepts_offer:
            await _create_general_question(state, rt, offered, updates)
            return
        updates["offered_question"] = None
    topic = interpretation.representative_topic
    if topic == "out_of_scope" and intent in (
        Intent.GENERAL_QUESTION,
        Intent.UNCLEAR,
        Intent.ASK_REPRESENTATIVE,
    ):
        rt.reply(he.OUT_OF_SCOPE, "info")
        return
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
    # "Yes, but my salary went up to 19,000" answers the confirmation too: calculate right away.
    resume_after_correction = bool(rt.answered_confirmation and previous and actual_now)
    resumed = resume_after_confirm or resume_after_correction
    if (
        intent in _COMPUTE_INTENTS
        or (hypothetical_now and intent is Intent.UPDATE_FACTS)
        or resumed
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
        elif resumed and previous:
            goal_intent = previous["intent"]
        else:
            goal_intent = Intent.CALCULATE_OPTIONS.value
        goal = ActiveGoal(intent=goal_intent, includeCurrent=include_current, kind=kind.value)
        if selection.mode is SelectionMode.EXPLICIT and selection.option_keys:
            goal["optionKeys"] = list(selection.option_keys)
        carried = previous if resuming and previous else None
        if interpretation.include_waiver or (carried and carried.get("includeWaiver")):
            goal["includeWaiver"] = True
        carried_propulsion = carried.get("propulsion", []) if carried else []
        propulsion = [p.value for p in interpretation.propulsion] or list(carried_propulsion)
        if propulsion:
            goal["propulsion"] = propulsion
        updates["active_goal"] = goal
        await compute(
            state,
            rt,
            input_id=turn["inputId"],
            selection=selection,
            # A current car mentioned but not identified: no difference against a car they may
            # no longer have; the goal keeps `includeCurrent`, so the answer resumes it.
            include_current=include_current and rt.vehicle_question is None,
            kind=kind,
            updates=updates,
        )
        return
    if intent is Intent.EXPLAIN_RESULT:
        await _show_run(state, rt, updates, explain=True)
    elif intent is Intent.SHOW_HISTORY:
        await _show_run(state, rt, updates, explain=False)
    elif intent is Intent.DATA_SOURCES:
        rt.reply(he.data_sources_text(_profile_sources(rt)), "info")
    elif intent is Intent.UPDATE_FACTS:
        if actual_now or hypothetical_now or rt.vehicle_question is not None:
            pass  # acknowledged by the update, or asked about after this turn
        elif rt.confirmed:  # they restated a saved fact ("יש לי קורולה קרוס"): nothing to write
            rt.reply(he.confirmed_text(rt.confirmed), "confirmation", fields=rt.confirmed)
        else:
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
        elif interpretation.question_for_representative:
            await _create_general_question(
                state, rt, interpretation.question_for_representative, updates
            )
        elif any(t["fieldCode"] == GENERAL_FIELD for t in state.get("pending_tasks", {}).values()):
            rt.reply(he.GENERAL_ALREADY_SENT, "representative_waiting")
        elif state.get("representative_help_shown"):
            rt.reply(he.REPRESENTATIVE_HELP_AGAIN, "clarification")
        else:
            rt.reply(he.REPRESENTATIVE_HELP, "clarification")
            updates["representative_help_shown"] = True
    elif intent is Intent.GENERAL_QUESTION:
        question = interpretation.question_for_representative
        if topic == "needs_representative" and question:
            updates["offered_question"] = question
            rt.reply(he.offer_question_text(question), "question")
        else:
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
    if rt.vehicle_question is not None:  # after whatever could be calculated without it
        vehicle = FieldPath.CURRENT_VEHICLE.value
        rt.reply(rt.vehicle_question, "question", fields=[vehicle])
        awaiting = updates.get("awaiting_confirmation", state.get("awaiting_confirmation", []))
        updates["awaiting_confirmation"] = list(dict.fromkeys([*awaiting, vehicle]))
    not_saved = [
        f
        for f in (rt.extraction.not_saved if rt.extraction else [])
        if f in rt.applied.get(UpdateMode.HYPOTHETICAL, [])
    ]
    if not_saved:
        rt.reply(he.not_saved_text(not_saved), "info", fields=not_saved)
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
    if task["fieldCode"] == GENERAL_FIELD:
        answered = view.status is RequestStatus.ANSWERED and view.answer is not None
        rt.reply(
            he.general_answer_text(view.answer.text or view.answer.value)
            if answered and view.answer is not None
            else "נציג התחבורה לא יכול היה לענות על השאלה הזו. אם תרצו, אפשר לנסח אותה אחרת.",
            "representative_answer",
            requestId=request_id,
        )
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
        employee_request=False,
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
        # Not asked through the chat (e.g. the stand-alone request form): a general question's
        # answer is still relayed here, since this conversation is bound to the request's case.
        task = PendingTask(
            fieldCode=GENERAL_FIELD, optionKey=None, includeCurrent=False, kind="regular"
        )
        lookup = await rt.tools.get_representative_request(
            RepresentativeRequestLookup(request_id=UUID(request_id))
        )
        if lookup.data is None or lookup.data.field_code.value != GENERAL_FIELD:
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
