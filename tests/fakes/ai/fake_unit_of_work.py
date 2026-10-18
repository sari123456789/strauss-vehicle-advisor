"""In-memory AI-side unit of work for the durable input queue fake (provisional, C3).

Staged writes become visible only on commit; otherwise rolled back. Not a Business UoW.
"""

from types import TracebackType
from typing import Self

from tests.fakes.ai.fake_input_queue import InMemoryInputQueue


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
