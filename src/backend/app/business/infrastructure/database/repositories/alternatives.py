"""`AlternativeRepository` over `business.alternatives`. Alternatives are deactivated,
never deleted; kind, option key and catalogue ID are fixed after creation.
"""

from dataclasses import replace
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Table, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.business.application.ports.repositories import (
    DuplicateResourceError,
    VersionConflictError,
)
from app.business.domain.cases import Alternative, AlternativeKind
from app.business.domain.vehicles import Track
from app.business.infrastructure.database.models.cases import AlternativeRow
from app.business.infrastructure.database.repositories.codec import freeze_document, thaw

ALTERNATIVES = cast(Table, AlternativeRow.__table__)


def _alternative(row: Any) -> Alternative:
    return Alternative(
        alternative_id=row.id,
        case_id=row.case_id,
        version=row.version,
        kind=AlternativeKind(row.kind),
        option_key=row.option_key,
        catalogue_id=row.catalogue_id,
        track=None if row.track is None else Track(row.track),
        active=row.active,
        created_at=row.created_at,
        updated_at=row.updated_at,
        inputs=freeze_document(row.inputs, "inputs"),
        deactivated_at=row.deactivated_at,
    )


class SqlAlternativeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_case(
        self, case_id: UUID, *, active_only: bool = True
    ) -> tuple[Alternative, ...]:
        statement = select(ALTERNATIVES).where(ALTERNATIVES.c.case_id == case_id)
        if active_only:
            statement = statement.where(ALTERNATIVES.c.active)
        rows = await self._session.execute(
            statement.order_by(ALTERNATIVES.c.created_at, ALTERNATIVES.c.id)
        )
        return tuple(_alternative(r) for r in rows)

    async def get(self, case_id: UUID, alternative_id: UUID) -> Alternative | None:
        statement = select(ALTERNATIVES).where(
            ALTERNATIVES.c.case_id == case_id, ALTERNATIVES.c.id == alternative_id
        )
        row = (await self._session.execute(statement)).first()
        return None if row is None else _alternative(row)

    async def find_active(self, case_id: UUID, option_key: str) -> Alternative | None:
        statement = select(ALTERNATIVES).where(
            ALTERNATIVES.c.case_id == case_id,
            ALTERNATIVES.c.option_key == option_key,
            ALTERNATIVES.c.active,
        )
        row = (await self._session.execute(statement)).first()
        return None if row is None else _alternative(row)

    async def add(self, alternative: Alternative) -> None:
        statement = (
            insert(ALTERNATIVES)
            .values(
                id=alternative.alternative_id,
                case_id=alternative.case_id,
                version=alternative.version,
                kind=alternative.kind.value,
                option_key=alternative.option_key,
                catalogue_id=alternative.catalogue_id,
                track=None if alternative.track is None else alternative.track.value,
                inputs=thaw(alternative.inputs),
                active=alternative.active,
                created_at=alternative.created_at,
                updated_at=alternative.updated_at,
                deactivated_at=alternative.deactivated_at,
            )
            .on_conflict_do_nothing()
            .returning(ALTERNATIVES.c.id)
        )
        if (await self._session.execute(statement)).first() is None:
            raise DuplicateResourceError(
                f"alternative {alternative.alternative_id} or an active {alternative.option_key} "
                f"already exists in case {alternative.case_id}"
            )

    async def update(self, alternative: Alternative) -> Alternative:
        """Changes track, inputs and activation. Reactivating an option that is already active
        in the case violates the unique index; check `find_active` first."""
        statement = (
            update(ALTERNATIVES)
            .where(
                ALTERNATIVES.c.id == alternative.alternative_id,
                ALTERNATIVES.c.case_id == alternative.case_id,
                ALTERNATIVES.c.version == alternative.version,
            )
            .values(
                version=alternative.version + 1,
                track=None if alternative.track is None else alternative.track.value,
                inputs=thaw(alternative.inputs),
                active=alternative.active,
                updated_at=alternative.updated_at,
                deactivated_at=alternative.deactivated_at,
            )
            .returning(ALTERNATIVES.c.version)
        )
        new_version = (await self._session.execute(statement)).scalar_one_or_none()
        if new_version is None:
            raise VersionConflictError(
                "alternative", alternative.alternative_id, alternative.version
            )
        return replace(alternative, version=new_version)
