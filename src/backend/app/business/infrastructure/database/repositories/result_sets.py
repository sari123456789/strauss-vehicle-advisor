"""`OptionResultSetRepository`: insert-only option lists (order preserved), scoped by case."""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import Table, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.business.domain.cases import OptionResultSetRecord
from app.business.domain.json_values import freeze_array
from app.business.infrastructure.database.models.cases import OptionResultSetRow
from app.business.infrastructure.database.repositories.codec import freeze_document, thaw

RESULT_SETS = cast(Table, OptionResultSetRow.__table__)
FINGERPRINT_KEY = "inputFingerprint"


def _record(row: Any) -> OptionResultSetRecord:
    return OptionResultSetRecord(
        result_set_id=row.id,
        case_id=row.case_id,
        case_version_used=row.case_version_used,
        reference_release_id=row.reference_release_id,
        request=freeze_document(row.request, "request"),
        items=freeze_array(row.items, path="items"),
        unchecked_categories=tuple(row.unchecked_categories),
        scope_complete=row.scope_complete,
        created_at=row.created_at,
    )


class SqlOptionResultSetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, record: OptionResultSetRecord) -> None:
        await self._session.execute(
            insert(RESULT_SETS).values(
                id=record.result_set_id,
                case_id=record.case_id,
                case_version_used=record.case_version_used,
                reference_release_id=record.reference_release_id,
                request=thaw(record.request),
                items=thaw(record.items),
                unchecked_categories=list(record.unchecked_categories),
                scope_complete=record.scope_complete,
                created_at=record.created_at,
            )
        )

    async def add_or_get_identical(
        self, record: OptionResultSetRecord
    ) -> tuple[OptionResultSetRecord, bool]:
        fingerprint = record.request.get(FINGERPRINT_KEY)
        if not isinstance(fingerprint, str):
            raise ValueError(f"result set request needs a string {FINGERPRINT_KEY!r}")
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"option_result_set:{record.case_id}:{fingerprint}"},
        )
        statement = (
            select(RESULT_SETS)
            .where(
                RESULT_SETS.c.case_id == record.case_id,
                RESULT_SETS.c.request[FINGERPRINT_KEY].astext == fingerprint,
            )
            .order_by(RESULT_SETS.c.created_at.desc(), RESULT_SETS.c.id)
            .limit(1)
        )
        existing = (await self._session.execute(statement)).first()
        if existing is not None:
            return _record(existing), False
        await self.add(record)
        return record, True

    async def get(self, case_id: UUID, result_set_id: UUID) -> OptionResultSetRecord | None:
        statement = select(RESULT_SETS).where(
            RESULT_SETS.c.case_id == case_id, RESULT_SETS.c.id == result_set_id
        )
        row = (await self._session.execute(statement)).first()
        return None if row is None else _record(row)
