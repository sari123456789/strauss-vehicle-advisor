"""Unit of Work port: one short atomic transaction (architecture Sections 3, 18, 20).

No SQLAlchemy objects are exposed. Business repositories are added to this Protocol with the
persistence phase. A Unit of Work satisfies `app.contracts.case_lifecycle.TransactionHandle`.
"""

from types import TracebackType
from typing import Protocol, Self, runtime_checkable

from app.application.ports.input_queue import DurableInputQueue


@runtime_checkable
class UnitOfWork(Protocol):
    input_queue: DurableInputQueue

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
