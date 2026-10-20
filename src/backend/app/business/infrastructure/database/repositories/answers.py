"""Repositories for representative answers, case representative facts, audit entries and the
integration outbox (B3a). All four are insert-only from the Unit of Work; outbox delivery and
acknowledgement live in `event_feed.py`.
"""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import Table, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.business.application.ports.repositories import AuditEntry, OutboxEvent
from app.business.domain.cases import FactPath
from app.business.domain.representatives import (
    AnswerApplication,
    AnswerOutcome,
    NotApplicableReason,
    RepresentativeAnswer,
    RepresentativeFact,
)
from app.business.domain.rules import EntitlementGroup
from app.business.domain.vehicles import Propulsion
from app.business.infrastructure.database.models.answers import (
    AuditEntryRow,
    CaseRepresentativeFactRow,
    IntegrationOutboxRow,
    RepresentativeAnswerRow,
)
from app.business.infrastructure.database.repositories.codec import (
    PersistenceFormatError,
    thaw,
)

ANSWERS = cast(Table, RepresentativeAnswerRow.__table__)
FACTS = cast(Table, CaseRepresentativeFactRow.__table__)
AUDIT = cast(Table, AuditEntryRow.__table__)
OUTBOX = cast(Table, IntegrationOutboxRow.__table__)


def _answer(row: Any) -> RepresentativeAnswer:
    return RepresentativeAnswer(
        answer_id=row.id,
        request_id=row.request_id,
        case_id=row.case_id,
        outcome=AnswerOutcome(row.outcome),
        answered_by=row.answered_by,
        answered_at=row.answered_at,
        operation_id=row.operation_id,
        value=row.value,
        unit=row.unit,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        note=row.note,
        application=None if row.application is None else AnswerApplication(row.application),
        application_reason=None
        if row.application_reason is None
        else NotApplicableReason(row.application_reason),
    )


class SqlRepresentativeAnswerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, answer: RepresentativeAnswer) -> None:
        await self._session.execute(
            insert(ANSWERS).values(
                id=answer.answer_id,
                request_id=answer.request_id,
                case_id=answer.case_id,
                outcome=answer.outcome.value,
                value=answer.value,
                unit=answer.unit,
                effective_from=answer.effective_from,
                effective_to=answer.effective_to,
                note=answer.note,
                application=None if answer.application is None else answer.application.value,
                application_reason=None
                if answer.application_reason is None
                else answer.application_reason.value,
                answered_by=answer.answered_by,
                answered_at=answer.answered_at,
                operation_id=answer.operation_id,
            )
        )

    async def get(self, request_id: UUID) -> RepresentativeAnswer | None:
        row = (
            await self._session.execute(select(ANSWERS).where(ANSWERS.c.request_id == request_id))
        ).first()
        return None if row is None else _answer(row)


def _fact_value(path: FactPath, raw: object) -> EntitlementGroup | Propulsion:
    if not isinstance(raw, str):
        raise PersistenceFormatError(f"case fact {path}: expected a string value")
    if path is FactPath.ENTITLEMENT_GROUP:
        return EntitlementGroup(raw)
    if path is FactPath.CURRENT_PROPULSION:
        return Propulsion(raw)
    raise PersistenceFormatError(f"{path} is not a representative case fact")


class SqlCaseFactRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, fact: RepresentativeFact) -> None:
        await self._session.execute(
            insert(FACTS).values(
                id=fact.fact_id,
                case_id=fact.case_id,
                request_id=fact.request_id,
                field_path=fact.path.value,
                value=fact.value.value,
                effective_from=fact.effective_from,
                effective_to=fact.effective_to,
                answered_at=fact.answered_at,
            )
        )

    async def list_for_case(self, case_id: UUID) -> tuple[RepresentativeFact, ...]:
        rows = await self._session.execute(
            select(FACTS)
            .where(FACTS.c.case_id == case_id)
            .order_by(FACTS.c.answered_at, FACTS.c.id)
        )
        facts = []
        for row in rows:
            path = FactPath(row.field_path)
            facts.append(
                RepresentativeFact(
                    fact_id=row.id,
                    case_id=row.case_id,
                    request_id=row.request_id,
                    path=path,
                    value=_fact_value(path, row.value),
                    answered_at=row.answered_at,
                    effective_from=row.effective_from,
                    effective_to=row.effective_to,
                )
            )
        return tuple(facts)


class SqlAuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entry: AuditEntry) -> None:
        await self._session.execute(
            insert(AUDIT).values(
                id=entry.entry_id,
                actor_subject_id=entry.actor_subject_id,
                actor_role=entry.actor_role,
                action=entry.action,
                case_id=entry.case_id,
                request_id=entry.request_id,
                field_code=entry.field_code,
                details=None if entry.details is None else thaw(entry.details),
                occurred_at=entry.occurred_at,
            )
        )


class SqlOutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: OutboxEvent) -> None:
        await self._session.execute(
            insert(OUTBOX).values(
                id=event.event_id,
                event_type=event.event_type,
                schema_version=event.schema_version,
                payload=thaw(event.payload),
                occurred_at=event.occurred_at,
            )
        )
