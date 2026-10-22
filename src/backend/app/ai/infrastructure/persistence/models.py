"""AI-owned ORM models (schema `agent`). The migration in `migrations_ai/` is authoritative.

- Conversation ID and LangGraph thread ID are separate values (architecture §21).
- `case_id` has no foreign key to the Business schema (decision D3); the case is bound
  through `CaseLifecyclePort` with the stored deterministic `case_operation_id`.
- Messages and events are append-only (enforced by database triggers).
- Inputs are deduplicated by uniqueness constraints: a user message ID is unique per
  conversation, a representative-answered event ID is globally unique (decision D1).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "agent"

CONVERSATION_STATUSES = ("initializing", "active")
MESSAGE_ROLES = ("user", "assistant")
INPUT_SOURCES = ("user", "representative_answered")
INPUT_STATUSES = ("pending", "processing", "retry", "completed", "dead", "needs_reconciliation")
SIDE_EFFECT_PHASES = ("started", "confirmed", "failed")

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


CHECKPOINTER_TABLES = frozenset(
    {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}
)
"""Tables owned by `langgraph-checkpoint-postgres` (created by its `setup()` in `agent`)."""


def include_agent_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Alembic filter: never autogenerate (and so never drop) library-owned objects."""
    if type_ == "table" and name in CHECKPOINTER_TABLES:
        return False
    table = getattr(obj, "table", None)
    if (
        type_ in {"index", "unique_constraint", "column", "foreign_key_constraint"}
        and getattr(table, "name", None) in CHECKPOINTER_TABLES
    ):
        return False
    return True


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class AgentBase(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA, naming_convention=NAMING)


def _now() -> Any:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ConversationRow(AgentBase):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(_in("status", CONVERSATION_STATUSES), name="status"),
        CheckConstraint("status = 'initializing' OR case_id IS NOT NULL", name="active_has_case"),
        CheckConstraint("thread_id <> id", name="thread_differs"),
        CheckConstraint("calculation_month ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'", name="month"),
        CheckConstraint(
            "next_message_seq >= 1 AND next_input_seq >= 1 AND next_event_seq >= 1",
            name="sequences",
        ),
        UniqueConstraint("thread_id"),
        UniqueConstraint("case_id"),
        UniqueConstraint("case_operation_id"),
        Index("ix_conversations_owner_updated", "owner_subject_id", text("updated_at DESC")),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_subject_id: Mapped[UUID] = mapped_column(nullable=False)
    thread_id: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    calculation_month: Mapped[str] = mapped_column(Text, nullable=False)
    case_operation_id: Mapped[UUID] = mapped_column(nullable=False)
    case_id: Mapped[UUID | None] = mapped_column(nullable=True)
    next_message_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")
    next_input_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")
    next_event_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """Set when the owner deleted it from their list; the history itself is kept."""


class InputRow(AgentBase):
    __tablename__ = "inputs"
    __table_args__ = (
        CheckConstraint(_in("source", INPUT_SOURCES), name="source"),
        CheckConstraint(_in("status", INPUT_STATUSES), name="status"),
        CheckConstraint("attempts >= 0", name="attempts"),
        CheckConstraint("sequence >= 1", name="sequence"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
        CheckConstraint("payload_sha256 ~ '^[0-9a-f]{64}$'", name="payload_hash"),
        UniqueConstraint("conversation_id", "sequence"),
        UniqueConstraint("conversation_id", "source", "external_id"),
        Index(
            "uq_inputs_event_id",
            "external_id",
            unique=True,
            postgresql_where=text("source = 'representative_answered'"),
        ),
        Index("ix_inputs_status_next_attempt", "status", "next_attempt_at"),
        CheckConstraint(
            "status <> 'processing' OR (claim_token IS NOT NULL AND claimed_by IS NOT NULL "
            "AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="processing_claim",
        ),
        CheckConstraint(
            "status <> 'completed' OR "
            "(completed_at IS NOT NULL AND lineage_checked_at IS NOT NULL)",
            name="completed_lineage",
        ),
        CheckConstraint("status <> 'retry' OR next_attempt_at IS NOT NULL", name="retry_schedule"),
        CheckConstraint(
            "status <> 'needs_reconciliation' OR reconciliation_reason IS NOT NULL",
            name="reconciliation_reason",
        ),
        Index(
            "ix_inputs_open_by_conversation",
            "conversation_id",
            "sequence",
            postgresql_where=text("status NOT IN ('completed', 'dead')"),
        ),
        Index(
            "ix_inputs_processing_lease",
            "lease_expires_at",
            postgresql_where=text("status = 'processing'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[UUID] = mapped_column(nullable=False)
    """Client message ID (`user`) or business event ID (`representative_answered`)."""
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_ref: Mapped[str | None] = mapped_column(Text)
    last_error_code: Mapped[str | None] = mapped_column(Text)
    claim_token: Mapped[UUID | None] = mapped_column(nullable=True)
    """Fencing token of the current claim; every worker write is conditional on it."""
    claimed_by: Mapped[str | None] = mapped_column(Text)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    base_checkpoint_id: Mapped[str | None] = mapped_column(Text)
    lineage_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconciliation_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class MessageRow(AgentBase):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(_in("role", MESSAGE_ROLES), name="role"),
        CheckConstraint("length(btrim(text)) > 0", name="text_not_blank"),
        CheckConstraint("length(kind) > 0", name="kind_not_blank"),
        CheckConstraint("sequence >= 1", name="sequence"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
        UniqueConstraint("conversation_id", "sequence"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    input_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("inputs.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = _now()


class EventRow(AgentBase):
    __tablename__ = "events"
    __table_args__ = (
        PrimaryKeyConstraint("conversation_id", "sequence"),
        CheckConstraint("sequence >= 1", name="sequence"),
        CheckConstraint("length(event_type) > 0", name="type_not_blank"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
        UniqueConstraint("event_id"),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_id: Mapped[UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _now()


class SideEffectRow(AgentBase):
    """Append-only journal event about one logical Business operation of an input."""

    __tablename__ = "input_side_effects"
    __table_args__ = (
        CheckConstraint(_in("phase", SIDE_EFFECT_PHASES), name="phase"),
        CheckConstraint("attempt >= 1", name="attempt"),
        CheckConstraint("length(operation_key) > 0", name="key"),
        CheckConstraint("length(tool) > 0", name="tool"),
        CheckConstraint("request_sha256 ~ '^[0-9a-f]{64}$'", name="request_hash"),
        UniqueConstraint("input_id", "operation_key", "claim_token", "phase"),
        Index("ix_input_side_effects_input_operation", "input_id", "operation_key"),
    )

    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    journal_seq: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), nullable=False, unique=True
    )
    """Insertion order (events in one transaction share `recorded_at`)."""
    input_id: Mapped[UUID] = mapped_column(
        ForeignKey("inputs.id", ondelete="RESTRICT"), nullable=False
    )
    operation_key: Mapped[str] = mapped_column(Text, nullable=False)
    """Stable logical operation ID (tool + Business idempotency key), same in every attempt."""
    tool: Mapped[str] = mapped_column(Text, nullable=False)
    phase: Mapped[str] = mapped_column(Text, nullable=False)
    claim_token: Mapped[UUID] = mapped_column(nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    request_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    outcome_code: Mapped[str | None] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(
        "recorded_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ResponseStagingRow(AgentBase):
    """One staged response per (input, claim attempt); immutable."""

    __tablename__ = "response_stagings"
    __table_args__ = (
        PrimaryKeyConstraint("input_id", "claim_token"),
        CheckConstraint("attempt >= 1", name="attempt"),
        CheckConstraint("message_count BETWEEN 0 AND 64", name="message_count"),
        CheckConstraint("manifest_sha256 ~ '^[0-9a-f]{64}$'", name="manifest_hash"),
        Index("ix_response_stagings_input_manifest", "input_id", "manifest_sha256"),
    )

    input_id: Mapped[UUID] = mapped_column(
        ForeignKey("inputs.id", ondelete="RESTRICT"), nullable=False
    )
    claim_token: Mapped[UUID] = mapped_column(nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _now()


class ResponseStagingMessageRow(AgentBase):
    """A staged assistant message: conversation content for recovery, not Business data."""

    __tablename__ = "response_staging_messages"
    __table_args__ = (
        PrimaryKeyConstraint("input_id", "claim_token", "position"),
        ForeignKeyConstraint(
            ["input_id", "claim_token"],
            ["response_stagings.input_id", "response_stagings.claim_token"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("input_id", "claim_token", "message_id"),
        CheckConstraint("position >= 0", name="position"),
        CheckConstraint("role = 'assistant'", name="role"),
        CheckConstraint("length(kind) > 0", name="kind_not_blank"),
        CheckConstraint("length(btrim(text)) > 0", name="text_not_blank"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
        CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="content_hash"),
    )

    input_id: Mapped[UUID] = mapped_column(nullable=False)
    claim_token: Mapped[UUID] = mapped_column(nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    message_id: Mapped[UUID] = mapped_column(nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(Text, nullable=False)
