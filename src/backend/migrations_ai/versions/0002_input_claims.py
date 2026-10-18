"""Input claims: fencing tokens, leases, explicit status transitions, side-effect journal.

Revision ID: 0002_input_claims
Revises: 0001_agent_conversations
Create Date: 2026-10-18

- Statuses: pending, processing, retry, completed, dead, needs_reconciliation (`failed`, never
  used, is removed). A trigger enforces the transition table; `completed` and `dead` are final.
- A `processing` row always has a claim token, a holder and a lease. Every worker write is
  conditional on the token (fencing), so a stale worker cannot commit.
- Lineage: `base_checkpoint_id`/`lineage_checked_at` are bound after the claim; an input can be
  `completed` only with a checked lineage (otherwise it goes to `needs_reconciliation`).
- `inputs.payload`/`payload_sha256` become immutable.
- `input_side_effects`: append-only journal. Each row is one event (own `event_id`) about a
  logical operation (`operation_key`, stable across attempts) in one attempt (`claim_token`):
  `started`, then at most one `confirmed` or `failed`. The request hash of an operation never
  changes across attempts.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0002_input_claims"
down_revision: str | None = "0001_agent_conversations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "agent"
STATUSES = "('pending', 'processing', 'retry', 'completed', 'dead', 'needs_reconciliation')"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_inputs_status"), "inputs", schema=SCHEMA)
    op.execute(f"UPDATE {SCHEMA}.inputs SET status = 'dead' WHERE status = 'failed'")
    for column in (
        sa.Column("claim_token", UUID(as_uuid=True), nullable=True),
        sa.Column("claimed_by", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("base_checkpoint_id", sa.Text(), nullable=True),
        sa.Column("lineage_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconciliation_reason", sa.Text(), nullable=True),
    ):
        op.add_column("inputs", column, schema=SCHEMA)
    op.create_check_constraint(
        op.f("ck_inputs_status"), "inputs", f"status IN {STATUSES}", schema=SCHEMA
    )
    op.create_check_constraint(
        op.f("ck_inputs_processing_claim"),
        "inputs",
        "status <> 'processing' OR (claim_token IS NOT NULL AND claimed_by IS NOT NULL "
        "AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL)",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_inputs_completed_lineage"),
        "inputs",
        "status <> 'completed' OR (completed_at IS NOT NULL AND lineage_checked_at IS NOT NULL)",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_inputs_retry_schedule"),
        "inputs",
        "status <> 'retry' OR next_attempt_at IS NOT NULL",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_inputs_reconciliation_reason"),
        "inputs",
        "status <> 'needs_reconciliation' OR reconciliation_reason IS NOT NULL",
        schema=SCHEMA,
    )
    op.create_index(
        "ix_inputs_open_by_conversation",
        "inputs",
        ["conversation_id", "sequence"],
        postgresql_where=sa.text("status NOT IN ('completed', 'dead')"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_inputs_processing_lease",
        "inputs",
        ["lease_expires_at"],
        postgresql_where=sa.text("status = 'processing'"),
        schema=SCHEMA,
    )
    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.check_input_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.payload IS DISTINCT FROM OLD.payload
               OR NEW.payload_sha256 IS DISTINCT FROM OLD.payload_sha256
               OR NEW.id IS DISTINCT FROM OLD.id
               OR NEW.conversation_id IS DISTINCT FROM OLD.conversation_id
               OR NEW.sequence IS DISTINCT FROM OLD.sequence
               OR NEW.source IS DISTINCT FROM OLD.source
               OR NEW.external_id IS DISTINCT FROM OLD.external_id THEN
                RAISE EXCEPTION 'input identity and payload are immutable'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.status IN ('completed', 'dead') THEN
                RAISE EXCEPTION 'input in final status % cannot change', OLD.status
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.attempts < OLD.attempts THEN
                RAISE EXCEPTION 'input attempts cannot decrease'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
                (OLD.status IN ('pending', 'retry') AND NEW.status = 'processing')
                OR (OLD.status = 'processing' AND NEW.status IN
                    ('completed', 'retry', 'dead', 'needs_reconciliation'))
                OR (OLD.status = 'needs_reconciliation' AND NEW.status IN
                    ('retry', 'completed', 'dead'))
            ) THEN
                RAISE EXCEPTION 'input status transition % -> % is not allowed',
                    OLD.status, NEW.status USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        f"CREATE TRIGGER inputs_check_update BEFORE UPDATE ON {SCHEMA}.inputs "
        f"FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.check_input_update()"
    )

    op.create_table(
        "input_side_effects",
        sa.Column("event_id", UUID(as_uuid=True), nullable=False),
        sa.Column("journal_seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("input_id", UUID(as_uuid=True), nullable=False),
        sa.Column("operation_key", sa.Text(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("phase", sa.Text(), nullable=False),
        sa.Column("claim_token", UUID(as_uuid=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("request_sha256", sa.Text(), nullable=False),
        sa.Column("outcome_code", sa.Text(), nullable=True),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_input_side_effects"),
        sa.ForeignKeyConstraint(
            ["input_id"],
            [f"{SCHEMA}.inputs.id"],
            name="fk_input_side_effects_input_id_inputs",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "phase IN ('started', 'confirmed', 'failed')", name=op.f("ck_input_side_effects_phase")
        ),
        sa.CheckConstraint("attempt >= 1", name=op.f("ck_input_side_effects_attempt")),
        sa.CheckConstraint("length(operation_key) > 0", name=op.f("ck_input_side_effects_key")),
        sa.CheckConstraint("length(tool) > 0", name=op.f("ck_input_side_effects_tool")),
        sa.CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_input_side_effects_request_hash")
        ),
        sa.UniqueConstraint("journal_seq", name="uq_input_side_effects_journal_seq"),
        sa.UniqueConstraint(
            "input_id",
            "operation_key",
            "claim_token",
            "phase",
            name="uq_input_side_effects_input_id_operation_key_claim_token_phase",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_input_side_effects_input_operation",
        "input_side_effects",
        ["input_id", "operation_key"],
        schema=SCHEMA,
    )
    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.check_side_effect_insert() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            started_count integer;
        BEGIN
            IF EXISTS (
                SELECT 1 FROM {SCHEMA}.input_side_effects
                WHERE input_id = NEW.input_id AND operation_key = NEW.operation_key
                  AND (request_sha256 <> NEW.request_sha256 OR tool <> NEW.tool)
            ) THEN
                RAISE EXCEPTION 'operation % changed its request or tool', NEW.operation_key
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.phase IN ('confirmed', 'failed') THEN
                SELECT count(*) INTO started_count FROM {SCHEMA}.input_side_effects
                WHERE input_id = NEW.input_id AND operation_key = NEW.operation_key
                  AND claim_token = NEW.claim_token AND phase = 'started';
                IF started_count = 0 THEN
                    RAISE EXCEPTION 'operation % has no started event in this attempt',
                        NEW.operation_key USING ERRCODE = 'restrict_violation';
                END IF;
                IF EXISTS (
                    SELECT 1 FROM {SCHEMA}.input_side_effects
                    WHERE input_id = NEW.input_id AND operation_key = NEW.operation_key
                      AND claim_token = NEW.claim_token AND phase IN ('confirmed', 'failed')
                ) THEN
                    RAISE EXCEPTION 'operation % already has an outcome in this attempt',
                        NEW.operation_key USING ERRCODE = 'restrict_violation';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        f"CREATE TRIGGER input_side_effects_check_insert BEFORE INSERT "
        f"ON {SCHEMA}.input_side_effects "
        f"FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.check_side_effect_insert()"
    )
    op.execute(
        f"CREATE TRIGGER input_side_effects_append_only BEFORE UPDATE OR DELETE "
        f"ON {SCHEMA}.input_side_effects "
        f"FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.reject_history_mutation()"
    )
    op.execute(
        f"CREATE TRIGGER input_side_effects_no_truncate BEFORE TRUNCATE "
        f"ON {SCHEMA}.input_side_effects "
        f"FOR EACH STATEMENT EXECUTE FUNCTION {SCHEMA}.reject_history_mutation()"
    )


def downgrade() -> None:
    op.drop_table("input_side_effects", schema=SCHEMA)
    op.execute(f"DROP FUNCTION {SCHEMA}.check_side_effect_insert()")
    op.execute(f"DROP TRIGGER inputs_check_update ON {SCHEMA}.inputs")
    op.execute(f"DROP FUNCTION {SCHEMA}.check_input_update()")
    op.drop_index("ix_inputs_processing_lease", "inputs", schema=SCHEMA)
    op.drop_index("ix_inputs_open_by_conversation", "inputs", schema=SCHEMA)
    for name in (
        "ck_inputs_reconciliation_reason",
        "ck_inputs_retry_schedule",
        "ck_inputs_completed_lineage",
        "ck_inputs_processing_claim",
        "ck_inputs_status",
    ):
        op.drop_constraint(op.f(name), "inputs", schema=SCHEMA)
    for column in (
        "reconciliation_reason",
        "lineage_checked_at",
        "base_checkpoint_id",
        "completed_at",
        "lease_expires_at",
        "claimed_at",
        "claimed_by",
        "claim_token",
    ):
        op.drop_column("inputs", column, schema=SCHEMA)
    op.execute(
        f"UPDATE {SCHEMA}.inputs SET status = 'failed' "
        "WHERE status IN ('retry', 'dead', 'needs_reconciliation')"
    )
    op.create_check_constraint(
        op.f("ck_inputs_status"),
        "inputs",
        "status IN ('pending', 'processing', 'completed', 'failed')",
        schema=SCHEMA,
    )
