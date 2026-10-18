"""`ComparisonRunRepository`: insert-only calculation records, scoped by case."""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import Table, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.business.domain.cases import ComparisonRunRecord, RunKind
from app.business.infrastructure.database.models.cases import ComparisonRunRow
from app.business.infrastructure.database.repositories.codec import freeze_document, thaw

RUNS = cast(Table, ComparisonRunRow.__table__)


def _record(row: Any) -> ComparisonRunRecord:
    return ComparisonRunRecord(
        run_id=row.id,
        case_id=row.case_id,
        kind=RunKind(row.kind),
        calculation_month=row.calculation_month,
        case_version_used=row.case_version_used,
        profile_version_used=row.profile_version_used,
        reference_release_id=row.reference_release_id,
        rules_snapshot_id=row.rules_snapshot_id,
        catalogue_version_id=row.catalogue_version_id,
        tax_dataset_version_id=row.tax_dataset_version_id,
        selection=freeze_document(row.selection, "selection"),
        include_current=row.include_current,
        input_snapshot=freeze_document(row.input_snapshot, "input_snapshot"),
        result=freeze_document(row.result, "result"),
        operation_id=row.operation_id,
        created_by=row.created_by,
        created_at=row.created_at,
        option_result_set_id=row.option_result_set_id,
    )


class SqlComparisonRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, record: ComparisonRunRecord) -> None:
        await self._session.execute(
            insert(RUNS).values(
                id=record.run_id,
                case_id=record.case_id,
                kind=record.kind.value,
                calculation_month=record.calculation_month,
                case_version_used=record.case_version_used,
                profile_version_used=record.profile_version_used,
                reference_release_id=record.reference_release_id,
                rules_snapshot_id=record.rules_snapshot_id,
                catalogue_version_id=record.catalogue_version_id,
                tax_dataset_version_id=record.tax_dataset_version_id,
                option_result_set_id=record.option_result_set_id,
                selection=thaw(record.selection),
                include_current=record.include_current,
                input_snapshot=thaw(record.input_snapshot),
                result=thaw(record.result),
                operation_id=record.operation_id,
                created_by=record.created_by,
                created_at=record.created_at,
            )
        )

    async def get(self, case_id: UUID, run_id: UUID) -> ComparisonRunRecord | None:
        statement = select(RUNS).where(RUNS.c.case_id == case_id, RUNS.c.id == run_id)
        row = (await self._session.execute(statement)).first()
        return None if row is None else _record(row)

    async def list_recent(self, case_id: UUID, limit: int) -> tuple[ComparisonRunRecord, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        statement = (
            select(RUNS)
            .where(RUNS.c.case_id == case_id)
            .order_by(RUNS.c.created_at.desc(), RUNS.c.id.desc())
            .limit(limit)
        )
        return tuple(_record(r) for r in await self._session.execute(statement))
