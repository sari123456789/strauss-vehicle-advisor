"""ORM tables for profiles, cases, alternatives, operations and calculation records (Step 3A).

ORM rows are not Domain models; repositories map between them. Keys, foreign keys and
indexes are declared here and compared against the migration by the drift test. CHECK
constraints, JSONB validation functions and protective triggers are defined in migration
`0002_business_cases` (Alembic autogenerate does not compare them).

Composite foreign keys keep references inside one case (requests -> alternatives, runs ->
result sets) and keep a run's three source versions inside one reference release.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.business.infrastructure.database.models.base import Base

RESTRICT = "RESTRICT"
USERS_ID = "auth.users.id"
CASES_ID = "business.cases.id"


def _uuid(**kwargs: Any) -> Mapped[UUID]:
    return mapped_column(PG_UUID(as_uuid=True), **kwargs)


def _fk_uuid(target: str, *, nullable: bool = False) -> Mapped[UUID]:
    return mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(target, ondelete=RESTRICT), nullable=nullable
    )


def _timestamp() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class EmployeeProfileRow(Base):
    __tablename__ = "employee_profiles"

    employee_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(USERS_ID, ondelete=RESTRICT), primary_key=True
    )
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    origin: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = _timestamp()
    updated_at: Mapped[datetime] = _timestamp()


class CaseRow(Base):
    __tablename__ = "cases"
    __table_args__ = (
        UniqueConstraint("conversation_id", name="uq_cases_conversation_id"),
        Index("ix_cases_owner_subject_id_updated_at", "owner_subject_id", "updated_at"),
    )

    id: Mapped[UUID] = _uuid(primary_key=True)
    conversation_id: Mapped[UUID] = _uuid(nullable=False)
    owner_subject_id: Mapped[UUID] = _fk_uuid(USERS_ID)
    calculation_month: Mapped[str] = mapped_column(Text, nullable=False)
    reference_date: Mapped[date] = mapped_column(Date, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    salary_profile_version_used: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actual_facts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    hypothetical_overrides: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = _timestamp()
    updated_at: Mapped[datetime] = _timestamp()


class AlternativeRow(Base):
    __tablename__ = "alternatives"
    __table_args__ = (
        UniqueConstraint("case_id", "id", name="uq_alternatives_case_id_id"),
        Index(
            "uq_alternatives_active_option",
            "case_id",
            "option_key",
            unique=True,
            postgresql_where=text("active"),
        ),
    )

    id: Mapped[UUID] = _uuid(primary_key=True)
    case_id: Mapped[UUID] = _fk_uuid(CASES_ID)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    option_key: Mapped[str] = mapped_column(Text, nullable=False)
    catalogue_id: Mapped[str | None] = mapped_column(Text)
    track: Mapped[str | None] = mapped_column(Text)
    inputs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = _timestamp()
    updated_at: Mapped[datetime] = _timestamp()
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OperationRow(Base):
    __tablename__ = "operations"
    __table_args__ = (
        UniqueConstraint(
            "actor_subject_id",
            "scope_kind",
            "scope_id",
            "operation_type",
            "operation_id",
            name="uq_operations_key",
        ),
    )

    id: Mapped[UUID] = _uuid(primary_key=True)
    actor_subject_id: Mapped[UUID] = _fk_uuid(USERS_ID)
    scope_kind: Mapped[str] = mapped_column(Text, nullable=False)
    scope_id: Mapped[UUID] = _uuid(nullable=False)
    operation_type: Mapped[str] = mapped_column(Text, nullable=False)
    operation_id: Mapped[UUID] = _uuid(nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = _timestamp()


class OptionResultSetRow(Base):
    __tablename__ = "option_result_sets"
    __table_args__ = (
        UniqueConstraint(
            "case_id",
            "id",
            "reference_release_id",
            name="uq_option_result_sets_case_id_id_reference_release_id",
        ),
        Index("ix_option_result_sets_case_id_created_at", "case_id", "created_at"),
    )

    id: Mapped[UUID] = _uuid(primary_key=True)
    case_id: Mapped[UUID] = _fk_uuid(CASES_ID)
    case_version_used: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reference_release_id: Mapped[UUID] = _fk_uuid("business.reference_releases.id")
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    items: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    unchecked_categories: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    scope_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = _timestamp()


class ComparisonRunRow(Base):
    __tablename__ = "comparison_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id"], [CASES_ID], name="fk_comparison_runs_case_id", ondelete=RESTRICT
        ),
        ForeignKeyConstraint(
            [
                "reference_release_id",
                "rules_snapshot_id",
                "catalogue_version_id",
                "tax_dataset_version_id",
            ],
            [
                "business.reference_releases.id",
                "business.reference_releases.rules_snapshot_id",
                "business.reference_releases.catalogue_version_id",
                "business.reference_releases.tax_dataset_version_id",
            ],
            name="fk_comparison_runs_reference_release_lineage",
            ondelete=RESTRICT,
        ),
        ForeignKeyConstraint(
            ["case_id", "option_result_set_id", "reference_release_id"],
            [
                "business.option_result_sets.case_id",
                "business.option_result_sets.id",
                "business.option_result_sets.reference_release_id",
            ],
            name="fk_comparison_runs_option_result_set",
            ondelete=RESTRICT,
        ),
        Index("ix_comparison_runs_case_id_created_at", "case_id", "created_at"),
    )

    id: Mapped[UUID] = _uuid(primary_key=True)
    case_id: Mapped[UUID] = _uuid(nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    calculation_month: Mapped[str] = mapped_column(Text, nullable=False)
    case_version_used: Mapped[int] = mapped_column(BigInteger, nullable=False)
    profile_version_used: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reference_release_id: Mapped[UUID] = _uuid(nullable=False)
    rules_snapshot_id: Mapped[UUID] = _uuid(nullable=False)
    catalogue_version_id: Mapped[UUID] = _uuid(nullable=False)
    tax_dataset_version_id: Mapped[UUID] = _uuid(nullable=False)
    option_result_set_id: Mapped[UUID | None] = _uuid()
    selection: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    include_current: Mapped[bool] = mapped_column(Boolean, nullable=False)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    operation_id: Mapped[UUID] = _uuid(nullable=False)
    created_by: Mapped[UUID] = _fk_uuid(USERS_ID)
    created_at: Mapped[datetime] = _timestamp()


class RepresentativeRequestRow(Base):
    __tablename__ = "representative_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id"], [CASES_ID], name="fk_representative_requests_case_id", ondelete=RESTRICT
        ),
        ForeignKeyConstraint(
            ["case_id", "alternative_id"],
            ["business.alternatives.case_id", "business.alternatives.id"],
            name="fk_representative_requests_alternative",
            ondelete=RESTRICT,
        ),
        UniqueConstraint(
            "case_id",
            "idempotency_key",
            name="uq_representative_requests_case_id_idempotency_key",
        ),
        Index(
            "uq_representative_requests_open_subject",
            "case_id",
            "alternative_id",
            "field_code",
            "subject_fingerprint",
            unique=True,
            postgresql_where=text("status = 'open'"),
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_representative_requests_status_created_at", "status", "created_at"),
    )

    id: Mapped[UUID] = _uuid(primary_key=True)
    case_id: Mapped[UUID] = _uuid(nullable=False)
    alternative_id: Mapped[UUID | None] = _uuid()
    alternative_version: Mapped[int | None] = mapped_column(BigInteger)
    option_key: Mapped[str | None] = mapped_column(Text)
    field_code: Mapped[str] = mapped_column(Text, nullable=False)
    expected_value_type: Mapped[str] = mapped_column(Text, nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    subject_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    subject_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    case_version_at_creation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    request_version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="open")
    idempotency_key: Mapped[UUID] = _uuid(nullable=False)
    created_by: Mapped[UUID] = _fk_uuid(USERS_ID)
    created_at: Mapped[datetime] = _timestamp()
    updated_at: Mapped[datetime] = _timestamp()
