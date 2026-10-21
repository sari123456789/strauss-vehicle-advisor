"""`RequestAssignmentRepository` over `business.request_assignments`.

Concurrency guarantees
----------------------
A partial unique index `uq_request_assignments_one_active` (WHERE status = 'active')
enforces at most one active assignment per request at the PostgreSQL level.

The caller must hold a `FOR UPDATE` lock on the `representative_requests` row **before**
calling `add()`. This serialises concurrent Claims on the same request: the second caller
waits for the first to commit, then either finds the active assignment (and gets
`AssignmentConflictError`) or safely inserts its own row. The unique index is the safety net
for any path that skips the application lock.

`lock_active()` issues its own `SELECT ... FOR UPDATE`. Callers take it only after the
`representative_requests` row (global lock order: case -> request -> assignment).
"""

from dataclasses import replace
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Table, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.business.application.ports.repositories import (
    AssignmentConflictError,
    QueueCounts,
    VersionConflictError,
)
from app.business.domain.representatives import (
    AssignmentStatus,
    RequestAssignment,
)
from app.business.infrastructure.database.models.answers import RequestAssignmentRow

ASSIGNMENTS = cast(Table, RequestAssignmentRow.__table__)

_UNIQUE_VIOLATION = "23505"

_COUNTS = text(
    """
    SELECT
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE rr.status = 'open' AND ra.id IS NULL) AS unclaimed,
        COUNT(*) FILTER (WHERE ra.claimed_by = :viewer) AS assigned_to_me,
        COUNT(*) FILTER (WHERE ra.id IS NOT NULL AND ra.claimed_by <> :viewer)
            AS assigned_to_others,
        COUNT(*) FILTER (WHERE rr.status = 'answered') AS answered,
        COUNT(*) FILTER (WHERE rr.status = 'cannot_determine') AS cannot_determine,
        COUNT(*) FILTER (WHERE rr.status = 'cancelled') AS cancelled
    FROM business.representative_requests rr
    LEFT JOIN business.request_assignments ra
        ON ra.request_id = rr.id AND ra.status = 'active'
    """
)


def assignment_from_row(row: Any) -> RequestAssignment:
    return RequestAssignment(
        assignment_id=row.id,
        request_id=row.request_id,
        claimed_by=row.claimed_by,
        claimed_at=row.claimed_at,
        assignment_version=row.assignment_version,
        status=AssignmentStatus(row.status),
        released_at=row.released_at,
        completed_at=row.completed_at,
        cancelled_at=row.cancelled_at,
    )


class SqlRequestAssignmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _one(self, *conditions: Any) -> RequestAssignment | None:
        row = (await self._session.execute(select(ASSIGNMENTS).where(*conditions))).first()
        return None if row is None else assignment_from_row(row)

    async def get_active(self, request_id: UUID) -> RequestAssignment | None:
        """Unlocked read of the active assignment (if any)."""
        return await self._one(
            ASSIGNMENTS.c.request_id == request_id,
            ASSIGNMENTS.c.status == AssignmentStatus.ACTIVE.value,
        )

    async def lock_active(self, request_id: UUID) -> RequestAssignment | None:
        """SELECT FOR UPDATE on the active assignment row.

        Must be called while the representative_requests row is already locked by the
        caller (lock order: request -> assignment).
        """
        statement = (
            select(ASSIGNMENTS)
            .where(
                ASSIGNMENTS.c.request_id == request_id,
                ASSIGNMENTS.c.status == AssignmentStatus.ACTIVE.value,
            )
            .with_for_update()
        )
        row = (await self._session.execute(statement)).first()
        return None if row is None else assignment_from_row(row)

    async def add(self, assignment: RequestAssignment) -> None:
        """Insert a new assignment row.

        Raises `AssignmentConflictError` if the partial unique index is violated.
        """
        try:
            await self._session.execute(
                insert(ASSIGNMENTS).values(
                    id=assignment.assignment_id,
                    request_id=assignment.request_id,
                    claimed_by=assignment.claimed_by,
                    claimed_at=assignment.claimed_at,
                    assignment_version=assignment.assignment_version,
                    status=assignment.status.value,
                    released_at=assignment.released_at,
                    completed_at=assignment.completed_at,
                    cancelled_at=assignment.cancelled_at,
                )
            )
        except Exception as exc:
            # SQLAlchemy wraps the psycopg error; the SQLSTATE identifies the violation.
            sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
            if sqlstate == _UNIQUE_VIOLATION:
                raise AssignmentConflictError(
                    f"request {assignment.request_id} already has an active assignment"
                ) from exc
            raise

    async def update_status(
        self,
        assignment: RequestAssignment,
        *,
        new_status: AssignmentStatus,
        released_at: Any = None,
        completed_at: Any = None,
        cancelled_at: Any = None,
    ) -> RequestAssignment:
        """Optimistic update on assignment_version; returns the updated assignment."""
        stmt = (
            update(ASSIGNMENTS)
            .where(
                ASSIGNMENTS.c.id == assignment.assignment_id,
                ASSIGNMENTS.c.assignment_version == assignment.assignment_version,
            )
            .values(
                status=new_status.value,
                assignment_version=assignment.assignment_version + 1,
                released_at=released_at,
                completed_at=completed_at,
                cancelled_at=cancelled_at,
            )
            .returning(ASSIGNMENTS.c.assignment_version)
        )
        new_version = (await self._session.execute(stmt)).scalar_one_or_none()
        if new_version is None:
            raise VersionConflictError(
                "request_assignment", assignment.assignment_id, assignment.assignment_version
            )
        return replace(
            assignment,
            status=new_status,
            assignment_version=new_version,
            released_at=released_at,
            completed_at=completed_at,
            cancelled_at=cancelled_at,
        )

    async def counts(self, viewer_id: UUID) -> QueueCounts:
        row = (await self._session.execute(_COUNTS, {"viewer": viewer_id})).one()
        return QueueCounts(
            total=int(row.total),
            unclaimed=int(row.unclaimed),
            assigned_to_me=int(row.assigned_to_me),
            assigned_to_others=int(row.assigned_to_others),
            answered=int(row.answered),
            cannot_determine=int(row.cannot_determine),
            cancelled=int(row.cancelled),
        )

    async def list_for_request(self, request_id: UUID) -> tuple[RequestAssignment, ...]:
        """All assignments for a request, oldest first (claim history)."""
        rows = await self._session.execute(
            select(ASSIGNMENTS)
            .where(ASSIGNMENTS.c.request_id == request_id)
            .order_by(ASSIGNMENTS.c.claimed_at, ASSIGNMENTS.c.id)
        )
        return tuple(assignment_from_row(r) for r in rows)
