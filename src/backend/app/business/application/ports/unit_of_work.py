"""Unit of Work port: one short atomic transaction (architecture Sections 3, 18, 20).

No SQLAlchemy objects are exposed. Each module owns its own Unit of Work and sessions; no
transaction is shared with the AI module (decision D1, C2). `BusinessUnitOfWork` adds the
Business repositories and is used only inside the Business Module. Never hold one open across
an LLM call.
"""

from types import TracebackType
from typing import Protocol, Self, runtime_checkable

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


@runtime_checkable
class UnitOfWork(Protocol):
    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Roll back unless `commit()` succeeded."""

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...


@runtime_checkable
class BusinessUnitOfWork(UnitOfWork, Protocol):
    """One short Business transaction: all repositories share it; nothing persists without
    `commit()`. Internal to the Business Module (not a cross-module transaction contract).
    """

    users: UserDirectory
    profiles: EmployeeProfileRepository
    cases: CaseRepository
    alternatives: AlternativeRepository
    operations: OperationRepository
    option_result_sets: OptionResultSetRepository
    comparison_runs: ComparisonRunRepository
    representative_requests: RepresentativeRequestRepository


class BusinessUnitOfWorkFactory(Protocol):
    def __call__(self) -> BusinessUnitOfWork: ...
