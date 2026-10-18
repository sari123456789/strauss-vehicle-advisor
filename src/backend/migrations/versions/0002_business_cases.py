"""Business cases: identities, employee profiles, cases, alternatives, operations, results.

Revision ID: 0002_business_cases
Revises: 0001_reference_data
Create Date: 2026-10-18

- `auth.users`: demo identities without credentials.
- Facts documents (`employee_profiles.fields`, `cases.actual_facts`) and hypothetical overrides
  are JSONB validated by IMMUTABLE functions in CHECK constraints (decision Q1): exact keys,
  status/value consistency, decimal strings (never JSON floats), missing is never zero.
- Composite foreign keys keep requests and runs inside their case and a run's source versions
  inside one reference release (Q4, Q5). `uq_reference_releases_lineage` is added to the 0001
  table as a constraint only; no reference data is changed.
- `option_result_sets` and `comparison_runs` are insert-only; profiles, cases, alternatives,
  operations and representative requests reject DELETE/TRUNCATE (Q6).
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision: str = "0002_business_cases"
down_revision: str | None = "0001_reference_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "business"
AUTH = "auth"
SHA256 = "~ '^[0-9a-f]{64}$'"
MONTH = r"~ '^\d{4}-(0[1-9]|1[0-2])$'"
OPTION_KEY = r"'^(vehicle:[A-Z]{2,5}-\d{2,4}|waiver)$'"
SNAPSHOT_TABLES = ("option_result_sets", "comparison_runs")
NO_DELETE_TABLES = (
    "employee_profiles",
    "cases",
    "alternatives",
    "operations",
    "representative_requests",
)
FACT_PATHS = (
    "payroll.baseGross",
    "payroll.commissionsGross",
    "payroll.bonusGross",
    "payroll.pensionEmployeeRate",
    "payroll.studyFundEmployeeRate",
    "payroll.creditPoints",
    "employee.occupationId",
    "employee.entitlementGroup",
    "employee.qualifyingChildrenCount",
    "currentArrangement.kind",
    "currentArrangement.vehicle",
    "currentArrangement.propulsion",
    "currentArrangement.participation",
    "currentArrangement.familyDeduction",
    "currentArrangement.familyDeductionIncludedInParticipation",
    "currentArrangement.allowance",
)
MONEY_PATHS = (
    "payroll.baseGross",
    "payroll.commissionsGross",
    "payroll.bonusGross",
    "currentArrangement.participation",
    "currentArrangement.familyDeduction",
    "currentArrangement.allowance",
)
UUID_PATTERN = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


def _array(values: Sequence[str]) -> str:
    return "ARRAY[" + ", ".join(f"'{v}'" for v in values) + "]::text[]"


FUNCTIONS: tuple[tuple[str, str], ...] = (
    (
        "json_key_count(v jsonb)",
        """RETURNS integer LANGUAGE plpgsql IMMUTABLE AS $$
        BEGIN
            IF v IS NULL OR jsonb_typeof(v) <> 'object' THEN RETURN -1; END IF;
            RETURN (SELECT count(*) FROM jsonb_object_keys(v));
        END; $$""",
    ),
    (
        "json_text_matches(v jsonb, pattern text)",
        """RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
        BEGIN
            IF v IS NULL OR jsonb_typeof(v) <> 'string' THEN RETURN false; END IF;
            RETURN (v #>> '{}') ~ pattern;
        END; $$""",
    ),
    (
        "json_decimal_string(v jsonb, max_length integer)",
        r"""RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
        BEGIN
            RETURN business.json_text_matches(v, '^\d+(\.\d+)?$')
                AND char_length(v #>> '{}') <= max_length;
        END; $$""",
    ),
    (
        "json_null_or_text(v jsonb, pattern text)",
        """RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
        BEGIN
            IF v IS NULL THEN RETURN false; END IF;
            RETURN jsonb_typeof(v) = 'null' OR business.json_text_matches(v, pattern);
        END; $$""",
    ),
    (
        "is_valid_tax_key(k jsonb)",
        r"""RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
        BEGIN
            IF business.json_key_count(k) <> 5 THEN RETURN false; END IF;
            RETURN business.json_text_matches(k->'taxYear', '^\d{4}$')
                AND business.json_text_matches(k->'registrationYear', '^\d{4}$')
                AND business.json_text_matches(k->'vehicleTypeCode', '^\d$')
                AND business.json_text_matches(k->'manufacturerCode', '^\d{5}$')
                AND business.json_text_matches(k->'modelCode', '^\d{5}$');
        END; $$""",
    ),
    (
        "is_valid_fact_value(path text, v jsonb)",
        rf"""RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
        BEGIN
            IF v IS NULL THEN RETURN false; END IF;
            IF path = ANY({_array(MONEY_PATHS)}) THEN
                RETURN business.json_decimal_string(v, 40);
            ELSIF path = 'payroll.creditPoints' THEN
                RETURN business.json_decimal_string(v, 10);
            ELSIF path IN ('payroll.pensionEmployeeRate', 'payroll.studyFundEmployeeRate') THEN
                RETURN business.json_text_matches(v, '^(0(\.\d+)?|1(\.0+)?)$')
                    AND char_length(v #>> '{{}}') <= 40;
            ELSIF path = 'employee.occupationId' THEN
                RETURN business.json_text_matches(v, '^OCC-[A-Z0-9-]{{2,40}}$');
            ELSIF path = 'employee.entitlementGroup' THEN
                RETURN business.json_text_matches(v, '^(א|ב|ג|ד)$');
            ELSIF path = 'employee.qualifyingChildrenCount' THEN
                RETURN jsonb_typeof(v) = 'number' AND (v #>> '{{}}') ~ '^\d{{1,3}}$'
                    AND (v #>> '{{}}')::integer <= 100;
            ELSIF path = 'currentArrangement.kind' THEN
                RETURN business.json_text_matches(
                    v, '^(company_car|allowance|no_car_no_allowance)$');
            ELSIF path = 'currentArrangement.propulsion' THEN
                RETURN business.json_text_matches(v, '^(petrol|hybrid|plug_in_hybrid|electric)$');
            ELSIF path = 'currentArrangement.familyDeductionIncludedInParticipation' THEN
                RETURN jsonb_typeof(v) = 'boolean';
            ELSIF path = 'currentArrangement.vehicle' THEN
                IF business.json_key_count(v) <> 1 THEN RETURN false; END IF;
                IF v ? 'catalogueId' THEN
                    RETURN business.json_text_matches(v->'catalogueId', '^[A-Z]{{2,5}}-\d{{2,4}}$');
                END IF;
                RETURN v ? 'taxKey' AND business.is_valid_tax_key(v->'taxKey');
            END IF;
            RETURN false;
        END; $$""",
    ),
    (
        "is_valid_field_envelope(path text, e jsonb)",
        f"""RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
        BEGIN
            IF business.json_key_count(e) <> 7 OR NOT (e ?& ARRAY['value', 'status', 'source',
                'sourceMessageId', 'updatedAt', 'revision', 'lastConfirmedAt']) THEN
                RETURN false;
            END IF;
            IF NOT business.json_text_matches(e->'status', '^(missing|needs_clarification|usable)$')
                OR NOT business.json_null_or_text(
                    e->'source', '^(employee|catalogue|rules|tax_dataset|representative)$')
                OR NOT business.json_null_or_text(e->'sourceMessageId', '{UUID_PATTERN}')
                OR NOT business.json_null_or_text(e->'updatedAt', '^.+$')
                OR NOT business.json_null_or_text(e->'lastConfirmedAt', '^.+$')
                OR jsonb_typeof(e->'revision') <> 'number'
                OR (e #>> '{{revision}}') !~ '^\\d{{1,18}}$' THEN
                RETURN false;
            END IF;
            IF e->>'status' = 'missing' THEN RETURN jsonb_typeof(e->'value') = 'null'; END IF;
            IF jsonb_typeof(e->'value') = 'null' THEN RETURN e->>'status' <> 'usable'; END IF;
            RETURN business.is_valid_fact_value(path, e->'value');
        END; $$""",
    ),
    (
        "is_valid_facts_document(doc jsonb)",
        f"""RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
        DECLARE p text;
        BEGIN
            IF business.json_key_count(doc) <> {len(FACT_PATHS) + 1}
                OR doc->'schemaVersion' IS DISTINCT FROM '1'::jsonb THEN
                RETURN false;
            END IF;
            FOREACH p IN ARRAY {_array(FACT_PATHS)} LOOP
                IF NOT business.is_valid_field_envelope(p, doc->p) THEN RETURN false; END IF;
            END LOOP;
            RETURN true;
        END; $$""",
    ),
    (
        "is_valid_overrides(arr jsonb)",
        f"""RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
        DECLARE e jsonb; seen text[] := ARRAY[]::text[];
        BEGIN
            IF arr IS NULL OR jsonb_typeof(arr) <> 'array' THEN RETURN false; END IF;
            FOR e IN SELECT value FROM jsonb_array_elements(arr) LOOP
                IF business.json_key_count(e) <> 4
                    OR NOT (e ?& ARRAY['path', 'value', 'sourceMessageId', 'createdAt'])
                    OR jsonb_typeof(e->'path') <> 'string'
                    OR NOT ((e->>'path') = ANY({_array(FACT_PATHS)}))
                    OR (e->>'path') = ANY(seen)
                    OR NOT business.is_valid_fact_value(e->>'path', e->'value')
                    OR NOT business.json_null_or_text(e->'sourceMessageId', '{UUID_PATTERN}')
                    OR NOT business.json_text_matches(e->'createdAt', '^.+$') THEN
                    RETURN false;
                END IF;
                seen := seen || (e->>'path');
            END LOOP;
            RETURN true;
        END; $$""",
    ),
    (
        "reject_snapshot_mutation()",
        """RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'calculation snapshots are immutable: % on %.% is not allowed',
                TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END; $$""",
    ),
    (
        "reject_delete()",
        """RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'business records are never deleted: % on %.% is not allowed',
                TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END; $$""",
    ),
)


def _uuid(name: str, **kwargs: Any) -> sa.Column[Any]:
    return sa.Column(name, UUID(as_uuid=True), **kwargs)


def _created(name: str) -> sa.Column[Any]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def _bigint(name: str, **kwargs: Any) -> sa.Column[Any]:
    return sa.Column(name, sa.BigInteger(), nullable=False, **kwargs)


def _text(name: str, nullable: bool = False, **kwargs: Any) -> sa.Column[Any]:
    return sa.Column(name, sa.Text(), nullable=nullable, **kwargs)


def _jsonb(name: str, nullable: bool = False, **kwargs: Any) -> sa.Column[Any]:
    return sa.Column(name, JSONB(), nullable=nullable, **kwargs)


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [target], name=f"fk_{table}_{column}", ondelete="RESTRICT"
    )


def _non_negative(*columns: str) -> list[sa.CheckConstraint]:
    return [sa.CheckConstraint(f"{c} >= 0", name=c) for c in columns]


def _json_type(column: str, kind: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(f"jsonb_typeof({column}) = '{kind}'", name=f"{column}_is_{kind}")


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA {AUTH}")
    for signature, body in FUNCTIONS:
        op.execute(f"CREATE FUNCTION {SCHEMA}.{signature} {body}")

    op.create_unique_constraint(
        "uq_reference_releases_lineage",
        "reference_releases",
        ["id", "rules_snapshot_id", "catalogue_version_id", "tax_dataset_version_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "users",
        _uuid("id"),
        _text("login"),
        _text("display_name", nullable=True),
        sa.Column("roles", ARRAY(sa.Text()), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        _created("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("login", name="uq_users_login"),
        sa.CheckConstraint(r"login ~ '^[a-z0-9._-]{2,64}$'", name="login"),
        sa.CheckConstraint(
            "cardinality(roles) >= 1 "
            "AND roles <@ ARRAY['employee', 'representative', 'import_operator']::text[]",
            name="roles",
        ),
        schema=AUTH,
    )

    op.create_table(
        "employee_profiles",
        _uuid("employee_id"),
        _bigint("version"),
        _jsonb("fields"),
        _jsonb("origin", nullable=True),
        _created("created_at"),
        _created("updated_at"),
        sa.PrimaryKeyConstraint("employee_id", name="pk_employee_profiles"),
        _fk("employee_profiles", "employee_id", f"{AUTH}.users.id"),
        *_non_negative("version"),
        sa.CheckConstraint(f"{SCHEMA}.is_valid_facts_document(fields)", name="fields"),
        sa.CheckConstraint("origin IS NULL OR jsonb_typeof(origin) = 'object'", name="origin"),
        schema=SCHEMA,
    )

    op.create_table(
        "cases",
        _uuid("id"),
        _uuid("conversation_id", nullable=False),
        _uuid("owner_subject_id", nullable=False),
        _text("calculation_month"),
        sa.Column("reference_date", sa.Date(), nullable=False),
        _bigint("version"),
        _text("lifecycle_status", server_default="active"),
        _bigint("salary_profile_version_used"),
        _jsonb("actual_facts"),
        _jsonb("hypothetical_overrides", server_default=sa.text("'[]'::jsonb")),
        _created("created_at"),
        _created("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_cases"),
        sa.UniqueConstraint("conversation_id", name="uq_cases_conversation_id"),
        _fk("cases", "owner_subject_id", f"{AUTH}.users.id"),
        sa.CheckConstraint(f"calculation_month {MONTH}", name="calculation_month"),
        *_non_negative("version", "salary_profile_version_used"),
        sa.CheckConstraint("lifecycle_status IN ('active', 'closed')", name="lifecycle_status"),
        sa.CheckConstraint(f"{SCHEMA}.is_valid_facts_document(actual_facts)", name="actual_facts"),
        sa.CheckConstraint(
            f"{SCHEMA}.is_valid_overrides(hypothetical_overrides)",
            name="hypothetical_overrides",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_cases_owner_subject_id_updated_at",
        "cases",
        ["owner_subject_id", "updated_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "alternatives",
        _uuid("id"),
        _uuid("case_id", nullable=False),
        _bigint("version"),
        _text("kind"),
        _text("option_key"),
        _text("catalogue_id", nullable=True),
        _text("track", nullable=True),
        _jsonb("inputs", server_default=sa.text("'{}'::jsonb")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        _created("created_at"),
        _created("updated_at"),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_alternatives"),
        _fk("alternatives", "case_id", f"{SCHEMA}.cases.id"),
        sa.UniqueConstraint("case_id", "id", name="uq_alternatives_case_id_id"),
        *_non_negative("version"),
        sa.CheckConstraint("kind IN ('vehicle', 'waiver')", name="kind"),
        sa.CheckConstraint(f"option_key ~ {OPTION_KEY}", name="option_key"),
        sa.CheckConstraint("track IS NULL OR track IN ('regular', 'family')", name="track"),
        sa.CheckConstraint(
            "(kind = 'vehicle' AND catalogue_id IS NOT NULL "
            "AND option_key = 'vehicle:' || catalogue_id) "
            "OR (kind = 'waiver' AND catalogue_id IS NULL AND track IS NULL "
            "AND option_key = 'waiver')",
            name="kind_consistency",
        ),
        sa.CheckConstraint("active = (deactivated_at IS NULL)", name="active_deactivated"),
        _json_type("inputs", "object"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_alternatives_active_option",
        "alternatives",
        ["case_id", "option_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("active"),
    )

    op.create_table(
        "operations",
        _uuid("id"),
        _uuid("actor_subject_id", nullable=False),
        _text("scope_kind"),
        _uuid("scope_id", nullable=False),
        _text("operation_type"),
        _uuid("operation_id", nullable=False),
        _text("request_hash"),
        _jsonb("result", nullable=True),
        _created("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_operations"),
        _fk("operations", "actor_subject_id", f"{AUTH}.users.id"),
        sa.UniqueConstraint(
            "actor_subject_id",
            "scope_kind",
            "scope_id",
            "operation_type",
            "operation_id",
            name="uq_operations_key",
        ),
        sa.CheckConstraint(
            "scope_kind IN ('conversation', 'case', 'representative_request')",
            name="scope_kind",
        ),
        sa.CheckConstraint("operation_type ~ '^[a-z_]{3,64}$'", name="operation_type"),
        sa.CheckConstraint(f"request_hash {SHA256}", name="request_hash"),
        sa.CheckConstraint("result IS NULL OR jsonb_typeof(result) = 'object'", name="result"),
        schema=SCHEMA,
    )

    op.create_table(
        "option_result_sets",
        _uuid("id"),
        _uuid("case_id", nullable=False),
        _bigint("case_version_used"),
        _uuid("reference_release_id", nullable=False),
        _jsonb("request"),
        _jsonb("items"),
        _jsonb("unchecked_categories", server_default=sa.text("'[]'::jsonb")),
        sa.Column("scope_complete", sa.Boolean(), nullable=False),
        _created("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_option_result_sets"),
        _fk("option_result_sets", "case_id", f"{SCHEMA}.cases.id"),
        _fk("option_result_sets", "reference_release_id", f"{SCHEMA}.reference_releases.id"),
        sa.UniqueConstraint(
            "case_id",
            "id",
            "reference_release_id",
            name="uq_option_result_sets_case_id_id_reference_release_id",
        ),
        *_non_negative("case_version_used"),
        _json_type("request", "object"),
        _json_type("items", "array"),
        _json_type("unchecked_categories", "array"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_option_result_sets_case_id_created_at",
        "option_result_sets",
        ["case_id", "created_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "comparison_runs",
        _uuid("id"),
        _uuid("case_id", nullable=False),
        _text("kind"),
        _text("calculation_month"),
        _bigint("case_version_used"),
        _bigint("profile_version_used"),
        _uuid("reference_release_id", nullable=False),
        _uuid("rules_snapshot_id", nullable=False),
        _uuid("catalogue_version_id", nullable=False),
        _uuid("tax_dataset_version_id", nullable=False),
        _uuid("option_result_set_id", nullable=True),
        _jsonb("selection"),
        sa.Column("include_current", sa.Boolean(), nullable=False),
        _jsonb("input_snapshot"),
        _jsonb("result"),
        _uuid("operation_id", nullable=False),
        _uuid("created_by", nullable=False),
        _created("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_comparison_runs"),
        _fk("comparison_runs", "case_id", f"{SCHEMA}.cases.id"),
        _fk("comparison_runs", "created_by", f"{AUTH}.users.id"),
        sa.ForeignKeyConstraint(
            [
                "reference_release_id",
                "rules_snapshot_id",
                "catalogue_version_id",
                "tax_dataset_version_id",
            ],
            [
                f"{SCHEMA}.reference_releases.id",
                f"{SCHEMA}.reference_releases.rules_snapshot_id",
                f"{SCHEMA}.reference_releases.catalogue_version_id",
                f"{SCHEMA}.reference_releases.tax_dataset_version_id",
            ],
            name="fk_comparison_runs_reference_release_lineage",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["case_id", "option_result_set_id", "reference_release_id"],
            [
                f"{SCHEMA}.option_result_sets.case_id",
                f"{SCHEMA}.option_result_sets.id",
                f"{SCHEMA}.option_result_sets.reference_release_id",
            ],
            name="fk_comparison_runs_option_result_set",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("kind IN ('regular', 'hypothetical')", name="kind"),
        sa.CheckConstraint(f"calculation_month {MONTH}", name="calculation_month"),
        *_non_negative("case_version_used", "profile_version_used"),
        _json_type("selection", "object"),
        _json_type("input_snapshot", "object"),
        _json_type("result", "object"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_comparison_runs_case_id_created_at",
        "comparison_runs",
        ["case_id", "created_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "representative_requests",
        _uuid("id"),
        _uuid("case_id", nullable=False),
        _uuid("alternative_id", nullable=True),
        sa.Column("alternative_version", sa.BigInteger(), nullable=True),
        _text("option_key", nullable=True),
        _text("field_code"),
        _text("expected_value_type"),
        _text("question_text"),
        _jsonb("subject_snapshot"),
        _text("subject_fingerprint"),
        _bigint("case_version_at_creation"),
        _bigint("request_version", server_default="0"),
        _text("status", server_default="open"),
        _uuid("idempotency_key", nullable=False),
        _uuid("created_by", nullable=False),
        _created("created_at"),
        _created("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_representative_requests"),
        _fk("representative_requests", "case_id", f"{SCHEMA}.cases.id"),
        _fk("representative_requests", "created_by", f"{AUTH}.users.id"),
        sa.ForeignKeyConstraint(
            ["case_id", "alternative_id"],
            [f"{SCHEMA}.alternatives.case_id", f"{SCHEMA}.alternatives.id"],
            name="fk_representative_requests_alternative",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "case_id",
            "idempotency_key",
            name="uq_representative_requests_case_id_idempotency_key",
        ),
        sa.CheckConstraint(
            "(alternative_id IS NULL) = (alternative_version IS NULL)",
            name="alternative_version",
        ),
        sa.CheckConstraint(
            f"option_key IS NULL OR option_key ~ {OPTION_KEY}",
            name="option_key",
        ),
        sa.CheckConstraint(
            "field_code IN ('alternative.participation', 'occupation.waiverPolicy', "
            "'employee.entitlementGroup', 'currentArrangement.propulsion', "
            "'currentArrangement.vehicle')",
            name="field_code",
        ),
        sa.CheckConstraint(
            "expected_value_type IN ('money', 'entitlement_group', 'propulsion', 'tax_key', "
            "'boolean')",
            name="expected_value_type",
        ),
        sa.CheckConstraint(
            "char_length(question_text) BETWEEN 1 AND 1000",
            name="question_text",
        ),
        sa.CheckConstraint(f"subject_fingerprint {SHA256}", name="subject_fingerprint"),
        sa.CheckConstraint(
            "status IN ('open', 'answered', 'cannot_determine', 'cancelled')",
            name="status",
        ),
        *_non_negative(
            "case_version_at_creation",
            "request_version",
        ),
        _json_type("subject_snapshot", "object"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_representative_requests_open_subject",
        "representative_requests",
        ["case_id", "alternative_id", "field_code", "subject_fingerprint"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'open'"),
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "ix_representative_requests_status_created_at",
        "representative_requests",
        ["status", "created_at"],
        schema=SCHEMA,
    )

    for table in SNAPSHOT_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable_rows BEFORE UPDATE OR DELETE ON {SCHEMA}.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.reject_snapshot_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {SCHEMA}.{table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {SCHEMA}.reject_snapshot_mutation()"
        )
    for table in NO_DELETE_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {SCHEMA}.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.reject_delete()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {SCHEMA}.{table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {SCHEMA}.reject_delete()"
        )


def downgrade() -> None:
    for table in (
        "representative_requests",
        "comparison_runs",
        "option_result_sets",
        "operations",
        "alternatives",
        "cases",
        "employee_profiles",
    ):
        op.drop_table(table, schema=SCHEMA)
    op.drop_table("users", schema=AUTH)
    op.drop_constraint("uq_reference_releases_lineage", "reference_releases", schema=SCHEMA)
    for signature, _body in reversed(FUNCTIONS):
        op.execute(f"DROP FUNCTION {SCHEMA}.{signature}")
    op.execute(f"DROP SCHEMA {AUTH}")
