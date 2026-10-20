"""ORM tables for representative answers, case representative facts, the integration outbox
and the audit log (Step B3a, migration `0004_representative_answers`).

Answers, facts and audit entries are insert-only; outbox rows change only to record their
acknowledgement. CHECK constraints and protective triggers live in the migration (Alembic
autogenerate does not compare them). Composite foreign keys keep an answer and a fact inside
the case of their request.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
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
REQUEST_KEY = ["business.representative_requests.case_id", "business.representative_requests.id"]


def _uuid(*, nullable: bool = False, primary_key: bool = False) -> Mapped[UUID]:
    return mapped_column(PG_UUID(as_uuid=True), nullable=nullable, primary_key=primary_key)


def _fk_uuid(target: str, *, nullable: bool = False) -> Mapped[UUID]:
    return mapped_column(
        PG_UUID(as_uuid=True), ForeignKey(target, ondelete=RESTRICT), nullable=nullable
    )


def _time(*, server_default: bool = False) -> Mapped[datetime]:
    if server_default:
        return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    return mapped_column(DateTime(timezone=True), nullable=False)


class RepresentativeAnswerRow(Base):
    __tablename__ = "representative_answers"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "request_id"],
            REQUEST_KEY,
            name="fk_representative_answers_request",
            ondelete=RESTRICT,
        ),
        UniqueConstraint("request_id", name="uq_representative_answers_request_id"),
    )

    id: Mapped[UUID] = _uuid(primary_key=True)
    request_id: Mapped[UUID] = _uuid()
    case_id: Mapped[UUID] = _fk_uuid(CASES_ID)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(Text)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)
    application: Mapped[str | None] = mapped_column(Text)
    application_reason: Mapped[str | None] = mapped_column(Text)
    answered_by: Mapped[UUID] = _fk_uuid(USERS_ID)
    answered_at: Mapped[datetime] = _time()
    operation_id: Mapped[UUID] = _uuid()
    created_at: Mapped[datetime] = _time(server_default=True)


class CaseRepresentativeFactRow(Base):
    __tablename__ = "case_representative_facts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "request_id"],
            REQUEST_KEY,
            name="fk_case_representative_facts_request",
            ondelete=RESTRICT,
        ),
        UniqueConstraint("request_id", name="uq_case_representative_facts_request_id"),
        Index("ix_case_representative_facts_case_id_field_path", "case_id", "field_path"),
    )

    id: Mapped[UUID] = _uuid(primary_key=True)
    case_id: Mapped[UUID] = _fk_uuid(CASES_ID)
    request_id: Mapped[UUID] = _uuid()
    field_path: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    answered_at: Mapped[datetime] = _time()
    created_at: Mapped[datetime] = _time(server_default=True)


class IntegrationOutboxRow(Base):
    __tablename__ = "integration_outbox"
    __table_args__ = (
        UniqueConstraint("sequence", name="uq_integration_outbox_sequence"),
        Index(
            "ix_integration_outbox_pending_sequence",
            "sequence",
            postgresql_where=text("acknowledged_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = _uuid(primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = _time()
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _time(server_default=True)


class AuditEntryRow(Base):
    __tablename__ = "audit_entries"
    __table_args__ = (Index("ix_audit_entries_case_id_occurred_at", "case_id", "occurred_at"),)

    id: Mapped[UUID] = _uuid(primary_key=True)
    actor_subject_id: Mapped[UUID] = _fk_uuid(USERS_ID)
    actor_role: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    case_id: Mapped[UUID] = _fk_uuid(CASES_ID)
    request_id: Mapped[UUID | None] = _fk_uuid("business.representative_requests.id", nullable=True)
    field_code: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    occurred_at: Mapped[datetime] = _time()
