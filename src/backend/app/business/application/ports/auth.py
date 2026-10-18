"""Identity store port (`auth.users`). Authentication and authorization services come in 3B.

No credentials are stored or exposed here: demo accounts have no password (tokens in 3B).
"""

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.contracts.tool_context import Role

_LOGIN = re.compile(r"^[a-z0-9._-]{2,64}$")


@dataclass(frozen=True, slots=True)
class UserAccount:
    user_id: UUID
    login: str
    roles: frozenset[Role]
    active: bool = True
    display_name: str | None = None

    def __post_init__(self) -> None:
        if not _LOGIN.fullmatch(self.login):
            raise ValueError(f"invalid login {self.login!r} (lowercase letters, digits, ._-)")
        if not self.roles:
            raise ValueError("a user needs at least one role")


@runtime_checkable
class UserDirectory(Protocol):
    async def get(self, user_id: UUID) -> UserAccount | None: ...

    async def get_by_login(self, login: str) -> UserAccount | None: ...

    async def add(self, account: UserAccount) -> None:
        """Raises `DuplicateResourceError` if the ID or login already exists."""
        ...
