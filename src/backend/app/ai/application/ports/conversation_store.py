"""Conversation persistence port (AI Application). STATUS: AI-internal.

The AI Application depends only on these Protocols and records; the PostgreSQL implementation
lives in `app.ai.infrastructure.persistence.store`. A `ConversationTransactions.transaction()`
is one short AI-only transaction: never await an LLM or a Business call inside it.
"""

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from app.ai.application.ports.response_staging import MessageRef, StagedMessage

USER = "user"
REPRESENTATIVE_ANSWERED = "representative_answered"

PENDING = "pending"
PROCESSING = "processing"
RETRY = "retry"
COMPLETED = "completed"
DEAD = "dead"
NEEDS_RECONCILIATION = "needs_reconciliation"
FINAL_STATUSES = frozenset({COMPLETED, DEAD})

MAX_ATTEMPTS = 10
RETRY_BACKOFF_SECONDS = (5, 30, 120, 600, 1800, 3600)
"""Delay after attempt n (1-based); the last value repeats (architecture §18: up to 1 hour)."""


def retry_delay_seconds(attempt: int) -> int:
    return RETRY_BACKOFF_SECONDS[min(max(attempt, 1), len(RETRY_BACKOFF_SECONDS)) - 1]


class PersistenceConflictError(Exception):
    """A stable ID was reused with different content; nothing was written."""


class ConversationConflictError(PersistenceConflictError):
    pass


class CaseBindingConflictError(PersistenceConflictError):
    pass


class InputPayloadMismatchError(PersistenceConflictError):
    pass


class MessageMismatchError(PersistenceConflictError):
    pass


class EventMismatchError(PersistenceConflictError):
    pass


class ConversationNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class NewConversation:
    conversation_id: UUID
    owner_subject_id: UUID
    thread_id: UUID
    calculation_month: str
    case_operation_id: UUID


@dataclass(frozen=True)
class ConversationRecord:
    conversation_id: UUID
    owner_subject_id: UUID
    thread_id: UUID
    status: str
    calculation_month: str
    case_operation_id: UUID
    case_id: UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class NewInput:
    input_id: UUID
    conversation_id: UUID
    source: str
    external_id: UUID
    payload: dict[str, Any]


@dataclass(frozen=True)
class InputRecord:
    input_id: UUID
    conversation_id: UUID
    sequence: int
    source: str
    external_id: UUID
    payload: dict[str, Any]
    status: str
    attempts: int
    next_attempt_at: datetime | None = None
    last_error_code: str | None = None
    reconciliation_reason: str | None = None
    execution_ref: str | None = None
    """Checkpoint ID recorded when the input was completed."""


@dataclass(frozen=True)
class NewMessage:
    message_id: UUID
    conversation_id: UUID
    role: str
    kind: str
    text: str
    payload: dict[str, Any] = field(default_factory=dict)
    input_id: UUID | None = None


@dataclass(frozen=True)
class MessageRecord:
    message_id: UUID
    conversation_id: UUID
    sequence: int
    role: str
    kind: str
    text: str
    payload: dict[str, Any]
    input_id: UUID | None
    created_at: datetime


@dataclass(frozen=True)
class NewEvent:
    event_id: UUID
    conversation_id: UUID
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EventRecord:
    event_id: UUID
    conversation_id: UUID
    sequence: int
    event_type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class Written[T]:
    record: T
    created: bool
    """False when an identical write was replayed and the stored record was returned."""


class StaleClaimError(Exception):
    """The claim token no longer owns the input (taken over, finished or expired lease).
    The caller must abandon the run without writing anything else."""


class StagingConflictError(Exception):
    """A response was staged again for the same claim with different content."""


STAGED_PAYLOAD_KEYS = frozenset(
    {
        "code",
        "fields",
        "mode",
        "optionKeys",
        "outcome",
        "requestId",
        "resultSetId",
        "retryAfterSeconds",
        "runId",
        "stale",
    }
)
"""Allowed keys of a staged/published message payload: identifiers, codes and field names only.
Amounts appear only in the message text (conversation content), never as structured data."""


class SideEffectConflictError(Exception):
    """A journal event contradicts the journal (changed request/tool, missing `started`,
    second outcome in one attempt)."""


@dataclass(frozen=True)
class InputClaim:
    """Exclusive, fenced ownership of one input for one attempt."""

    input: InputRecord
    claim_token: UUID
    attempt: int
    claimed_by: str
    lease_expires_at: datetime
    thread_id: UUID
    owner_subject_id: UUID
    expected_base_checkpoint_id: str | None
    """Checkpoint recorded by the conversation's latest completed input (None: no history)."""


@dataclass(frozen=True)
class ExpiredClaim:
    input_id: UUID
    claim_token: UUID
    thread_id: UUID
    lease_expires_at: datetime


@dataclass(frozen=True)
class LineageObservation:
    """What the checkpointer reports for the thread right after the claim."""

    latest_checkpoint_id: str | None
    latest_parent_checkpoint_id: str | None
    latest_contains_input: bool
    """The latest checkpoint already records this input as processed."""


class LineageResult(StrEnum):
    FRESH = "fresh"
    """Latest checkpoint is the expected one: run the graph."""
    ALREADY_EXECUTED = "already_executed"
    """Latest checkpoint is a child of the expected one and already contains this input."""
    MISMATCH = "mismatch"
    """Unverifiable lineage: the input was moved to `needs_reconciliation`."""


class SideEffectPhase(StrEnum):
    STARTED = "started"
    CONFIRMED = "confirmed"
    FAILED = "failed"


@dataclass(frozen=True)
class NewSideEffectEvent:
    event_id: UUID
    operation_key: str
    """Stable logical operation ID (tool + Business idempotency key), same in every attempt."""
    tool: str
    phase: SideEffectPhase
    request_sha256: str
    outcome_code: str | None = None


@dataclass(frozen=True)
class SideEffectEvent:
    event_id: UUID
    input_id: UUID
    operation_key: str
    tool: str
    phase: str
    claim_token: UUID
    attempt: int
    request_sha256: str
    outcome_code: str | None


@dataclass(frozen=True)
class FailureDecision:
    status: str
    """`retry`, `dead` or `needs_reconciliation`."""
    next_attempt_at: datetime | None
    reason: str | None


@runtime_checkable
class ConversationStore(Protocol):
    async def create_conversation(self, new: NewConversation) -> Written[ConversationRecord]: ...

    async def get_conversation(self, conversation_id: UUID) -> ConversationRecord | None: ...

    async def get_owned_conversation(
        self, conversation_id: UUID, owner_subject_id: UUID
    ) -> ConversationRecord | None: ...

    async def get_conversation_by_case(self, case_id: UUID) -> ConversationRecord | None: ...

    async def list_owned_conversations(
        self, owner_subject_id: UUID, *, limit: int = 50
    ) -> list[ConversationRecord]: ...

    async def hide_conversation(self, conversation_id: UUID) -> None:
        """Leave it out of `list_owned_conversations`; the conversation itself is kept."""
        ...

    async def bind_case(
        self, conversation_id: UUID, case_id: UUID
    ) -> Written[ConversationRecord]: ...

    async def enqueue_input(self, new: NewInput) -> Written[InputRecord]: ...

    async def get_input(self, input_id: UUID) -> InputRecord | None: ...

    async def append_message(self, new: NewMessage) -> Written[MessageRecord]: ...

    async def list_messages(
        self, conversation_id: UUID, *, after_sequence: int = 0, limit: int = 200
    ) -> list[MessageRecord]: ...

    async def append_event(self, new: NewEvent) -> Written[EventRecord]: ...

    async def list_events(
        self, conversation_id: UUID, *, after_sequence: int = 0, limit: int = 200
    ) -> list[EventRecord]: ...

    # --- input claims (fenced by `InputClaim.claim_token`) ---------------------------------

    async def claim_next_input(
        self, worker_id: str, *, lease_seconds: int
    ) -> InputClaim | None: ...

    async def list_expired_claims(self, *, limit: int = 20) -> list[ExpiredClaim]: ...

    async def take_over_expired_claim(
        self, expired: ExpiredClaim, worker_id: str, *, lease_seconds: int
    ) -> InputClaim | None: ...

    async def renew_lease(self, claim: InputClaim, *, lease_seconds: int) -> datetime: ...

    async def is_claim_live(self, claim: InputClaim) -> bool: ...

    async def bind_lineage(
        self, claim: InputClaim, observation: LineageObservation
    ) -> LineageResult: ...

    async def record_side_effect(
        self, claim: InputClaim, event: NewSideEffectEvent
    ) -> Written[SideEffectEvent]: ...

    async def list_side_effects(self, input_id: UUID) -> list[SideEffectEvent]: ...

    async def stage_response(
        self, claim: InputClaim, messages: Sequence[StagedMessage]
    ) -> bool: ...

    async def complete_input(
        self,
        claim: InputClaim,
        *,
        checkpoint_id: str,
        parent_checkpoint_id: str | None,
        message_refs: Sequence[MessageRef],
        events: Sequence[NewEvent] = (),
    ) -> str: ...

    async def fail_input(
        self,
        claim: InputClaim,
        *,
        error_code: str,
        replay_safe_tools: frozenset[str],
        max_attempts: int = MAX_ATTEMPTS,
    ) -> FailureDecision: ...

    async def mark_needs_reconciliation(self, claim: InputClaim, *, reason: str) -> None: ...


@runtime_checkable
class ConversationTransactions(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[ConversationStore]:
        """Commit on success, roll back on any exception."""
        ...
