"""Regression tests for conversation quality: vehicles named before a list was shown, general
answers grounded in the conversation, where information comes from, and the representative.

Contract fake and scripted LLM only (no network). Business decides eligibility and amounts; these
tests check that the workflow asks the right business tools and answers the right question.
"""

import copy
import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.ai.application.ports.graph_runner import (
    HistoryMessage,
    InputKind,
    TurnInput,
    TurnOutcome,
)
from app.ai.application.ports.llm import LlmOutcome, LlmTaskKind
from app.ai.workflow import messages_he as he
from app.ai.workflow.models import FactField, Intent, OptionReference
from app.ai.workflow.runner import LangGraphTurnRunner
from app.contracts.cases import CaseContext
from app.contracts.catalogue import ListOptionsResult, ResolveVehicleResult
from tests.fakes.ai.fake_llm import failure, interpretation
from tests.unit.ai.workflow.test_turns import CTX, assert_employee_safe, make, say, text_of

FIXTURES = Path(__file__).resolve().parents[3] / "fakes" / "fixtures" / "success.json"
SUCCESS: dict[str, Any] = json.loads(FIXTURES.read_text(encoding="utf-8"))


def _success(tool: str) -> dict[str, Any]:
    return copy.deepcopy(SUCCESS[tool])


def _item(key: str, model: str, maker: str = "יצרן") -> dict[str, Any]:
    item = _success("list_eligible_options")["data"]["items"][0]
    catalogue_id = key.split(":", 1)[1]
    return {
        **item,
        "optionKey": key,
        "catalogueId": catalogue_id,
        "commercialName": model,
        "manufacturerName": maker,
    }


def _list(
    *items: dict[str, Any],
    unchecked: list[str] | None = None,
    missing: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    raw = _success("list_eligible_options")
    raw["data"]["items"] = list(items)
    raw["data"]["uncheckedCategories"] = unchecked or []
    raw["data"]["scopeComplete"] = not unchecked
    raw["data"]["missingInformation"] = missing or []
    return raw


def _resolved_in_catalogue(model: str, catalogue_id: str) -> dict[str, Any]:
    raw = _success("resolve_vehicle")
    candidate = raw["data"]["candidates"][0]
    raw["data"] = {
        "status": "ambiguous",  # a free-text query never resolves on its own (decision A1)
        "candidates": [{**candidate, "commercialName": model, "catalogueId": catalogue_id}],
    }
    return raw


NOT_FOUND = {"status": "ok", "data": {"status": "not_found", "candidates": []}}


async def say_with(
    runner: LangGraphTurnRunner, text: str, history: list[tuple[str, str]]
) -> TurnOutcome:
    recent = tuple(HistoryMessage(role, message) for role, message in history)  # type: ignore[arg-type]
    turn = TurnInput(uuid4(), InputKind.USER_MESSAGE, text=text, recent_messages=recent)
    return await runner.run_turn(thread_id="thread-1", tool_context=CTX, turn=turn)


def calculate(name: str) -> Any:
    return interpretation(
        Intent.CALCULATE_OPTIONS, option_refs=[OptionReference(kind="name", value=name)]
    )


# --- 1. A vehicle named before any list was shown ------------------------------------------------


async def test_vehicle_named_in_a_new_conversation_is_calculated() -> None:
    runner, port, llm = make()
    llm.interpret(calculate("ATTO 3"))
    outcome = await say(runner, "כמה אקבל נטו עם ATTO 3?")
    assert he.ASK_WHICH_VEHICLE not in text_of(outcome)
    tools = port.tools()
    assert tools.index("list_eligible_options") < tools.index("compare_scenarios")
    readiness = port.requests("check_calculation_readiness")[0]
    assert readiness.selection.option_keys == ["vehicle:CAR-10"]
    assert_employee_safe(outcome)


async def test_vehicle_name_variants_resolve_the_same_option() -> None:
    for name in ("atto 3", "BYD ATTO 3", "Atto-3"):
        runner, port, llm = make()
        llm.interpret(calculate(name))
        await say(runner, f"כמה נטו עם {name}?")
        readiness = port.requests("check_calculation_readiness")
        assert readiness and readiness[0].selection.option_keys == ["vehicle:CAR-10"], name


async def test_vehicle_in_catalogue_but_not_eligible_is_explained_not_calculated() -> None:
    runner, port, llm = make()
    port.override("list_eligible_options", ListOptionsResult, _list(_item("vehicle:CAR-06", "MG4")))
    port.override(
        "resolve_vehicle", ResolveVehicleResult, _resolved_in_catalogue("ATTO 3", "CAR-10")
    )
    llm.interpret(calculate("ATTO 3"))
    outcome = await say(runner, "כמה נטו עם ATTO 3?")
    assert "נמצא בקטלוג" in text_of(outcome) and "אינו בין החלופות" in text_of(outcome)
    assert "compare_scenarios" not in port.tools()
    request = port.requests("resolve_vehicle")[0]
    assert request.purpose.value == "alternative"  # catalogue only, never the tax dataset
    assert_employee_safe(outcome)


async def test_vehicle_outside_the_catalogue_is_reported_as_not_found() -> None:
    runner, port, llm = make()
    port.override("resolve_vehicle", ResolveVehicleResult, NOT_FOUND)
    llm.interpret(calculate("טסלה מודל 3"))
    outcome = await say(runner, "כמה נטו עם טסלה מודל 3?")
    assert "לא מצאתי רכב בשם" in text_of(outcome)
    assert "אינו בין החלופות" not in text_of(outcome)
    assert "compare_scenarios" not in port.tools()


async def test_unknown_eligibility_asks_for_the_missing_fact_instead_of_guessing() -> None:
    runner, port, llm = make()
    missing = [{"code": "MISSING_INPUT", "field": "employee.entitlementGroup"}]
    port.override(
        "list_eligible_options",
        ListOptionsResult,
        _list(unchecked=["regular"], missing=missing),
    )
    port.override(
        "resolve_vehicle", ResolveVehicleResult, _resolved_in_catalogue("ATTO 3", "CAR-10")
    )
    llm.interpret(calculate("ATTO 3"))
    outcome = await say(runner, "כמה נטו עם ATTO 3?")
    assert "חסר לי עוד מידע" in text_of(outcome)
    assert he.field_label("employee.entitlementGroup") in text_of(outcome)
    assert "אינו בין החלופות" not in text_of(outcome)
    assert "compare_scenarios" not in port.tools()


async def test_several_matching_vehicles_are_offered_and_a_follow_up_ordinal_works() -> None:
    runner, port, llm = make()
    items = (
        _item("vehicle:CAR-07", "COROLLA", "טויוטה טורקיה"),
        _item("vehicle:CAR-09", "COROLLA CROSS", "טויוטה יפן"),
    )
    port.override("list_eligible_options", ListOptionsResult, _list(*items))
    llm.interpret(calculate("טויוטה"))
    outcome = await say(runner, "כמה נטו עם טויוטה?")
    assert "לאיזה מהם התכוונת" in text_of(outcome)
    assert "COROLLA CROSS" in text_of(outcome)
    assert "compare_scenarios" not in port.tools()

    llm.interpret(
        interpretation(
            Intent.CALCULATE_OPTIONS, option_refs=[OptionReference(kind="ordinal", value="2")]
        )
    )
    await say(runner, "השני")
    assert port.requests("check_calculation_readiness")[0].selection.option_keys == [
        "vehicle:CAR-09"
    ]


async def test_exact_model_name_wins_over_a_longer_one() -> None:
    runner, port, llm = make()
    items = (_item("vehicle:CAR-07", "COROLLA"), _item("vehicle:CAR-09", "COROLLA CROSS"))
    port.override("list_eligible_options", ListOptionsResult, _list(*items))
    llm.interpret(calculate("COROLLA"))
    await say(runner, "נטו עם COROLLA")
    assert port.requests("check_calculation_readiness")[0].selection.option_keys == [
        "vehicle:CAR-07"
    ]


async def test_a_named_vehicle_on_the_displayed_list_needs_no_extra_lookup() -> None:
    runner, port, llm = make()
    llm.interpret(interpretation(Intent.LIST_OPTIONS), calculate("MG4"))
    await say(runner, "אילו רכבים מתאימים לי?")
    await say(runner, "כמה נטו עם MG4?")
    assert port.tools().count("list_eligible_options") == 1
    assert "resolve_vehicle" not in port.tools()


# --- 2. General answers use the conversation and verified facts only -----------------------------


async def test_general_answer_receives_masked_recent_conversation_and_verified_facts() -> None:
    runner, _, llm = make()
    llm.interpret(interpretation(Intent.GENERAL_QUESTION))
    llm.compose("הצגתי את הרכבים שמתאימים לקבוצת הזכאות שלך, כדי שנוכל לבחור מה לחשב.")
    history = [
        ("user", "אילו רכבים אפשר לבחור?"),
        ("assistant", "1. בי ווי די ATTO 3\nנטו משוער 10,001 ₪"),
    ]
    outcome = await say_with(runner, "אז למה הבאת לי חלופות?", history)
    task = llm.calls(LlmTaskKind.COMPOSE)[0]
    sent = task.messages[0].text
    assert "אז למה הבאת לי חלופות?" in sent and "ATTO" in sent
    assert "10,001" not in sent and "10" not in sent  # amounts never reach the model
    assert "transportation representative" in task.system_instruction
    assert "There is no other advisor" in task.system_instruction
    assert outcome.messages[-1].kind == "explanation"


async def test_general_answer_with_a_number_still_falls_back_safely() -> None:
    runner, _, llm = make()
    llm.interpret(interpretation(Intent.GENERAL_QUESTION))
    llm.compose("פנו ליועץ בטלפון 03-1234567.")
    outcome = await say(runner, "איך פונים ליועץ?")
    assert "1234567" not in text_of(outcome)
    assert outcome.messages[-1].text == he.GENERAL_FALLBACK


# --- 3. Where the information comes from ---------------------------------------------------------


async def test_data_sources_question_is_answered_from_the_profile_provenance() -> None:
    runner, port, llm = make()
    llm.interpret(interpretation(Intent.DATA_SOURCES))
    outcome = await say(runner, "מאיפה יש לך את הפרטים שלי?")
    text = text_of(outcome)
    assert he.NO_RESULTS_YET not in text
    assert "הפרופיל השמור" in text and "רשות המסים" in text and "מקור" in text
    assert "18500" not in text and "18,500" not in text  # sources only, never values
    assert llm.calls(LlmTaskKind.COMPOSE) == []
    assert port.tools() == ["get_conversation_context"]
    assert_employee_safe(outcome)


async def test_data_sources_distinguish_sources_per_field() -> None:
    runner, port, llm = make()
    context = _success("get_conversation_context")
    profile = context["data"]["profile"]
    profile["entitlementGroup"]["source"] = "representative"
    profile["bonusGross"] = {"status": "missing", "revision": 0}
    port.override("get_conversation_context", CaseContext, context)
    llm.interpret(interpretation(Intent.DATA_SOURCES))
    text = text_of(await say(runner, "מאיפה המידע?"))
    group_line = next(line for line in text.splitlines() if "קבוצת הזכאות" in line)
    assert "נציג התחבורה" in group_line
    assert "עדיין חסרים" in text and he.field_label("payroll.bonusGross") in text


async def test_how_net_is_calculated_in_general_is_answered_without_a_previous_run() -> None:
    runner, port, llm = make()
    context = _success("get_conversation_context")
    context["data"]["recentRuns"] = []
    port.override("get_conversation_context", CaseContext, context)
    llm.interpret(interpretation(Intent.GENERAL_QUESTION))
    llm.compose("שווי השימוש מתווסף להכנסה החייבת, ומנוכים מס, ביטוח לאומי והפרשות.")
    outcome = await say(runner, "איך מחושב הנטו?")
    assert he.NO_RESULTS_YET not in text_of(outcome)
    assert "get_comparison_details" not in port.tools()
    system = llm.calls(LlmTaskKind.COMPOSE)[0].system_instruction
    assert "How the estimated net is calculated" in system
    assert outcome.messages[-1].kind == "explanation"


async def test_explaining_a_calculation_that_was_never_made_says_so() -> None:
    runner, port, llm = make()
    context = _success("get_conversation_context")
    context["data"]["recentRuns"] = []
    port.override("get_conversation_context", CaseContext, context)
    llm.interpret(interpretation(Intent.EXPLAIN_RESULT))
    outcome = await say(runner, "תסביר לי את החישוב")
    assert text_of(outcome) == he.NO_RESULTS_YET  # it is a question about a calculation


async def test_interpretation_separates_data_sources_from_how_net_is_calculated() -> None:
    runner, _, llm = make()
    llm.interpret(interpretation(Intent.SMALLTALK))
    await say(runner, "שלום")
    system = llm.tasks[0].system_instruction
    assert "data_sources (where the employee's details" in system
    assert "how net pay is calculated in\ngeneral" in system
    assert "explain_result (explain a calculation or result\nalready made" in system


async def test_previous_comparison_request_without_runs_still_says_so() -> None:
    runner, port, llm = make()
    context = _success("get_conversation_context")
    context["data"]["recentRuns"] = []
    port.override("get_conversation_context", CaseContext, context)
    llm.interpret(interpretation(Intent.SHOW_HISTORY))
    outcome = await say(runner, "תראה לי את ההשוואה הקודמת")
    assert text_of(outcome) == he.NO_RESULTS_YET


# --- 4. The transportation representative --------------------------------------------------------


async def test_representative_without_topic_explains_once_then_does_not_loop() -> None:
    runner, port, llm = make()
    llm.interpret(
        interpretation(Intent.ASK_REPRESENTATIVE), interpretation(Intent.ASK_REPRESENTATIVE)
    )
    first = text_of(await say(runner, "אני רוצה לדבר עם נציג"))
    second = text_of(await say(runner, "נציג בבקשה"))
    assert first == he.REPRESENTATIVE_HELP
    assert second == he.REPRESENTATIVE_HELP_AGAIN and second != first
    assert first.count("?") == 1  # one focused question
    assert "create_representative_question" not in port.tools()


async def test_representative_help_is_shown_again_after_a_topic_change() -> None:
    runner, _, llm = make()
    llm.interpret(
        interpretation(Intent.ASK_REPRESENTATIVE),
        interpretation(Intent.LIST_OPTIONS),
        interpretation(Intent.ASK_REPRESENTATIVE),
    )
    await say(runner, "נציג")
    await say(runner, "אילו רכבים מתאימים לי?")
    assert text_of(await say(runner, "נציג")) == he.REPRESENTATIVE_HELP


async def test_representative_with_a_named_topic_sends_the_question() -> None:
    runner, port, llm = make()
    llm.interpret(
        interpretation(Intent.ASK_REPRESENTATIVE, unknown_fields=[FactField.ENTITLEMENT_GROUP])
    )
    outcome = await say(runner, "תשאל את הנציג מה קבוצת הזכאות שלי")
    assert "create_representative_question" in port.tools()
    assert outcome.messages[-1].kind == "representative_request"


async def test_a_car_question_after_the_representative_is_handled_as_a_car_question() -> None:
    runner, port, llm = make()
    llm.interpret(interpretation(Intent.ASK_REPRESENTATIVE), calculate("ATTO 3"))
    await say(runner, "איך פונים לנציג?")
    outcome = await say(runner, "כמה נטו עם ATTO 3?")
    assert he.REPRESENTATIVE_HELP_AGAIN not in text_of(outcome)
    assert "compare_scenarios" in port.tools()
    state = await runner.conversation_state("thread-1")
    assert not state.get("representative_help_shown")


async def test_interpretation_prompt_states_the_topic_rules() -> None:
    runner, _, llm = make()
    llm.interpret(interpretation(Intent.SMALLTALK))
    await say(runner, "שלום")
    system = llm.tasks[0].system_instruction
    assert "Classify by EMPLOYEE_MESSAGE" in system
    assert "data_sources" in system
    assert "even when\nSTATE.displayedOptions is empty" in system


# --- Found in the live check ---------------------------------------------------------------------


async def test_interpretation_asks_for_catalogue_spelling_of_hebrew_car_names() -> None:
    runner, _, llm = make()
    llm.interpret(interpretation(Intent.SMALLTALK))
    await say(runner, "שלום")
    assert '"מאזדה 3" -> "MAZDA 3"' in llm.tasks[0].system_instruction


async def test_comparison_explanation_may_not_refer_the_employee_to_anyone() -> None:
    runner, _, llm = make()
    llm.interpret(calculate("ATTO 3"), interpretation(Intent.EXPLAIN_RESULT))
    llm.compose("זה הנטו המשוער.")
    await say(runner, "כמה נטו עם ATTO 3?")
    assert not llm.calls(LlmTaskKind.COMPOSE)  # no automatic explanation under the result
    await say(runner, "למה?")  # explained only when the employee asks
    system = llm.calls(LlmTaskKind.COMPOSE)[0].system_instruction
    assert "Never invite the employee to contact anyone" in system
    assert "How the estimated net is calculated" in system and "gender-neutral" in system


async def test_model_text_in_another_script_is_dropped() -> None:
    runner, _, llm = make()
    llm.interpret(interpretation(Intent.GENERAL_QUESTION))
    llm.compose("החלופות מבוססות על הקטלוג במระบบ.")  # a stray Thai token, seen live
    outcome = await say(runner, "למה הבאת לי חלופות?")
    assert "ระบบ" not in text_of(outcome)
    assert outcome.messages[-1].text == he.GENERAL_FALLBACK


async def test_fallback_texts_do_not_invent_departments() -> None:
    assert "מחלקת" not in he.GENERAL_FALLBACK


async def test_rate_limited_reply_is_stored_with_whole_seconds() -> None:
    runner, _, llm = make()
    llm.script(LlmTaskKind.INTERPRET, failure(LlmOutcome.RATE_LIMITED, 4.27))
    outcome = await say(runner, "אני רוצה לדבר עם נציג")
    retry = outcome.messages[0].payload["retryAfterSeconds"]
    assert retry == 5 and isinstance(retry, int)  # a float failed the whole turn (no reply)


async def test_a_vehicle_resolved_from_the_catalogue_keeps_its_name_in_later_turns() -> None:
    runner, _, llm = make()
    llm.interpret(calculate("ATTO 3"), interpretation(Intent.SMALLTALK))
    await say(runner, "כמה נטו עם ATTO 3?")
    await say(runner, "תודה")  # a later turn keeps the label in the conversation state
    state = await runner.conversation_state("thread-1")
    assert state["option_labels"]["vehicle:CAR-10"].endswith("ATTO 3")
    assert state.get("displayed_options", []) == []  # ordinals still refer to shown lists only


# --- Natural tone --------------------------------------------------------------------------------


async def test_full_disclaimer_is_shown_once_then_a_short_reminder() -> None:
    runner, _, llm = make()
    llm.interpret(calculate("ATTO 3"), calculate("MG4"))
    first = text_of(await say(runner, "כמה נטו עם ATTO 3?"))
    second = text_of(await say(runner, "ועם MG4?"))
    assert he.DISCLAIMER in first and he.SHORT_DISCLAIMER not in first
    assert he.SHORT_DISCLAIMER in second and he.DISCLAIMER not in second


async def test_greeting_gets_a_contextual_reply_with_a_warm_fallback() -> None:
    runner, port, llm = make()
    llm.interpret(interpretation(Intent.SMALLTALK), interpretation(Intent.SMALLTALK))
    llm.compose("בשמחה! רוצה שנעבור על הרכבים שמגיעים לך?")
    replied = await say(runner, "תודה רבה")
    assert replied.messages[-1].kind == "explanation"
    assert "בשמחה" in text_of(replied)
    assert llm.calls(LlmTaskKind.COMPOSE)[0].messages[0].text.count("תודה רבה") == 1
    fallback = await say(runner, "שלום")  # model unavailable -> the fixed warm greeting
    assert text_of(fallback) == he.HELP
    assert port.tools().count("get_conversation_context") == 2  # no business tools for chit-chat


async def test_prompts_carry_the_persona() -> None:
    runner, _, llm = make()
    llm.interpret(interpretation(Intent.GENERAL_QUESTION))
    llm.compose("אין בעיה.")
    await say(runner, "מה זה שווי שימוש?")
    system = llm.calls(LlmTaskKind.COMPOSE)[0].system_instruction
    assert "warm, down-to-earth colleague" in system and "gender-neutral" in system


GENDERED = re.compile(r"(אתה|תרצה|תרצי|תוכל |תוכלי)")  # "את" is also the object marker


async def test_fixed_texts_avoid_gendered_address() -> None:
    texts = [
        value
        for name, value in vars(he).items()
        if isinstance(value, str)
        and name.isupper()
        and any("\u0590" <= c <= "\u05ff" for c in value)
    ]
    assert len(texts) > 10
    for text in texts:
        assert not GENDERED.search(text), text
