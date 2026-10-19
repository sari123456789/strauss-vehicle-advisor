"""Stage B (workflow level): real LangGraph turns over the REAL `BusinessToolsFacade`.

`LangGraphTurnRunner(services.tools, ScriptedLlm())`: the scripted LLM only proposes intents
(no network, no Gemini); every number shown to the employee must come from Business.
Scenarios 2-4, 6-9 at workflow level. Requires the AI dependencies (`langgraph`); without
them the module is skipped with an explicit reason - never silently passed.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("langgraph", reason="AI dependencies (langgraph) are not installed")

from app.ai.application.ports.graph_runner import (  # noqa: E402
    InputKind,
    TurnInput,
    TurnOutcome,
    TurnStatus,
)
from app.ai.application.ports.llm import LlmTaskKind  # noqa: E402
from app.ai.workflow.messages_he import shekels  # noqa: E402
from app.ai.workflow.models import (  # noqa: E402
    FactField,
    FactMode,
    Intent,
    OptionReference,
    ProposedFact,
)
from app.ai.workflow.runner import LangGraphTurnRunner  # noqa: E402
from app.contracts.catalogue import EligibilityStatus, ListOptionsRequest  # noqa: E402
from app.contracts.common import RunKind  # noqa: E402
from app.contracts.comparisons import ComparisonDetailsRequest  # noqa: E402
from app.contracts.tool_context import ToolContext  # noqa: E402
from tests.fakes.ai.fake_llm import ScriptedLlm, interpretation  # noqa: E402
from tests.integration.ai_business.database.stage_b_helpers import (  # noqa: E402
    RecordingPort,
    business,
    demo_subject,
    open_case,
    runs_of,
)
from tests.integration.business.database.case_helpers import (  # noqa: E402
    new_user,
    persistence,
    profile,
)
from tests.integration.business.database.db_helpers import run  # noqa: E402
from tests.unit.ai.workflow.test_turns import RAW_CODES  # noqa: E402
from tests.unit.business.bl.test_reference_employees import SPEC_RAW  # noqa: E402

pytestmark = [pytest.mark.db, pytest.mark.sources]
D = Decimal


async def say(
    runner: LangGraphTurnRunner, ctx: ToolContext, text: str, input_id: UUID | None = None
) -> TurnOutcome:
    turn = TurnInput(input_id or uuid4(), InputKind.USER_MESSAGE, text=text)
    return await runner.run_turn(thread_id=str(ctx.conversation_id), tool_context=ctx, turn=turn)


def text_of(outcome: TurnOutcome) -> str:
    return "\n".join(m.text for m in outcome.messages)


def of_kind(outcome: TurnOutcome, kind: str) -> list[Any]:
    return [m for m in outcome.messages if m.kind == kind]


def car(catalogue_id: str) -> OptionReference:
    return OptionReference(kind="catalogue_id", value=catalogue_id)


def test_langgraph_lists_exactly_the_business_eligible_options(stage_b_db: str) -> None:
    async def scenario() -> None:
        async with business(stage_b_db) as services:
            ctx = await open_case(services, demo_subject("E-001"))
            port, llm = RecordingPort(services.tools), ScriptedLlm()
            runner = LangGraphTurnRunner(port, llm)  # type: ignore[arg-type]
            llm.interpret(interpretation(Intent.LIST_OPTIONS))
            outcome = await say(runner, ctx, "איזה רכבים מתאימים לי?")
            assert port.calls == ["get_conversation_context", "list_eligible_options"]
            state = await runner.conversation_state(str(ctx.conversation_id))
            listed = await services.tools.list_eligible_options(ctx, ListOptionsRequest())
            assert listed.data is not None
            eligible = [
                i.option_key
                for i in listed.data.items
                if i.eligibility is EligibilityStatus.ELIGIBLE
            ]
            assert [o["optionKey"] for o in state["displayed_options"]] == eligible
            assert "שכר" not in text_of(outcome) and not RAW_CODES.search(text_of(outcome))

    run(scenario())


def test_langgraph_asks_for_missing_salary_instead_of_calculating(stage_b_db: str) -> None:
    async def scenario() -> None:
        async with persistence(stage_b_db) as p:
            subject = await new_user(p)
            async with p.uow() as uow:
                await uow.profiles.add(profile(subject))
                await uow.commit()
        async with business(stage_b_db) as services:
            ctx = await open_case(services, subject)
            port, llm = RecordingPort(services.tools), ScriptedLlm()
            runner = LangGraphTurnRunner(port, llm)  # type: ignore[arg-type]
            llm.interpret(interpretation(Intent.CALCULATE_OPTIONS, option_refs=[car("CAR-10")]))
            outcome = await say(runner, ctx, "כמה נטו יישאר לי עם ATTO 3?")
            assert "check_calculation_readiness" in port.calls
            assert "compare_scenarios" not in port.calls
            assert of_kind(outcome, "question") and "₪" not in text_of(outcome)
            assert runs_of(stage_b_db, ctx) == 0

    run(scenario())


def test_langgraph_shows_engine_values_and_rejects_invented_numbers(stage_b_db: str) -> None:
    async def scenario() -> None:
        async with business(stage_b_db) as services:
            ctx = await open_case(services, demo_subject("E-001"))
            port, llm = RecordingPort(services.tools), ScriptedLlm()
            runner = LangGraphTurnRunner(port, llm)  # type: ignore[arg-type]
            llm.interpret(interpretation(Intent.CALCULATE_OPTIONS, option_refs=[car("CAR-10")]))
            llm.compose("לפי החישוב הנטו שלך יהיה 99999 ש״ח")  # an invented amount
            outcome = await say(runner, ctx, "כמה נטו יישאר לי עם ATTO 3?")
            assert port.calls[-2:] == ["check_calculation_readiness", "compare_scenarios"]
            card = of_kind(outcome, "comparison")[0]
            details = await services.tools.get_comparison_details(
                ctx, ComparisonDetailsRequest(run_id=UUID(card.payload["runId"]))
            )
            assert details.data is not None
            net = details.data.result.alternatives[0].net
            assert net is not None
            assert D(net.raw).quantize(D("0.001")) == D(SPEC_RAW["E-001"][1])
            assert shekels(net) in card.text  # the engine's display value, rendered by code
            assert "99999" not in text_of(outcome)  # ungrounded prose is discarded
            context = (await services.tools.get_conversation_context(ctx)).data
            assert context is not None and context.profile.base_gross.value is not None
            salary = str(int(D(context.profile.base_gross.value)))
            prompts = " ".join(
                t.system_instruction + " ".join(m.text for m in t.messages) for t in llm.tasks
            )
            assert salary not in prompts and str(ctx.case_id) not in prompts
            assert llm.calls(LlmTaskKind.INTERPRET) and runs_of(stage_b_db, ctx) == 1

    run(scenario())


def test_langgraph_hypothetical_turn_keeps_the_profile(stage_b_db: str) -> None:
    async def scenario() -> None:
        async with business(stage_b_db) as services:
            ctx = await open_case(services, demo_subject("E-004"))
            before = (await services.tools.get_conversation_context(ctx)).data
            assert before is not None
            port, llm = RecordingPort(services.tools), ScriptedLlm()
            runner = LangGraphTurnRunner(port, llm)  # type: ignore[arg-type]
            what_if = ProposedFact(
                field=FactField.BASE_GROSS,
                value="25000",
                mode=FactMode.HYPOTHETICAL,
                quote="נניח שהשכר שלי 25000",
            )
            llm.interpret(
                interpretation(Intent.UPDATE_FACTS, facts=[what_if], option_refs=[car("CAR-11")])
            )
            outcome = await say(runner, ctx, "נניח שהשכר שלי 25000, כמה יישאר עם EV3?")
            card = of_kind(outcome, "comparison")[0]
            details = await services.tools.get_comparison_details(
                ctx, ComparisonDetailsRequest(run_id=UUID(card.payload["runId"]))
            )
            assert details.data is not None and details.data.result.kind is RunKind.HYPOTHETICAL
            assert details.data.input_snapshot["factBasis"] == "case_copy_with_overrides"
            after = (await services.tools.get_conversation_context(ctx)).data
            assert after is not None and after.profile == before.profile

    run(scenario())


def test_langgraph_compare_to_current_without_confirmation_shows_no_delta(
    stage_b_db: str,
) -> None:
    async def scenario() -> None:
        async with business(stage_b_db) as services:
            ctx = await open_case(services, demo_subject("E-001"))
            port, llm = RecordingPort(services.tools), ScriptedLlm()
            runner = LangGraphTurnRunner(port, llm)  # type: ignore[arg-type]
            llm.interpret(interpretation(Intent.COMPARE_TO_CURRENT, option_refs=[car("CAR-10")]))
            outcome = await say(runner, ctx, "כמה אפסיד או ארוויח לעומת היום עם ATTO 3?")
            # Whether the workflow asks for confirmation first or shows alternatives only, a
            # difference against today must never be presented for an unconfirmed baseline.
            assert "לעומת היום)" not in text_of(outcome)
            for card in of_kind(outcome, "comparison"):
                details = await services.tools.get_comparison_details(
                    ctx, ComparisonDetailsRequest(run_id=UUID(card.payload["runId"]))
                )
                assert details.data is not None
                assert all(a.delta is None for a in details.data.result.alternatives)

    run(scenario())


def test_langgraph_replay_history_and_foreign_case(stage_b_db: str) -> None:
    async def scenario() -> None:
        async with business(stage_b_db) as services:
            ctx = await open_case(services, demo_subject("E-002"))
            port, llm = RecordingPort(services.tools), ScriptedLlm()
            runner = LangGraphTurnRunner(port, llm)  # type: ignore[arg-type]
            input_id = uuid4()
            llm.interpret(interpretation(Intent.CALCULATE_OPTIONS, option_refs=[car("CAR-05")]))
            first = await say(runner, ctx, "כמה נטו עם NIRO?", input_id)
            run_id = of_kind(first, "comparison")[0].payload["runId"]
            replay = await say(runner, ctx, "כמה נטו עם NIRO?", input_id)
            assert replay.status is TurnStatus.DUPLICATE and replay.tool_calls == 0
            llm.interpret(interpretation(Intent.SHOW_HISTORY))
            history = await say(runner, ctx, "תראה לי את החישוב הקודם")
            assert of_kind(history, "history")[0].payload["runId"] == run_id
            assert runs_of(stage_b_db, ctx) == 1
            other = await open_case(services, demo_subject("E-005"))
            foreign = ctx.model_copy(update={"case_id": other.case_id})
            refused = await say(LangGraphTurnRunner(services.tools, ScriptedLlm()), foreign, "?")
            assert of_kind(refused, "error") and not RAW_CODES.search(text_of(refused))

    run(scenario())
