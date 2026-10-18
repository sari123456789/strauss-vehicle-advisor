"""LangGraph implementation of the AI Application's `GraphRunner` port.

The checkpointer is injected (PostgreSQL `FencedPostgresSaver` in production, in-memory by
default for tests). A turn is checkpointed once, at its end (`durability="exit"`); the tool
context, the LLM port and all business data stay in the per-turn runtime context and are never
written to a checkpoint. An optional `fence` is installed for the run so that the checkpointer
checks the input claim before writing (check-then-write, not atomic; see
`app.ai.application.ports.checkpoints`).
"""

from typing import Any
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from app.ai.application.ports.checkpoints import CheckpointFence, current_checkpoint_fence
from app.ai.application.ports.graph_runner import (
    AssistantMessage,
    TurnInput,
    TurnOutcome,
    TurnStatus,
)
from app.ai.application.ports.llm import LlmPort
from app.ai.application.ports.response_staging import (
    MessageRef,
    ResponseStager,
    StagedMessage,
)
from app.ai.tools.gateway import DEFAULT_MAX_TOOL_CALLS, BusinessToolGateway
from app.ai.workflow.graph import CompiledConversationGraph, build_graph
from app.ai.workflow.state import ConversationState, TurnPayload, TurnRuntime
from app.contracts.business_port import BusinessToolsPort
from app.contracts.tool_context import ToolContext

DEFAULT_MAX_LLM_CALLS = 2
RECURSION_LIMIT = 25


class LangGraphTurnRunner:
    def __init__(
        self,
        port: BusinessToolsPort,
        llm: LlmPort,
        *,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
        max_llm_calls: int = DEFAULT_MAX_LLM_CALLS,
        compose_with_llm: bool = True,
    ) -> None:
        self._port = port
        self._llm = llm
        self._max_tool_calls = max_tool_calls
        self._max_llm_calls = max_llm_calls
        self._compose_with_llm = compose_with_llm
        self._graph: CompiledConversationGraph = build_graph().compile(
            checkpointer=checkpointer or InMemorySaver()
        )

    async def run_turn(
        self,
        *,
        thread_id: str,
        tool_context: ToolContext,
        turn: TurnInput,
        fence: CheckpointFence | None = None,
        stager: ResponseStager | None = None,
    ) -> TurnOutcome:
        runtime = TurnRuntime(
            tools=BusinessToolGateway(self._port, tool_context, self._max_tool_calls),
            llm=self._llm,
            max_llm_calls=self._max_llm_calls,
            compose_with_llm=self._compose_with_llm,
            stager=stager,
        )
        payload = TurnPayload(
            inputId=str(turn.input_id),
            kind=turn.kind.value,
            text=turn.text,
            requestId=str(turn.request_id) if turn.request_id else None,
            recentMessages=[{"role": m.role, "text": m.text} for m in turn.recent_messages],
        )
        initial: ConversationState = {"turn": payload}
        config: RunnableConfig = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": RECURSION_LIMIT,
        }
        token = current_checkpoint_fence.set(fence)
        try:
            final: dict[str, Any] = await self._graph.ainvoke(
                initial, config=config, context=runtime, durability="exit"
            )
        finally:
            current_checkpoint_fence.reset(token)
        snapshot = await self._graph.aget_state({"configurable": {"thread_id": thread_id}})
        checkpoint_id = _checkpoint_id(snapshot.config)
        parent_id = _checkpoint_id(snapshot.parent_config)
        tool_calls, llm_calls = len(runtime.tools.calls), runtime.llm_calls
        last = final.get("last_outbox")
        refs = tuple(
            MessageRef(r["messageId"], r["contentSha256"])
            for r in (last["messages"] if last and last["inputId"] == payload["inputId"] else [])
        )
        if final.get("route") == "duplicate":
            # The checkpoint holds references only; the text is published from staging.
            return TurnOutcome(
                TurnStatus.DUPLICATE, (), tool_calls, llm_calls, checkpoint_id, parent_id, refs
            )
        return TurnOutcome(
            TurnStatus.COMPLETED,
            _messages(runtime.staged),
            tool_calls,
            llm_calls,
            checkpoint_id,
            parent_id,
            refs,
        )

    async def conversation_state(self, thread_id: str) -> dict[str, Any]:
        """The checkpointed conversational memory of a thread (diagnostics and tests)."""
        snapshot = await self._graph.aget_state({"configurable": {"thread_id": thread_id}})
        return dict(snapshot.values)


def _checkpoint_id(config: RunnableConfig | None) -> str | None:
    if not config:
        return None
    value = config.get("configurable", {}).get("checkpoint_id")
    return None if value is None else str(value)


def _messages(staged: list[StagedMessage]) -> tuple[AssistantMessage, ...]:
    return tuple(
        AssistantMessage(
            message_id=UUID(m.message_id), text=m.text, kind=m.kind, payload=dict(m.payload)
        )
        for m in staged
    )
