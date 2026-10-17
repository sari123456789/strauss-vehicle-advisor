"""Reference data: business schema, immutable versions, records and releases.

Revision ID: 0001_reference_data
Revises:
Create Date: 2026-10-17

Tables are insert-only: a trigger rejects UPDATE/DELETE (per row) and TRUNCATE (per statement).
The active reference set is the `reference_releases` row with the highest `release_seq`.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0001_reference_data"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "business"
SHA256 = "~ '^[0-9a-f]{64}$'"
IMMUTABLE_TABLES = (
    "rules_snapshots",
    "catalogue_versions",
    "vehicles",
    "occupation_policies",
    "tax_dataset_versions",
    "value_of_use",
    "reference_releases",
)


def _uuid(name: str, **kwargs: Any) -> sa.Column[Any]:
    return sa.Column(name, UUID(as_uuid=True), **kwargs)


def _created(name: str) -> sa.Column[Any]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def _text(name: str, nullable: bool = False) -> sa.Column[Any]:
    return sa.Column(name, sa.Text(), nullable=nullable)


def _key_columns(table: str) -> list[Any]:
    return [
        _text("tax_year"),
        _text("registration_year"),
        _text("vehicle_type_code"),
        _text("manufacturer_code"),
        _text("model_code"),
        sa.CheckConstraint(r"tax_year ~ '^\d{4}$'", name=f"ck_{table}_tax_year"),
        sa.CheckConstraint(r"registration_year ~ '^\d{4}$'", name=f"ck_{table}_registration_year"),
        sa.CheckConstraint(r"vehicle_type_code ~ '^\d$'", name=f"ck_{table}_vehicle_type_code"),
        sa.CheckConstraint(r"manufacturer_code ~ '^\d{5}$'", name=f"ck_{table}_manufacturer_code"),
        sa.CheckConstraint(r"model_code ~ '^\d{5}$'", name=f"ck_{table}_model_code"),
    ]


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    op.create_table(
        "rules_snapshots",
        _uuid("id"),
        _text("supplied_version"),
        _text("rules_sha256"),
        _text("derived_policy_version"),
        _text("policy_sha256"),
        sa.Column("rules_content", JSONB(), nullable=False),
        sa.Column("policy_content", JSONB(), nullable=False),
        _created("imported_at"),
        sa.PrimaryKeyConstraint("id", name="pk_rules_snapshots"),
        sa.UniqueConstraint("rules_sha256", "policy_sha256", name="uq_rules_snapshots_sources"),
        sa.UniqueConstraint("policy_sha256", name="uq_rules_snapshots_policy_sha256"),
        sa.UniqueConstraint("derived_policy_version", name="uq_rules_snapshots_policy_version"),
        sa.CheckConstraint(f"rules_sha256 {SHA256}", name="ck_rules_snapshots_rules_sha256"),
        sa.CheckConstraint(f"policy_sha256 {SHA256}", name="ck_rules_snapshots_policy_sha256"),
        schema=SCHEMA,
    )

    op.create_table(
        "catalogue_versions",
        _uuid("id"),
        _text("workbook_sha256"),
        _text("policy_sha256"),
        sa.Column("report", JSONB(), nullable=False),
        _created("imported_at"),
        sa.PrimaryKeyConstraint("id", name="pk_catalogue_versions"),
        sa.UniqueConstraint(
            "workbook_sha256", "policy_sha256", name="uq_catalogue_versions_sources"
        ),
        sa.CheckConstraint(
            f"workbook_sha256 {SHA256}", name="ck_catalogue_versions_workbook_sha256"
        ),
        sa.CheckConstraint(f"policy_sha256 {SHA256}", name="ck_catalogue_versions_policy_sha256"),
        schema=SCHEMA,
    )

    op.create_table(
        "vehicles",
        _uuid("catalogue_version_id", nullable=False),
        _text("catalogue_id"),
        _text("track"),
        _text("entitlement_group", nullable=True),
        _text("manufacturer_name"),
        _text("commercial_name"),
        _text("model_description"),
        *_key_columns("vehicles"),
        _text("propulsion"),
        sa.Column("list_price", sa.Numeric(), nullable=False),
        sa.Column("value_of_use", sa.Numeric(), nullable=False),
        sa.Column("monthly_company_cost", sa.Numeric(), nullable=False),
        sa.Column("default_participation", sa.Numeric(), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("source_values", JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("catalogue_version_id", "catalogue_id", name="pk_vehicles"),
        sa.ForeignKeyConstraint(
            ["catalogue_version_id"],
            [f"{SCHEMA}.catalogue_versions.id"],
            name="fk_vehicles_catalogue_version_id",
        ),
        sa.CheckConstraint("track IN ('regular', 'family')", name="ck_vehicles_track"),
        sa.CheckConstraint(
            "(track = 'regular') = (entitlement_group IS NOT NULL)",
            name="ck_vehicles_group_matches_track",
        ),
        sa.CheckConstraint(
            "propulsion IN ('petrol', 'hybrid', 'plug_in_hybrid', 'electric')",
            name="ck_vehicles_propulsion",
        ),
        sa.CheckConstraint(
            "list_price >= 0 AND value_of_use >= 0 AND monthly_company_cost >= 0 "
            "AND default_participation >= 0",
            name="ck_vehicles_non_negative_money",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "occupation_policies",
        _uuid("catalogue_version_id", nullable=False),
        _text("occupation_id"),
        _text("name_he"),
        _text("entitlement_group"),
        sa.Column("mandatory_car", sa.Boolean(), nullable=False),
        sa.Column("waiver_allowed", sa.Boolean(), nullable=False),
        sa.Column("waiver_allowance", sa.Numeric(), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("source_values", JSONB(), nullable=False),
        sa.PrimaryKeyConstraint(
            "catalogue_version_id", "occupation_id", name="pk_occupation_policies"
        ),
        sa.ForeignKeyConstraint(
            ["catalogue_version_id"],
            [f"{SCHEMA}.catalogue_versions.id"],
            name="fk_occupation_policies_catalogue_version_id",
        ),
        sa.UniqueConstraint("catalogue_version_id", "name_he", name="uq_occupation_policies_name"),
        sa.CheckConstraint(
            "waiver_allowance >= 0", name="ck_occupation_policies_non_negative_allowance"
        ),
        sa.CheckConstraint(
            "NOT (mandatory_car AND waiver_allowed)",
            name="ck_occupation_policies_mandatory_without_waiver",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "tax_dataset_versions",
        _uuid("id"),
        _text("csv_sha256"),
        sa.Column("tax_years", JSONB(), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("original_header", JSONB(), nullable=False),
        sa.Column("report", JSONB(), nullable=False),
        _created("imported_at"),
        sa.PrimaryKeyConstraint("id", name="pk_tax_dataset_versions"),
        sa.UniqueConstraint("csv_sha256", name="uq_tax_dataset_versions_csv_sha256"),
        sa.CheckConstraint(f"csv_sha256 {SHA256}", name="ck_tax_dataset_versions_csv_sha256"),
        sa.CheckConstraint("record_count > 0", name="ck_tax_dataset_versions_record_count"),
        schema=SCHEMA,
    )

    op.create_table(
        "value_of_use",
        _uuid("tax_dataset_version_id", nullable=False),
        *_key_columns("value_of_use"),
        sa.Column("value_of_use", sa.Numeric(), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=True),
        _text("drive_code"),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("source_values", JSONB(), nullable=False),
        sa.PrimaryKeyConstraint(
            "tax_dataset_version_id",
            "tax_year",
            "registration_year",
            "vehicle_type_code",
            "manufacturer_code",
            "model_code",
            name="pk_value_of_use",
        ),
        sa.ForeignKeyConstraint(
            ["tax_dataset_version_id"],
            [f"{SCHEMA}.tax_dataset_versions.id"],
            name="fk_value_of_use_tax_dataset_version_id",
        ),
        sa.CheckConstraint("value_of_use >= 0", name="ck_value_of_use_non_negative_value"),
        sa.CheckConstraint(r"drive_code ~ '^\d$'", name="ck_value_of_use_drive_code"),
        schema=SCHEMA,
    )

    op.create_table(
        "reference_releases",
        _uuid("id"),
        sa.Column("release_seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        _uuid("rules_snapshot_id", nullable=False),
        _uuid("catalogue_version_id", nullable=False),
        _uuid("tax_dataset_version_id", nullable=False),
        _text("challenge_pdf_sha256"),
        sa.Column("challenge_pdf_page_count", sa.Integer(), nullable=False),
        sa.Column("consistency_report", JSONB(), nullable=False),
        _uuid("published_by", nullable=False),
        _created("published_at"),
        sa.PrimaryKeyConstraint("id", name="pk_reference_releases"),
        sa.UniqueConstraint("release_seq", name="uq_reference_releases_release_seq"),
        sa.ForeignKeyConstraint(
            ["rules_snapshot_id"],
            [f"{SCHEMA}.rules_snapshots.id"],
            name="fk_reference_releases_rules_snapshot_id",
        ),
        sa.ForeignKeyConstraint(
            ["catalogue_version_id"],
            [f"{SCHEMA}.catalogue_versions.id"],
            name="fk_reference_releases_catalogue_version_id",
        ),
        sa.ForeignKeyConstraint(
            ["tax_dataset_version_id"],
            [f"{SCHEMA}.tax_dataset_versions.id"],
            name="fk_reference_releases_tax_dataset_version_id",
        ),
        sa.CheckConstraint(
            f"challenge_pdf_sha256 {SHA256}", name="ck_reference_releases_challenge_pdf_sha256"
        ),
        schema=SCHEMA,
    )

    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.reject_reference_data_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'reference data is immutable: % on %.% is not allowed',
                TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    for table in IMMUTABLE_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable_rows BEFORE UPDATE OR DELETE ON {SCHEMA}.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.reject_reference_data_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {SCHEMA}.{table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {SCHEMA}.reject_reference_data_mutation()"
        )


def downgrade() -> None:
    for table in reversed(IMMUTABLE_TABLES):
        op.drop_table(table, schema=SCHEMA)
    op.execute(f"DROP FUNCTION {SCHEMA}.reject_reference_data_mutation()")
    op.execute(f"DROP SCHEMA {SCHEMA}")
