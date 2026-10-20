"""Exclusive execution of one LangGraph thread (AI-internal port).

An expired lease does not prove that its holder has stopped (a paused process can wake up
later), so a worker runs a thread - and may take over an expired claim on it - only while it
holds that thread's lock. The lock must be released automatically when its holder dies (for
PostgreSQL: a session-level advisory lock on a dedicated connection).
"""

from contextlib import AbstractAsyncContextManager
from typing import Protocol, runtime_checkable


@runtime_checkable
class ThreadLock(Protocol):
    def hold(self, thread_id: str) -> AbstractAsyncContextManager[bool]:
        """Try to take the thread's lock without waiting.

        Yields True while the lock is held (released on exit), or False when another holder
        has it - the caller must then leave the thread alone.
        """
        ...
