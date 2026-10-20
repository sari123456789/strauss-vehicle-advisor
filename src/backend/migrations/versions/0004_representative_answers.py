"""Representative answers, case representative facts, integration outbox and audit (B3a).

Revision ID: 0004_representative_answers
Revises: 0003_auth_credentials
Create Date: 2026-10-20

- `representative_answers`: at most one immutable answer per request (architecture §17, §20).
- `case_representative_facts`: case-local facts supplied by a representative (decision Q1);
  values are validated by the 0002 function `is_valid_fact_value`. Insert-only.
- `integration_outbox`: business events written in the answer transaction (decision D1);
  `sequence` is the delivery order. Rows are never deleted, and the only permitted change is
  setting `acknowledged_at` once.
- `audit_entries`: minimal audit (decision D2/R1), insert-only, no salary data.
- `uq_representative_requests_case_id_id` is added to the 0002 table as a constraint only, so
  answers and facts reference a request inside their own case.
- Additive only: 0001-0003 are unchanged.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0004_representative_answers"
down_revision: str | None = "0003_auth_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "business"
AUTH = "auth"
INSERT_ONLY_TABLES = ("representative_answers", "case_representative_facts", "audit_entries")
FUNCTIONS: tuple[tuple[str, str], ...] = (
    (
        "reject_record_mutation()",
        """RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'records are insert-only: % on %.% is not allowed',
                TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END; $$""",
    ),
    (
        "outbox_acknowledge_only()",
        """RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.acknowledged_at IS NOT NULL OR NEW.acknowledged_at IS NULL
                OR (to_jsonb(NEW) - 'acknowledged_at')
                    IS DISTINCT FROM (to_jsonb(OLD) - 'acknowledged_at') THEN
                RAISE EXCEPTION 'outbox events are immutable except for one acknowledgement'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END; $$""",
    ),
)


def _uuid(name: str, nullable: bool = False) -> sa.Column[Any]:
    return sa.Column(name, UUID(as_uuid=True), nullable=nullable)


def _time(name: str, *, server_default: bool = False, nullable: bool = False) -> sa.Column[Any]:
    default = sa.func.now() if server_default else None
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, server_default=default)


def _text(name: str, nullable: bool = False) -> sa.Column[Any]:
    return sa.Column(name, sa.Text(), nullable=nullable)


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [target], name=f"fk_{table}_{column}", ondelete="RESTRICT"
    )


def _request_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["case_id", "request_id"],
        [f"{SCHEMA}.representative_requests.case_id", f"{SCHEMA}.representative_requests.id"],
        name=f"fk_{table}_request",
        ondelete="RESTRICT",
    )


def _period() -> sa.CheckConstraint:
    return sa.CheckConstraint(
        "effective_from IS NULL OR effective_to IS NULL OR effective_to >= effective_from",
        name="effective_period",
    )


def upgrade() -> None:
    for signature, body in FUNCTIONS:
        op.execute(f"CREATE FUNCTION {SCHEMA}.{signature} {body}")

    op.create_unique_constraint(
        "uq_representative_requests_case_id_id",
        "representative_requests",
        ["case_id", "id"],
        schema=SCHEMA,
    )

    op.create_table(
        "representative_answers",
        _uuid("id"),
        _uuid("request_id"),
        _uuid("case_id"),
        _text("outcome"),
        _text("value", nullable=True),
        _text("unit", nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        _text("note", nullable=True),
        _text("application", nullable=True),
        _text("application_reason", nullable=True),
        _uuid("answered_by"),
        _time("answered_at"),
        _uuid("operation_id"),
        _time("created_at", server_default=True),
        sa.PrimaryKeyConstraint("id", name="pk_representative_answers"),
        sa.UniqueConstraint("request_id", name="uq_representative_answers_request_id"),
        _fk("representative_answers", "case_id", f"{SCHEMA}.cases.id"),
        _fk("representative_answers", "answered_by", f"{AUTH}.users.id"),
        _request_fk("representative_answers"),
        sa.CheckConstraint("outcome IN ('answered', 'cannot_determine')", name="outcome"),
        sa.CheckConstraint("(outcome = 'answered') = (value IS NOT NULL)", name="outcome_value"),
        sa.CheckConstraint("value IS NULL OR char_length(value) BETWEEN 1 AND 200", name="value"),
        sa.CheckConstraint("unit IS NULL OR unit = 'ILS'", name="unit"),
        sa.CheckConstraint("note IS NULL OR char_length(note) <= 1000", name="note"),
        sa.CheckConstraint(
            "(outcome = 'answered') = (application IS NOT NULL) "
            "AND (application IS NULL OR application IN ('applied', 'not_applicable'))",
            name="application",
        ),
        sa.CheckConstraint(
            "(application IS NOT DISTINCT FROM 'not_applicable') "
            "= (application_reason IS NOT NULL) "
            "AND (application_reason IS NULL OR application_reason IN "
            "('outside_effective_period', 'alternative_changed'))",
            name="application_reason",
        ),
        _period(),
        schema=SCHEMA,
    )

    op.create_table(
        "case_representative_facts",
        _uuid("id"),
        _uuid("case_id"),
        _uuid("request_id"),
        _text("field_path"),
        sa.Column("value", JSONB(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        _time("answered_at"),
        _time("created_at", server_default=True),
        sa.PrimaryKeyConstraint("id", name="pk_case_representative_facts"),
        sa.UniqueConstraint("request_id", name="uq_case_representative_facts_request_id"),
        _fk("case_representative_facts", "case_id", f"{SCHEMA}.cases.id"),
        _request_fk("case_representative_facts"),
        sa.CheckConstraint(
            "field_path IN ('employee.entitlementGroup', 'currentArrangement.propulsion')",
            name="field_path",
        ),
        sa.CheckConstraint(f"{SCHEMA}.is_valid_fact_value(field_path, value)", name="value"),
        _period(),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_case_representative_facts_case_id_field_path",
        "case_representative_facts",
        ["case_id", "field_path"],
        schema=SCHEMA,
    )

    op.create_table(
        "integration_outbox",
        _uuid("id"),
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        _text("event_type"),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        _time("occurred_at"),
        _time("acknowledged_at", nullable=True),
        _time("created_at", server_default=True),
        sa.PrimaryKeyConstraint("id", name="pk_integration_outbox"),
        sa.UniqueConstraint("sequence", name="uq_integration_outbox_sequence"),
        sa.CheckConstraint("event_type IN ('representative.answered')", name="event_type"),
        sa.CheckConstraint("schema_version = 1", name="schema_version"),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_is_object"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_integration_outbox_pending_sequence",
        "integration_outbox",
        ["sequence"],
        schema=SCHEMA,
        postgresql_where=sa.text("acknowledged_at IS NULL"),
    )

    op.create_table(
        "audit_entries",
        _uuid("id"),
        _uuid("actor_subject_id"),
        _text("actor_role"),
        _text("action"),
        _uuid("case_id"),
        _uuid("request_id", nullable=True),
        _text("field_code", nullable=True),
        sa.Column("details", JSONB(), nullable=True),
        _time("occurred_at"),
        sa.PrimaryKeyConstraint("id", name="pk_audit_entries"),
        _fk("audit_entries", "actor_subject_id", f"{AUTH}.users.id"),
        _fk("audit_entries", "case_id", f"{SCHEMA}.cases.id"),
        _fk("audit_entries", "request_id", f"{SCHEMA}.representative_requests.id"),
        sa.CheckConstraint(
            "actor_role IN ('employee', 'representative', 'import_operator')", name="actor_role"
        ),
        sa.CheckConstraint(r"action ~ '^[a-z_.]{3,64}$'", name="action"),
        sa.CheckConstraint("details IS NULL OR jsonb_typeof(details) = 'object'", name="details"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_audit_entries_case_id_occurred_at",
        "audit_entries",
        ["case_id", "occurred_at"],
        schema=SCHEMA,
    )

    for table in INSERT_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_insert_only BEFORE UPDATE OR DELETE ON {SCHEMA}.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.reject_record_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {SCHEMA}.{table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {SCHEMA}.reject_record_mutation()"
        )
    op.execute(
        f"CREATE TRIGGER integration_outbox_acknowledge_only BEFORE UPDATE "
        f"ON {SCHEMA}.integration_outbox "
        f"FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.outbox_acknowledge_only()"
    )
    op.execute(
        f"CREATE TRIGGER integration_outbox_no_delete BEFORE DELETE "
        f"ON {SCHEMA}.integration_outbox "
        f"FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.reject_delete()"
    )
    op.execute(
        f"CREATE TRIGGER integration_outbox_no_truncate BEFORE TRUNCATE "
        f"ON {SCHEMA}.integration_outbox "
        f"FOR EACH STATEMENT EXECUTE FUNCTION {SCHEMA}.reject_delete()"
    )


def downgrade() -> None:
    for table in (
        "audit_entries",
        "integration_outbox",
        "case_representative_facts",
        "representative_answers",
    ):
        op.drop_table(table, schema=SCHEMA)
    op.drop_constraint(
        "uq_representative_requests_case_id_id", "representative_requests", schema=SCHEMA
    )
    for signature, _body in reversed(FUNCTIONS):
        op.execute(f"DROP FUNCTION {SCHEMA}.{signature}")
