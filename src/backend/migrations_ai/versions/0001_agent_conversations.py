"""Agent schema: conversations, inputs, messages, events.

Revision ID: 0001_agent_conversations
Revises:
Create Date: 2026-10-18

- `conversations`: conversation ID and LangGraph `thread_id` are distinct; `case_id` has no
  foreign key to the Business schema (decision D3) and is bound once, when the conversation
  moves from `initializing` to `active`. `case_operation_id` is the stable idempotency key
  for `CaseLifecyclePort` retries. Per-conversation sequence counters serialize appends.
- `inputs`: durable queue. A user message ID is unique per conversation; a
  representative-answered event ID is globally unique (decision D1). `payload_sha256`
  detects same-ID/different-payload reuse.
- `messages` and `events` are append-only (UPDATE/DELETE/TRUNCATE rejected).
- Conversations and inputs reject DELETE/TRUNCATE: no automatic deletion in the demo.
- LangGraph checkpointer tables are not created here (managed by the library, P2c).
The schema itself is created by `env.py` (it holds the version table) and is never dropped.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0001_agent_conversations"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "agent"
APPEND_ONLY = ("messages", "events")
NO_DELETE = ("conversations", "inputs")


def _ts(name: str, *, nullable: bool = False, default: bool = True) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if default else None,
    )


def _jsonb(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, JSONB(), nullable=False)


def _uuid(name: str, *, nullable: bool = False) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, UUID(as_uuid=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "conversations",
        _uuid("id"),
        _uuid("owner_subject_id"),
        _uuid("thread_id"),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("calculation_month", sa.Text(), nullable=False),
        _uuid("case_operation_id"),
        _uuid("case_id", nullable=True),
        sa.Column("next_message_seq", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("next_input_seq", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("next_event_seq", sa.BigInteger(), nullable=False, server_default="1"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
        sa.CheckConstraint(
            "status IN ('initializing', 'active')", name=op.f("ck_conversations_status")
        ),
        sa.CheckConstraint(
            "status = 'initializing' OR case_id IS NOT NULL",
            name=op.f("ck_conversations_active_has_case"),
        ),
        sa.CheckConstraint("thread_id <> id", name=op.f("ck_conversations_thread_differs")),
        sa.CheckConstraint(
            "calculation_month ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'", name=op.f("ck_conversations_month")
        ),
        sa.CheckConstraint(
            "next_message_seq >= 1 AND next_input_seq >= 1 AND next_event_seq >= 1",
            name=op.f("ck_conversations_sequences"),
        ),
        sa.UniqueConstraint("thread_id", name="uq_conversations_thread_id"),
        sa.UniqueConstraint("case_id", name="uq_conversations_case_id"),
        sa.UniqueConstraint("case_operation_id", name="uq_conversations_case_operation_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_conversations_owner_updated",
        "conversations",
        ["owner_subject_id", sa.text("updated_at DESC")],
        schema=SCHEMA,
    )

    op.create_table(
        "inputs",
        _uuid("id"),
        _uuid("conversation_id"),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        _uuid("external_id"),
        _jsonb("payload"),
        sa.Column("payload_sha256", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        _ts("next_attempt_at", nullable=True, default=False),
        sa.Column("execution_ref", sa.Text(), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_inputs"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            [f"{SCHEMA}.conversations.id"],
            name="fk_inputs_conversation_id_conversations",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "source IN ('user', 'representative_answered')", name=op.f("ck_inputs_source")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name=op.f("ck_inputs_status"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_inputs_attempts")),
        sa.CheckConstraint("sequence >= 1", name=op.f("ck_inputs_sequence")),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name=op.f("ck_inputs_payload_object")
        ),
        sa.CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_inputs_payload_hash")
        ),
        sa.UniqueConstraint(
            "conversation_id", "sequence", name="uq_inputs_conversation_id_sequence"
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "source",
            "external_id",
            name="uq_inputs_conversation_id_source_external_id",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_inputs_event_id",
        "inputs",
        ["external_id"],
        unique=True,
        postgresql_where=sa.text("source = 'representative_answered'"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_inputs_status_next_attempt", "inputs", ["status", "next_attempt_at"], schema=SCHEMA
    )

    op.create_table(
        "messages",
        _uuid("id"),
        _uuid("conversation_id"),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        _jsonb("payload"),
        _uuid("input_id", nullable=True),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            [f"{SCHEMA}.conversations.id"],
            name="fk_messages_conversation_id_conversations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["input_id"],
            [f"{SCHEMA}.inputs.id"],
            name="fk_messages_input_id_inputs",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("role IN ('user', 'assistant')", name=op.f("ck_messages_role")),
        sa.CheckConstraint("length(btrim(text)) > 0", name=op.f("ck_messages_text_not_blank")),
        sa.CheckConstraint("length(kind) > 0", name=op.f("ck_messages_kind_not_blank")),
        sa.CheckConstraint("sequence >= 1", name=op.f("ck_messages_sequence")),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name=op.f("ck_messages_payload_object")
        ),
        sa.UniqueConstraint(
            "conversation_id", "sequence", name="uq_messages_conversation_id_sequence"
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_messages_input_id", "messages", ["input_id"], schema=SCHEMA)

    op.create_table(
        "events",
        _uuid("conversation_id"),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        _uuid("event_id"),
        sa.Column("event_type", sa.Text(), nullable=False),
        _jsonb("payload"),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("conversation_id", "sequence", name="pk_events"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            [f"{SCHEMA}.conversations.id"],
            name="fk_events_conversation_id_conversations",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("sequence >= 1", name=op.f("ck_events_sequence")),
        sa.CheckConstraint("length(event_type) > 0", name=op.f("ck_events_type_not_blank")),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name=op.f("ck_events_payload_object")
        ),
        sa.UniqueConstraint("event_id", name="uq_events_event_id"),
        schema=SCHEMA,
    )

    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.reject_history_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'conversation history is preserved: % on %.% is not allowed',
                TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    for table in APPEND_ONLY:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {SCHEMA}.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.reject_history_mutation()"
        )
    for table in NO_DELETE:
        op.execute(
            f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {SCHEMA}.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.reject_history_mutation()"
        )
    for table in (*APPEND_ONLY, *NO_DELETE):
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {SCHEMA}.{table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {SCHEMA}.reject_history_mutation()"
        )


def downgrade() -> None:
    for table in ("events", "messages", "inputs", "conversations"):
        op.drop_table(table, schema=SCHEMA)
    op.execute(f"DROP FUNCTION {SCHEMA}.reject_history_mutation()")
