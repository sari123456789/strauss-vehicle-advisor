"""The conversation graph.

    ingest -> load_context -> interpret -> prepare -> apply_updates -> apply_confirmations
           -> act -> compose -> finalize
                          \\-> handle_answer -> finalize          (representative answers)

Every input starts a fresh, bounded run from START on the conversation's thread; there is no
`interrupt()`: pending representative work is recorded in state and only the dependent option
waits, so the conversation is never paused. The graph is acyclic, so a turn always terminates.
"""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.ai.workflow.nodes.act import act, handle_answer
from app.ai.workflow.nodes.respond import compose, finalize
from app.ai.workflow.nodes.understand import (
    apply_confirmations,
    apply_updates,
    ingest,
    interpret,
    load_context,
    prepare,
)
from app.ai.workflow.state import ConversationState, TurnRuntime


def _after_ingest(state: ConversationState) -> str:
    return END if state.get("route") == "duplicate" else "load_context"


def _after_context(state: ConversationState) -> str:
    route = state.get("route")
    if route == "stop":
        return "finalize"
    return "handle_answer" if route == "answer" else "interpret"


def _after_interpret(state: ConversationState) -> str:
    return "finalize" if state.get("route") == "stop" else "prepare"


def build_graph() -> StateGraph[
    ConversationState, TurnRuntime, ConversationState, ConversationState
]:
    graph = StateGraph(ConversationState, context_schema=TurnRuntime)
    graph.add_node("ingest", ingest)
    graph.add_node("load_context", load_context)
    graph.add_node("interpret", interpret)
    graph.add_node("prepare", prepare)
    graph.add_node("apply_updates", apply_updates)
    graph.add_node("apply_confirmations", apply_confirmations)
    graph.add_node("act", act)
    graph.add_node("handle_answer", handle_answer)
    graph.add_node("compose", compose)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "ingest")
    graph.add_conditional_edges("ingest", _after_ingest, ["load_context", END])
    graph.add_conditional_edges(
        "load_context", _after_context, ["interpret", "handle_answer", "finalize"]
    )
    graph.add_conditional_edges("interpret", _after_interpret, ["prepare", "finalize"])
    graph.add_edge("prepare", "apply_updates")
    graph.add_edge("apply_updates", "apply_confirmations")
    graph.add_edge("apply_confirmations", "act")
    graph.add_edge("act", "compose")
    graph.add_edge("handle_answer", "finalize")
    graph.add_edge("compose", "finalize")
    graph.add_edge("finalize", END)
    return graph


CompiledConversationGraph = CompiledStateGraph[
    ConversationState, TurnRuntime, ConversationState, ConversationState
]
