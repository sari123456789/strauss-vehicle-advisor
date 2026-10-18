"""`SqlUnitOfWork`: one short Business transaction over one `AsyncSession`.

All Business repositories share the session; nothing persists unless `commit()` succeeds
(`__aexit__` rolls back otherwise). Each instance is single-use. The session never leaves
Business Infrastructure (no Application port exposes it).

The Business UoW holds Business repositories only (decision D1, C2): AI inputs are never
written from a Business transaction.
"""

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.application.ports.auth import UserDirectory
from app.business.application.ports.repositories import (
    AlternativeRepository,
    CaseRepository,
    ComparisonRunRepository,
    EmployeeProfileRepository,
    OperationRepository,
    OptionResultSetRepository,
    RepresentativeRequestRepository,
)
from app.business.infrastructure.database.repositories.alternatives import (
    SqlAlternativeRepository,
)
from app.business.infrastructure.database.repositories.cases import SqlCaseRepository
from app.business.infrastructure.database.repositories.operations import SqlOperationRepository
from app.business.infrastructure.database.repositories.profiles import (
    SqlEmployeeProfileRepository,
)
from app.business.infrastructure.database.repositories.representatives import (
    SqlRepresentativeRequestRepository,
)
from app.business.infrastructure.database.repositories.result_sets import (
    SqlOptionResultSetRepository,
)
from app.business.infrastructure.database.repositories.runs import SqlComparisonRunRepository
from app.business.infrastructure.database.repositories.users import SqlUserDirectory


class SqlUnitOfWork:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._session = sessions()
        self._entered = False
        self._finished = False
        self._committed = False
        self.users: UserDirectory = SqlUserDirectory(self._session)
        self.profiles: EmployeeProfileRepository = SqlEmployeeProfileRepository(self._session)
        self.cases: CaseRepository = SqlCaseRepository(self._session)
        self.alternatives: AlternativeRepository = SqlAlternativeRepository(self._session)
        self.operations: OperationRepository = SqlOperationRepository(self._session)
        self.option_result_sets: OptionResultSetRepository = SqlOptionResultSetRepository(
            self._session
        )
        self.comparison_runs: ComparisonRunRepository = SqlComparisonRunRepository(self._session)
        self.representative_requests: RepresentativeRequestRepository = (
            SqlRepresentativeRequestRepository(self._session)
        )

    async def __aenter__(self) -> Self:
        if self._entered:
            raise RuntimeError("a unit of work is single-use")
        self._entered = True
        await self._session.begin()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if not self._committed:
                await self._session.rollback()
        finally:
            self._finished = True
            await self._session.close()

    def _require_active(self) -> None:
        if not self._entered or self._finished or self._committed:
            raise RuntimeError("the unit of work is not active")

    async def commit(self) -> None:
        self._require_active()
        await self._session.commit()
        self._committed = True

    async def rollback(self) -> None:
        self._require_active()
        await self._session.rollback()
        self._finished = True


class SqlUnitOfWorkFactory:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    def __call__(self) -> SqlUnitOfWork:
        return SqlUnitOfWork(self._sessions)
