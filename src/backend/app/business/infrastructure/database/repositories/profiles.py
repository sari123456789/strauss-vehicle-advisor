"""`EmployeeProfileRepository` over `business.employee_profiles` (one row per employee)."""

from dataclasses import replace
from typing import cast
from uuid import UUID

from sqlalchemy import Table, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.business.application.ports.repositories import (
    DuplicateResourceError,
    VersionConflictError,
)
from app.business.domain.employee_profiles import EmployeeProfile
from app.business.domain.json_values import JsonObject
from app.business.infrastructure.database.models.cases import EmployeeProfileRow
from app.business.infrastructure.database.repositories.codec import (
    decode_facts,
    encode_facts,
    freeze_document,
    thaw,
)

PROFILES = cast(Table, EmployeeProfileRow.__table__)


class SqlEmployeeProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, employee_id: UUID) -> EmployeeProfile | None:
        statement = select(PROFILES.c.version, PROFILES.c.fields).where(
            PROFILES.c.employee_id == employee_id
        )
        row = (await self._session.execute(statement)).first()
        if row is None:
            return None
        return decode_facts(row.fields, employee_id=employee_id, version=row.version)

    async def lock(self, employee_id: UUID) -> EmployeeProfile | None:
        statement = (
            select(PROFILES.c.version, PROFILES.c.fields)
            .where(PROFILES.c.employee_id == employee_id)
            .with_for_update()
        )
        row = (await self._session.execute(statement)).first()
        if row is None:
            return None
        return decode_facts(row.fields, employee_id=employee_id, version=row.version)

    async def add(self, profile: EmployeeProfile, *, origin: JsonObject | None = None) -> None:
        statement = (
            insert(PROFILES)
            .values(
                employee_id=profile.employee_id,
                version=profile.version,
                fields=encode_facts(profile),
                origin=None if origin is None else thaw(origin),
            )
            .on_conflict_do_nothing()
            .returning(PROFILES.c.employee_id)
        )
        if (await self._session.execute(statement)).first() is None:
            raise DuplicateResourceError(f"employee profile {profile.employee_id} exists")

    async def update(self, profile: EmployeeProfile) -> EmployeeProfile:
        statement = (
            update(PROFILES)
            .where(
                PROFILES.c.employee_id == profile.employee_id,
                PROFILES.c.version == profile.version,
            )
            .values(
                version=profile.version + 1,
                fields=encode_facts(profile),
                updated_at=func.now(),
            )
            .returning(PROFILES.c.version)
        )
        new_version = (await self._session.execute(statement)).scalar_one_or_none()
        if new_version is None:
            raise VersionConflictError("employee_profile", profile.employee_id, profile.version)
        return replace(profile, version=new_version)

    async def get_origin(self, employee_id: UUID) -> JsonObject | None:
        statement = select(PROFILES.c.origin).where(PROFILES.c.employee_id == employee_id)
        origin = (await self._session.execute(statement)).scalar_one_or_none()
        return None if origin is None else freeze_document(origin, "origin")
