"""`CaseRepository` over `business.cases`. Reads are always scoped by the case owner."""

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
from app.business.domain.cases import CaseLifecycleStatus, ComparisonCase
from app.business.infrastructure.database.models.cases import CaseRow
from app.business.infrastructure.database.repositories.codec import (
    decode_facts,
    decode_overrides,
    encode_facts,
    encode_overrides,
)

CASES = cast(Table, CaseRow.__table__)


def _case(row: Any) -> ComparisonCase:
    return ComparisonCase(
        case_id=row.id,
        conversation_id=row.conversation_id,
        owner_subject_id=row.owner_subject_id,
        calculation_month=row.calculation_month,
        reference_date=row.reference_date,
        version=row.version,
        lifecycle_status=CaseLifecycleStatus(row.lifecycle_status),
        actual_facts=decode_facts(
            row.actual_facts,
            employee_id=row.owner_subject_id,
            version=row.salary_profile_version_used,
        ),
        created_at=row.created_at,
        updated_at=row.updated_at,
        hypothetical_overrides=decode_overrides(row.hypothetical_overrides),
    )


def _mutable_values(case: ComparisonCase) -> dict[str, Any]:
    return {
        "calculation_month": case.calculation_month,
        "reference_date": case.reference_date,
        "lifecycle_status": case.lifecycle_status.value,
        "salary_profile_version_used": case.salary_profile_version_used,
        "actual_facts": encode_facts(case.actual_facts),
        "hypothetical_overrides": encode_overrides(case.hypothetical_overrides),
        "updated_at": case.updated_at,
    }


class SqlCaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _one(self, *conditions: Any) -> ComparisonCase | None:
        row = (await self._session.execute(select(CASES).where(*conditions))).first()
        return None if row is None else _case(row)

    async def get(self, case_id: UUID, *, owner_subject_id: UUID) -> ComparisonCase | None:
        return await self._one(CASES.c.id == case_id, CASES.c.owner_subject_id == owner_subject_id)

    async def lock(self, case_id: UUID, *, owner_subject_id: UUID) -> ComparisonCase | None:
        statement = (
            select(CASES)
            .where(CASES.c.id == case_id, CASES.c.owner_subject_id == owner_subject_id)
            .with_for_update()
        )
        row = (await self._session.execute(statement)).first()
        return None if row is None else _case(row)

    async def get_by_conversation(
        self, conversation_id: UUID, *, owner_subject_id: UUID
    ) -> ComparisonCase | None:
        return await self._one(
            CASES.c.conversation_id == conversation_id,
            CASES.c.owner_subject_id == owner_subject_id,
        )

    async def add(self, case: ComparisonCase) -> None:
        statement = (
            insert(CASES)
            .values(
                id=case.case_id,
                conversation_id=case.conversation_id,
                owner_subject_id=case.owner_subject_id,
                version=case.version,
                created_at=case.created_at,
                **_mutable_values(case),
            )
            .on_conflict_do_nothing()
            .returning(CASES.c.id)
        )
        if (await self._session.execute(statement)).first() is None:
            raise DuplicateResourceError(
                f"case {case.case_id} or a case for conversation {case.conversation_id} exists"
            )

    async def update(self, case: ComparisonCase) -> ComparisonCase:
        statement = (
            update(CASES)
            .where(
                CASES.c.id == case.case_id,
                CASES.c.owner_subject_id == case.owner_subject_id,
                CASES.c.version == case.version,
            )
            .values(version=case.version + 1, **_mutable_values(case))
            .returning(CASES.c.version)
        )
        new_version = (await self._session.execute(statement)).scalar_one_or_none()
        if new_version is None:
            raise VersionConflictError("case", case.case_id, case.version)
        return replace(case, version=new_version)
