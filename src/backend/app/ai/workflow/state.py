"""Graph state (checkpointed) and per-turn runtime context (never checkpointed).

`ConversationState` holds only durable conversational memory: the active goal, the options as
displayed, pending representative work and processed input IDs. It holds no salary values, no
authoritative business data and no identifiers chosen by the model.

`TurnRuntime` is passed as LangGraph runtime context for one turn. It carries the server-bound
tool gateway, the `LlmPort` and per-turn scratch data (the loaded case context, tool results,
replies), so business data and credentials never enter a checkpoint.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, NotRequired, TypedDict
from uuid import NAMESPACE_URL, UUID, uuid5

from app.ai.application.ports.llm import (
    LlmOutcome,
    LlmPort,
    LlmResult,
    LlmTask,
    OutputT,
)
from app.ai.application.ports.response_staging import ResponseStager, StagedMessage
from app.ai.tools.gateway import BusinessToolGateway
from app.ai.workflow.extraction import ExtractionOutcome
from app.ai.workflow.models import TurnInterpretation
from app.contracts.cases import CaseContext
from app.contracts.common import UpdateMode

ID_NAMESPACE = uuid5(NAMESPACE_URL, "https://strauss-vehicle-advisor/ai-engine/v1")
MAX_PROCESSED_INPUTS = 100


def stable_id(*parts: object) -> UUID:
    """Deterministic UUID: replaying the same input yields the same operation/message IDs."""
    return uuid5(ID_NAMESPACE, ":".join(str(p) for p in parts))


class TurnPayload(TypedDict):
    inputId: str
    kind: Literal["user_message", "representative_answered"]
    text: NotRequired[str | None]
    requestId: NotRequired[str | None]
    recentMessages: NotRequired[list[dict[str, str]]]


class DisplayedOption(TypedDict):
    optionKey: str
    label: str


class ActiveGoal(TypedDict):
    intent: str
    includeCurrent: bool
    kind: Literal["regular", "hypothetical"]
    optionKeys: NotRequired[list[str]]
    """The options the employee explicitly selected (absent for list/all-option goals and in
    goals checkpointed before this field existed). A resumed goal compares exactly these."""
    includeWaiver: NotRequired[bool]
    """The goal includes the waiver option; a resumed goal keeps it (e.g. after "yes")."""
    propulsion: NotRequired[list[str]]
    """Drive-type filter (`Propulsion` values) of an all-option goal; kept on resume."""


class PendingTask(TypedDict):
    fieldCode: str
    optionKey: str | None
    includeCurrent: bool
    kind: Literal["regular", "hypothetical"]


class OutboxRef(TypedDict):
    """Checkpointed reference to a staged message: never its text (which may hold amounts)."""

    messageId: str
    contentSha256: str


class LastOutbox(TypedDict):
    inputId: str
    messages: list[OutboxRef]


class OccupationCandidateRef(TypedDict):
    """An official occupation offered for selection (ID and official name only)."""

    occupationId: str
    label: str


class ConversationState(TypedDict, total=False):
    turn: TurnPayload | None
    route: str
    active_goal: ActiveGoal | None
    displayed_options: list[DisplayedOption]
    result_set_id: str | None
    pending_tasks: dict[str, PendingTask]
    cancelled_request_ids: list[str]
    unknown_fields: list[str]
    asked_counts: dict[str, int]
    processed_input_ids: list[str]
    latest_run_id: str | None
    last_outbox: LastOutbox | None
    awaiting_confirmation: list[str]
    """Saved facts (`FieldPath` values) the advisor asked the employee to confirm."""
    salary_confirmation_asked: bool
    """The one-per-conversation confirmation of saved salary facts was asked; never again."""
    saved_facts_noted: list[str]
    """Saved salary facts already named in a "calculated with saved facts" note (no repeats)."""
    occupation_candidates: list[OccupationCandidateRef]
    """Official occupations shown for an ambiguous description, awaiting a choice."""
    offered_question: str | None
    """A general question the advisor offered to send to the representative (awaiting yes)."""
    representative_help_shown: bool
    """The last reply explained what the representative can help with (prevents a repeat)."""
    disclaimer_shown: bool
    """The full estimate disclaimer was shown once; later results show a short reminder."""
    option_labels: dict[str, str]
    """Labels of options the employee named that were resolved from the catalogue."""


@dataclass
class Reply:
    text: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ComposeRequest:
    kind: Literal["comparison", "general"]
    question: str
    values: dict[str, str] = field(default_factory=dict)
    descriptions: dict[str, str] = field(default_factory=dict)
    fallback: str | None = None
    recent: list[dict[str, str]] = field(default_factory=list)
    """Recent conversation for general answers, with digits masked (never salary values)."""


@dataclass
class TurnRuntime:
    tools: BusinessToolGateway
    llm: LlmPort
    max_llm_calls: int = 3
    compose_with_llm: bool = True
    llm_calls: int = 0
    case: CaseContext | None = None
    case_version: int = 0
    profile_version: int = 0
    interpretation: TurnInterpretation | None = None
    extraction: ExtractionOutcome | None = None
    applied: dict[UpdateMode, list[str]] = field(default_factory=dict)
    confirmed: list[str] = field(default_factory=list)
    """Saved facts confirmed in this turn through `confirm_profile_fields`."""
    answered_confirmation: bool = False
    """This turn corrected a saved fact the advisor had asked to confirm (resume the goal)."""
    implicit_confirmations: list[str] = field(default_factory=list)
    """Saved facts the employee restated with the same value (e.g. their saved current car):
    confirmed silently instead of being written again."""
    vehicle_question: str | None = None
    """A current car mentioned in this turn could not be identified: asked after the results,
    which then show no difference from today (never a delta against a car they may not have)."""
    halt: bool = False
    """Stop acting on this turn (an error or clarification has already been replied)."""
    replies: list[Reply] = field(default_factory=list)
    compose: ComposeRequest | None = None
    labels: dict[str, str] = field(default_factory=dict)
    """Display labels of options resolved from the catalogue in this turn."""
    stager: ResponseStager | None = None
    """Durable staging under the input claim (None: in-memory runs and unit tests)."""
    staged: list[StagedMessage] = field(default_factory=list)
    """This run's response (set by `finalize`)."""

    def reply(self, text: str, kind: str, **payload: Any) -> None:
        self.replies.append(Reply(text, kind, dict(payload)))

    async def call_llm(self, task: LlmTask[OutputT]) -> LlmResult[OutputT]:
        """At most `max_llm_calls` model calls per turn (architecture §15: 3)."""
        if self.llm_calls >= self.max_llm_calls:
            return LlmResult(LlmOutcome.BLOCKED_BY_POLICY, diagnostic_code="turn_llm_budget")
        self.llm_calls += 1
        return await self.llm.generate(task)
