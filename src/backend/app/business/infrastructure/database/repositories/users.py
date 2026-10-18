"""`UserDirectory` over `auth.users` (bound to one Unit of Work session)."""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import Table, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.business.application.ports.auth import UserAccount
from app.business.application.ports.repositories import DuplicateResourceError
from app.business.infrastructure.database.models.identity import UserRow
from app.contracts.tool_context import Role

USERS = cast(Table, UserRow.__table__)


def _account(row: Any) -> UserAccount:
    return UserAccount(
        user_id=row.id,
        login=row.login,
        roles=frozenset(Role(r) for r in row.roles),
        active=row.active,
        display_name=row.display_name,
    )


class SqlUserDirectory:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: UUID) -> UserAccount | None:
        row = (await self._session.execute(select(USERS).where(USERS.c.id == user_id))).first()
        return None if row is None else _account(row)

    async def get_by_login(self, login: str) -> UserAccount | None:
        statement = select(USERS).where(USERS.c.login == login)
        row = (await self._session.execute(statement)).first()
        return None if row is None else _account(row)

    async def add(self, account: UserAccount) -> None:
        statement = (
            insert(USERS)
            .values(
                id=account.user_id,
                login=account.login,
                display_name=account.display_name,
                roles=sorted(r.value for r in account.roles),
                active=account.active,
            )
            .on_conflict_do_nothing()
            .returning(USERS.c.id)
        )
        if (await self._session.execute(statement)).first() is None:
            raise DuplicateResourceError(
                f"user {account.user_id} or login {account.login!r} exists"
            )
