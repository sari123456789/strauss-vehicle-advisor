"""`RepresentativeRequestRepository` over `business.representative_requests`.

`add_or_get_open` never aborts the transaction on a duplicate: the insert uses ON CONFLICT DO
NOTHING and the existing request (same idempotency key, or the equivalent open request) is
returned instead.
"""

from collections.abc import Collection
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Table, Text, and_, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.business.application.ports.repositories import (
    QueueEntry,
    QueueQuery,
    QueueScope,
    VersionConflictError,
)
from app.business.domain.representatives import (
    AssignmentStatus,
    ExpectedValueType,
    RepresentativeFieldCode,
    RepresentativeRequest,
    RequestStatus,
)
from app.business.infrastructure.database.models.answers import RequestAssignmentRow
from app.business.infrastructure.database.models.cases import RepresentativeRequestRow
from app.business.infrastructure.database.repositories.assignments import assignment_from_row
from app.business.infrastructure.database.repositories.codec import freeze_document, thaw

REQUESTS = cast(Table, RepresentativeRequestRow.__table__)
ASSIGNMENTS = cast(Table, RequestAssignmentRow.__table__)
"""Joined as the ACTIVE assignment only (the partial unique index keeps it to at most one)."""


def _request(row: Any) -> RepresentativeRequest:
    return RepresentativeRequest(
        request_id=row.id,
        case_id=row.case_id,
        field_code=RepresentativeFieldCode(row.field_code),
        expected_value_type=ExpectedValueType(row.expected_value_type),
        question_text=row.question_text,
        subject_snapshot=freeze_document(row.subject_snapshot, "subject_snapshot"),
        subject_fingerprint=row.subject_fingerprint,
        case_version_at_creation=row.case_version_at_creation,
        request_version=row.request_version,
        status=RequestStatus(row.status),
        idempotency_key=row.idempotency_key,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        alternative_id=row.alternative_id,
        alternative_version=row.alternative_version,
        option_key=row.option_key,
    )


def _queue_scope_conditions(query: QueueQuery) -> list[Any]:
    viewer = query.viewer_id
    if query.scope is QueueScope.UNCLAIMED:
        return [
            REQUESTS.c.status == RequestStatus.OPEN.value,
            ASSIGNMENTS.c.id.is_(None),
        ]
    if query.scope is QueueScope.MINE:
        return [ASSIGNMENTS.c.claimed_by == viewer]
    if query.scope is QueueScope.OTHERS:
        return [ASSIGNMENTS.c.id.is_not(None), ASSIGNMENTS.c.claimed_by != viewer]
    return []


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _queue_entry(row: Any) -> QueueEntry:
    mapping = row._mapping
    assignment = None
    if mapping["a_id"] is not None:
        assignment = assignment_from_row(
            SimpleNamespace(**{c.name: mapping[f"a_{c.name}"] for c in ASSIGNMENTS.c})
        )
    return QueueEntry(request=_request(row), assignment=assignment)


class SqlRepresentativeRequestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _one(self, *conditions: Any) -> RepresentativeRequest | None:
        row = (await self._session.execute(select(REQUESTS).where(*conditions))).first()
        return None if row is None else _request(row)

    async def _by_key(self, request: RepresentativeRequest) -> RepresentativeRequest | None:
        return await self._one(
            REQUESTS.c.case_id == request.case_id,
            REQUESTS.c.idempotency_key == request.idempotency_key,
        )

    async def add_or_get_open(
        self, request: RepresentativeRequest
    ) -> tuple[RepresentativeRequest, bool]:
        existing = await self._by_key(request)
        if existing is not None:
            return existing, False
        statement = (
            insert(REQUESTS)
            .values(
                id=request.request_id,
                case_id=request.case_id,
                alternative_id=request.alternative_id,
                alternative_version=request.alternative_version,
                option_key=request.option_key,
                field_code=request.field_code.value,
                expected_value_type=request.expected_value_type.value,
                question_text=request.question_text,
                subject_snapshot=thaw(request.subject_snapshot),
                subject_fingerprint=request.subject_fingerprint,
                case_version_at_creation=request.case_version_at_creation,
                request_version=request.request_version,
                status=request.status.value,
                idempotency_key=request.idempotency_key,
                created_by=request.created_by,
                created_at=request.created_at,
                updated_at=request.updated_at,
            )
            .on_conflict_do_nothing()
            .returning(REQUESTS.c.id)
        )
        if (await self._session.execute(statement)).first() is not None:
            return request, True
        existing = await self._by_key(request) or await self._one(
            REQUESTS.c.case_id == request.case_id,
            REQUESTS.c.alternative_id.is_not_distinct_from(request.alternative_id),
            REQUESTS.c.field_code == request.field_code.value,
            REQUESTS.c.subject_fingerprint == request.subject_fingerprint,
            REQUESTS.c.status == RequestStatus.OPEN.value,
        )
        if existing is None:
            raise RuntimeError(f"request {request.request_id}: conflict without an existing row")
        return existing, False

    async def get(self, case_id: UUID, request_id: UUID) -> RepresentativeRequest | None:
        return await self._one(REQUESTS.c.case_id == case_id, REQUESTS.c.id == request_id)

    async def get_by_id(self, request_id: UUID) -> RepresentativeRequest | None:
        return await self._one(REQUESTS.c.id == request_id)

    async def lock(self, request_id: UUID) -> RepresentativeRequest | None:
        statement = select(REQUESTS).where(REQUESTS.c.id == request_id).with_for_update()
        row = (await self._session.execute(statement)).first()
        return None if row is None else _request(row)

    async def list_queue(self, query: QueueQuery) -> tuple[QueueEntry, ...]:
        assignment_columns = [c.label(f"a_{c.name}") for c in ASSIGNMENTS.c]
        join = REQUESTS.outerjoin(
            ASSIGNMENTS,
            and_(
                ASSIGNMENTS.c.request_id == REQUESTS.c.id,
                ASSIGNMENTS.c.status == AssignmentStatus.ACTIVE.value,
            ),
        )
        statement = select(REQUESTS, *assignment_columns).select_from(join)
        if query.statuses is not None:
            statement = statement.where(REQUESTS.c.status.in_([s.value for s in query.statuses]))
        scope = _queue_scope_conditions(query)
        if scope:
            statement = statement.where(*scope)
        if query.field_code is not None:
            statement = statement.where(REQUESTS.c.field_code == query.field_code)
        if query.search:
            pattern = f"%{_escape_like(query.search)}%"
            statement = statement.where(
                or_(
                    REQUESTS.c.question_text.ilike(pattern, escape="\\"),
                    REQUESTS.c.id.cast(Text).ilike(pattern, escape="\\"),
                )
            )
        if query.after_created_at is not None and query.after_id is not None:
            statement = statement.where(
                tuple_(REQUESTS.c.created_at, REQUESTS.c.id)
                > tuple_(query.after_created_at, query.after_id)
            )
        rows = await self._session.execute(
            statement.order_by(REQUESTS.c.created_at, REQUESTS.c.id).limit(query.limit)
        )
        return tuple(_queue_entry(row) for row in rows)

    async def list_for_case(
        self, case_id: UUID, statuses: Collection[RequestStatus] | None = None
    ) -> tuple[RepresentativeRequest, ...]:
        statement = select(REQUESTS).where(REQUESTS.c.case_id == case_id)
        if statuses is not None:
            statement = statement.where(REQUESTS.c.status.in_([s.value for s in statuses]))
        rows = await self._session.execute(statement.order_by(REQUESTS.c.created_at, REQUESTS.c.id))
        return tuple(_request(r) for r in rows)

    async def list_for_creator(self, created_by: UUID) -> tuple[RepresentativeRequest, ...]:
        statement = (
            select(REQUESTS)
            .where(REQUESTS.c.created_by == created_by)
            .order_by(REQUESTS.c.created_at.desc(), REQUESTS.c.id.desc())
            .limit(100)
        )
        rows = await self._session.execute(statement)
        return tuple(_request(r) for r in rows)

    async def update(self, request: RepresentativeRequest) -> RepresentativeRequest:
        statement = (
            update(REQUESTS)
            .where(
                REQUESTS.c.id == request.request_id,
                REQUESTS.c.case_id == request.case_id,
                REQUESTS.c.request_version == request.request_version,
            )
            .values(
                request_version=request.request_version + 1,
                status=request.status.value,
                updated_at=request.updated_at,
            )
            .returning(REQUESTS.c.request_version)
        )
        new_version = (await self._session.execute(statement)).scalar_one_or_none()
        if new_version is None:
            raise VersionConflictError(
                "representative_request", request.request_id, request.request_version
            )
        return replace(request, request_version=new_version)
