"""In-memory `UnitOfWork`: staged writes become visible only on commit; otherwise rolled back."""

from types import TracebackType
from typing import Self

from tests.fakes.fake_input_queue import InMemoryInputQueue


class InMemoryUnitOfWork:
    def __init__(self, input_queue: InMemoryInputQueue | None = None) -> None:
        self.input_queue = input_queue or InMemoryInputQueue()
        self.committed = False

    async def __aenter__(self) -> Self:
        self.committed = False
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if not self.committed:
            await self.rollback()

    async def commit(self) -> None:
        self.input_queue.commit_staged()
        self.committed = True

    async def rollback(self) -> None:
        self.input_queue.discard_staged()
