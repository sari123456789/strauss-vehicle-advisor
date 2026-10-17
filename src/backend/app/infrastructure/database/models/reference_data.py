"""ORM tables for immutable reference data (architecture §20). Infrastructure only.

All tables are insert-only: the migration installs triggers that reject UPDATE, DELETE and
TRUNCATE. The active set is the `reference_releases` row with the highest `release_seq`.
Money is `numeric`; codes are `text` with their leading zeros; `source_values` keeps the
original, untrimmed source values for traceability.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base

SHA256_CHECK = "~ '^[0-9a-f]{64}$'"
IMMUTABLE_TABLES: tuple[str, ...] = (
    "rules_snapshots",
    "catalogue_versions",
    "vehicles",
    "occupation_policies",
    "tax_dataset_versions",
    "value_of_use",
    "reference_releases",
)


def _uuid_pk() -> Mapped[UUID]:
    return mapped_column(PG_UUID(as_uuid=True), primary_key=True)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _key_checks() -> tuple[CheckConstraint, ...]:
    """Complete official tax key as text codes (leading zeros kept)."""
    return (
        CheckConstraint(r"tax_year ~ '^\d{4}$'", name="tax_year"),
        CheckConstraint(r"registration_year ~ '^\d{4}$'", name="registration_year"),
        CheckConstraint(r"vehicle_type_code ~ '^\d$'", name="vehicle_type_code"),
        CheckConstraint(r"manufacturer_code ~ '^\d{5}$'", name="manufacturer_code"),
        CheckConstraint(r"model_code ~ '^\d{5}$'", name="model_code"),
    )


class RulesSnapshotRow(Base):
    __tablename__ = "rules_snapshots"
    __table_args__ = (
        UniqueConstraint("rules_sha256", "policy_sha256", name="uq_rules_snapshots_sources"),
        UniqueConstraint("policy_sha256", name="uq_rules_snapshots_policy_sha256"),
        UniqueConstraint("derived_policy_version", name="uq_rules_snapshots_policy_version"),
        CheckConstraint(f"rules_sha256 {SHA256_CHECK}", name="rules_sha256"),
        CheckConstraint(f"policy_sha256 {SHA256_CHECK}", name="policy_sha256"),
    )

    id: Mapped[UUID] = _uuid_pk()
    supplied_version: Mapped[str] = mapped_column(Text, nullable=False)
    rules_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    derived_policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    policy_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    rules_content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    policy_content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    imported_at: Mapped[datetime] = _created_at()


class CatalogueVersionRow(Base):
    __tablename__ = "catalogue_versions"
    __table_args__ = (
        UniqueConstraint("workbook_sha256", "policy_sha256", name="uq_catalogue_versions_sources"),
        CheckConstraint(f"workbook_sha256 {SHA256_CHECK}", name="workbook_sha256"),
        CheckConstraint(f"policy_sha256 {SHA256_CHECK}", name="policy_sha256"),
    )

    id: Mapped[UUID] = _uuid_pk()
    workbook_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    policy_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    imported_at: Mapped[datetime] = _created_at()


class VehicleRow(Base):
    __tablename__ = "vehicles"
    __table_args__ = (
        PrimaryKeyConstraint("catalogue_version_id", "catalogue_id", name="pk_vehicles"),
        CheckConstraint("track IN ('regular', 'family')", name="track"),
        CheckConstraint(
            "(track = 'regular') = (entitlement_group IS NOT NULL)", name="group_matches_track"
        ),
        CheckConstraint(
            "propulsion IN ('petrol', 'hybrid', 'plug_in_hybrid', 'electric')", name="propulsion"
        ),
        CheckConstraint(
            "list_price >= 0 AND value_of_use >= 0 AND monthly_company_cost >= 0 "
            "AND default_participation >= 0",
            name="non_negative_money",
        ),
        *_key_checks(),
    )

    catalogue_version_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("business.catalogue_versions.id"), nullable=False
    )
    catalogue_id: Mapped[str] = mapped_column(Text, nullable=False)
    track: Mapped[str] = mapped_column(Text, nullable=False)
    entitlement_group: Mapped[str | None] = mapped_column(Text)
    manufacturer_name: Mapped[str] = mapped_column(Text, nullable=False)
    commercial_name: Mapped[str] = mapped_column(Text, nullable=False)
    model_description: Mapped[str] = mapped_column(Text, nullable=False)
    tax_year: Mapped[str] = mapped_column(Text, nullable=False)
    registration_year: Mapped[str] = mapped_column(Text, nullable=False)
    vehicle_type_code: Mapped[str] = mapped_column(Text, nullable=False)
    manufacturer_code: Mapped[str] = mapped_column(Text, nullable=False)
    model_code: Mapped[str] = mapped_column(Text, nullable=False)
    propulsion: Mapped[str] = mapped_column(Text, nullable=False)
    list_price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    value_of_use: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    monthly_company_cost: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    default_participation: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_values: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)


class OccupationPolicyRow(Base):
    __tablename__ = "occupation_policies"
    __table_args__ = (
        PrimaryKeyConstraint(
            "catalogue_version_id", "occupation_id", name="pk_occupation_policies"
        ),
        UniqueConstraint("catalogue_version_id", "name_he", name="uq_occupation_policies_name"),
        CheckConstraint("waiver_allowance >= 0", name="non_negative_allowance"),
        CheckConstraint("NOT (mandatory_car AND waiver_allowed)", name="mandatory_without_waiver"),
    )

    catalogue_version_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("business.catalogue_versions.id"), nullable=False
    )
    occupation_id: Mapped[str] = mapped_column(Text, nullable=False)
    name_he: Mapped[str] = mapped_column(Text, nullable=False)
    entitlement_group: Mapped[str] = mapped_column(Text, nullable=False)
    mandatory_car: Mapped[bool] = mapped_column(nullable=False)
    waiver_allowed: Mapped[bool] = mapped_column(nullable=False)
    waiver_allowance: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_values: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)


class TaxDatasetVersionRow(Base):
    __tablename__ = "tax_dataset_versions"
    __table_args__ = (
        UniqueConstraint("csv_sha256", name="uq_tax_dataset_versions_csv_sha256"),
        CheckConstraint(f"csv_sha256 {SHA256_CHECK}", name="csv_sha256"),
        CheckConstraint("record_count > 0", name="record_count"),
    )

    id: Mapped[UUID] = _uuid_pk()
    csv_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    tax_years: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    original_header: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    imported_at: Mapped[datetime] = _created_at()


class ValueOfUseRow(Base):
    __tablename__ = "value_of_use"
    __table_args__ = (
        PrimaryKeyConstraint(
            "tax_dataset_version_id",
            "tax_year",
            "registration_year",
            "vehicle_type_code",
            "manufacturer_code",
            "model_code",
            name="pk_value_of_use",
        ),
        CheckConstraint("value_of_use >= 0", name="non_negative_value"),
        CheckConstraint(r"drive_code ~ '^\d$'", name="drive_code"),
        *_key_checks(),
    )

    tax_dataset_version_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("business.tax_dataset_versions.id"), nullable=False
    )
    tax_year: Mapped[str] = mapped_column(Text, nullable=False)
    registration_year: Mapped[str] = mapped_column(Text, nullable=False)
    vehicle_type_code: Mapped[str] = mapped_column(Text, nullable=False)
    manufacturer_code: Mapped[str] = mapped_column(Text, nullable=False)
    model_code: Mapped[str] = mapped_column(Text, nullable=False)
    value_of_use: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    effective_date: Mapped[date | None] = mapped_column(Date)
    drive_code: Mapped[str] = mapped_column(Text, nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_values: Mapped[list[str]] = mapped_column(JSONB, nullable=False)


class ReferenceReleaseRow(Base):
    __tablename__ = "reference_releases"
    __table_args__ = (
        UniqueConstraint("release_seq", name="uq_reference_releases_release_seq"),
        CheckConstraint(f"challenge_pdf_sha256 {SHA256_CHECK}", name="challenge_pdf_sha256"),
    )

    id: Mapped[UUID] = _uuid_pk()
    release_seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    rules_snapshot_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("business.rules_snapshots.id"), nullable=False
    )
    catalogue_version_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("business.catalogue_versions.id"), nullable=False
    )
    tax_dataset_version_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("business.tax_dataset_versions.id"), nullable=False
    )
    challenge_pdf_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    challenge_pdf_page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    consistency_report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    published_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    published_at: Mapped[datetime] = _created_at()
