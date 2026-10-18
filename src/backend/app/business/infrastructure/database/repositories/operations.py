"""`OperationRepository`: idempotency records written in the same transaction as the mutation.

`claim` inserts with ON CONFLICT DO NOTHING. A concurrent claim of the same key waits on the
unique index until the first transaction ends: after its commit the second caller gets a
`Replay`; after its rollback the second insert succeeds. No in-progress state is needed for
synchronous operations, and the transaction is never aborted by a unique violation.
"""

from typing import cast
from uuid import uuid4

from sqlalchemy import ColumnElement, Table, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.business.application.ports.repositories import (
    Claimed,
    ClaimResult,
    OperationKey,
    OperationPayloadMismatchError,
    Replay,
)
from app.business.domain.json_values import JsonObject
from app.business.infrastructure.database.models.cases import OperationRow
from app.business.infrastructure.database.repositories.codec import freeze_document, thaw

OPERATIONS = cast(Table, OperationRow.__table__)


def _key_matches(key: OperationKey) -> tuple[ColumnElement[bool], ...]:
    c = OPERATIONS.c
    return (
        c.actor_subject_id == key.actor_subject_id,
        c.scope_kind == key.scope_kind.value,
        c.scope_id == key.scope_id,
        c.operation_type == key.operation_type,
        c.operation_id == key.operation_id,
    )


class SqlOperationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(self, key: OperationKey, request_hash: str) -> ClaimResult:
        statement = (
            insert(OPERATIONS)
            .values(
                id=uuid4(),
                actor_subject_id=key.actor_subject_id,
                scope_kind=key.scope_kind.value,
                scope_id=key.scope_id,
                operation_type=key.operation_type,
                operation_id=key.operation_id,
                request_hash=request_hash,
            )
            .on_conflict_do_nothing()
            .returning(OPERATIONS.c.id)
        )
        if (await self._session.execute(statement)).first() is not None:
            return Claimed(key)
        existing = (
            await self._session.execute(
                select(OPERATIONS.c.request_hash, OPERATIONS.c.result).where(*_key_matches(key))
            )
        ).first()
        if existing is None:
            raise RuntimeError(f"operation {key.operation_id}: claim conflict without a record")
        if existing.request_hash != request_hash:
            raise OperationPayloadMismatchError(
                f"operation {key.operation_id} was already used with a different payload"
            )
        result = None if existing.result is None else freeze_document(existing.result, "result")
        return Replay(key, result)

    async def complete(self, key: OperationKey, result: JsonObject) -> None:
        statement = (
            update(OPERATIONS)
            .where(*_key_matches(key), OPERATIONS.c.result.is_(None))
            .values(result=thaw(result))
            .returning(OPERATIONS.c.id)
        )
        if (await self._session.execute(statement)).first() is None:
            raise RuntimeError(f"operation {key.operation_id} is not an open claim")
