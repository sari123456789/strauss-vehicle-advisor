"""`CredentialStore` and `RefreshSessionRepository` over the `auth` schema (Step 4D).

Bound to one Unit of Work session. Only hashes are read or written.
"""

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Table, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.business.application.ports.credentials import RefreshSession
from app.business.infrastructure.database.models.identity import CredentialRow, RefreshSessionRow

CREDENTIALS = cast(Table, CredentialRow.__table__)
SESSIONS = cast(Table, RefreshSessionRow.__table__)


def _session(row: Any) -> RefreshSession:
    return RefreshSession(
        session_id=row.id,
        family_id=row.family_id,
        user_id=row.user_id,
        token_hash=row.token_hash,
        created_at=row.created_at,
        expires_at=row.expires_at,
        rotated_at=row.rotated_at,
        revoked_at=row.revoked_at,
    )


class SqlCredentialStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_password_hash(self, user_id: UUID) -> str | None:
        statement = select(CREDENTIALS.c.password_hash).where(CREDENTIALS.c.user_id == user_id)
        value = (await self._session.execute(statement)).scalar_one_or_none()
        return None if value is None else str(value)

    async def set_password_hash(self, user_id: UUID, password_hash: str, *, at: datetime) -> None:
        statement = insert(CREDENTIALS).values(
            user_id=user_id, password_hash=password_hash, updated_at=at
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[CREDENTIALS.c.user_id],
                set_={"password_hash": password_hash, "updated_at": at},
            )
        )


class SqlRefreshSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, session: RefreshSession) -> None:
        await self._session.execute(
            insert(SESSIONS).values(
                id=session.session_id,
                family_id=session.family_id,
                user_id=session.user_id,
                token_hash=session.token_hash,
                created_at=session.created_at,
                expires_at=session.expires_at,
                rotated_at=session.rotated_at,
                revoked_at=session.revoked_at,
            )
        )

    async def lock_by_token_hash(self, token_hash: str) -> RefreshSession | None:
        statement = select(SESSIONS).where(SESSIONS.c.token_hash == token_hash).with_for_update()
        row = (await self._session.execute(statement)).first()
        return None if row is None else _session(row)

    async def mark_rotated(self, session_id: UUID, *, at: datetime) -> None:
        await self._session.execute(
            update(SESSIONS).where(SESSIONS.c.id == session_id).values(rotated_at=at)
        )

    async def revoke_family(self, family_id: UUID, *, at: datetime) -> None:
        await self._session.execute(
            update(SESSIONS)
            .where(SESSIONS.c.family_id == family_id, SESSIONS.c.revoked_at.is_(None))
            .values(revoked_at=at)
        )

    async def family_active(self, family_id: UUID, *, now: datetime) -> bool:
        statement = (
            select(SESSIONS.c.id)
            .where(
                SESSIONS.c.family_id == family_id,
                SESSIONS.c.revoked_at.is_(None),
                SESSIONS.c.expires_at > now,
            )
            .limit(1)
        )
        return (await self._session.execute(statement)).first() is not None
