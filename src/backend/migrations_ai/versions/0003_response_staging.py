"""Response staging: assistant messages staged under an input claim before the checkpoint.

Revision ID: 0003_response_staging
Revises: 0002_input_claims
Create Date: 2026-10-18

- `response_stagings`: one header per (input, claim attempt) with the message count and the
  manifest hash of the ordered message references the checkpoint records.
- `response_staging_messages`: the staged messages (conversation content kept only for durable
  recovery: no structured amount columns; Business remains authoritative).
- A DEFERRED constraint trigger rejects, at commit, any header whose messages are not exactly
  positions 0..count-1, so a partially staged response can never be committed.
- Both tables are append-only. Publication copies a matching staging into `messages`
  (`complete_input`); staged rows are kept (no retention/cleanup without approval).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0003_response_staging"
down_revision: str | None = "0002_input_claims"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "agent"
HASH = "~ '^[0-9a-f]{64}$'"
MAX_MESSAGES = 64


def upgrade() -> None:
    op.create_table(
        "response_stagings",
        sa.Column("input_id", UUID(as_uuid=True), nullable=False),
        sa.Column("claim_token", UUID(as_uuid=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("manifest_sha256", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("input_id", "claim_token", name="pk_response_stagings"),
        sa.ForeignKeyConstraint(
            ["input_id"],
            [f"{SCHEMA}.inputs.id"],
            name="fk_response_stagings_input_id_inputs",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("attempt >= 1", name=op.f("ck_response_stagings_attempt")),
        sa.CheckConstraint(
            f"message_count BETWEEN 0 AND {MAX_MESSAGES}",
            name=op.f("ck_response_stagings_message_count"),
        ),
        sa.CheckConstraint(
            f"manifest_sha256 {HASH}", name=op.f("ck_response_stagings_manifest_hash")
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_response_stagings_input_manifest",
        "response_stagings",
        ["input_id", "manifest_sha256"],
        schema=SCHEMA,
    )
    op.create_table(
        "response_staging_messages",
        sa.Column("input_id", UUID(as_uuid=True), nullable=False),
        sa.Column("claim_token", UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("message_id", UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "input_id", "claim_token", "position", name="pk_response_staging_messages"
        ),
        sa.ForeignKeyConstraint(
            ["input_id", "claim_token"],
            [f"{SCHEMA}.response_stagings.input_id", f"{SCHEMA}.response_stagings.claim_token"],
            name="fk_response_staging_messages_input_id_response_stagings",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "input_id",
            "claim_token",
            "message_id",
            name="uq_response_staging_messages_input_id_claim_token_message_id",
        ),
        sa.CheckConstraint("position >= 0", name=op.f("ck_response_staging_messages_position")),
        sa.CheckConstraint("role = 'assistant'", name=op.f("ck_response_staging_messages_role")),
        sa.CheckConstraint(
            "length(kind) > 0", name=op.f("ck_response_staging_messages_kind_not_blank")
        ),
        sa.CheckConstraint(
            "length(btrim(text)) > 0", name=op.f("ck_response_staging_messages_text_not_blank")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name=op.f("ck_response_staging_messages_payload_object"),
        ),
        sa.CheckConstraint(
            f"content_sha256 {HASH}", name=op.f("ck_response_staging_messages_content_hash")
        ),
        schema=SCHEMA,
    )
    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.check_response_staging_complete() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            found integer;
            highest integer;
        BEGIN
            SELECT count(*), max(position) INTO found, highest
            FROM {SCHEMA}.response_staging_messages
            WHERE input_id = NEW.input_id AND claim_token = NEW.claim_token;
            IF found <> NEW.message_count
               OR (found > 0 AND highest <> NEW.message_count - 1) THEN
                RAISE EXCEPTION 'incomplete response staging for input %', NEW.input_id
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        f"CREATE CONSTRAINT TRIGGER response_stagings_complete AFTER INSERT "
        f"ON {SCHEMA}.response_stagings DEFERRABLE INITIALLY DEFERRED "
        f"FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.check_response_staging_complete()"
    )
    for table in ("response_stagings", "response_staging_messages"):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {SCHEMA}.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.reject_history_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {SCHEMA}.{table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {SCHEMA}.reject_history_mutation()"
        )


def downgrade() -> None:
    op.drop_table("response_staging_messages", schema=SCHEMA)
    op.drop_table("response_stagings", schema=SCHEMA)
    op.execute(f"DROP FUNCTION {SCHEMA}.check_response_staging_complete()")
