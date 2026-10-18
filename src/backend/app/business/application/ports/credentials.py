"""Authentication ports (Step 4D): password hashes, refresh sessions and access tokens.

Infrastructure implements them; bootstrap wires them. Secrets and raw tokens never cross into
logs or Domain. Only hashes are stored: a password as a salted memory-hard hash, a refresh
token as its SHA-256 digest. Roles are never stored in tokens - they are read from the
identity store on every request (decision B1/§22: roles from the store, not from claims).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID


class InvalidTokenError(Exception):
    """An access token is malformed, wrongly signed, expired or for another audience."""


@dataclass(frozen=True, slots=True)
class AccessClaims:
    subject_id: UUID
    session_family_id: UUID
    issued_at: datetime
    expires_at: datetime
    token_id: str


@dataclass(frozen=True, slots=True)
class RefreshSession:
    session_id: UUID
    family_id: UUID
    """All sessions created by rotation from one login; revoking the family logs out."""
    user_id: UUID
    token_hash: str
    created_at: datetime
    expires_at: datetime
    rotated_at: datetime | None = None
    revoked_at: datetime | None = None


@runtime_checkable
class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, password: str, encoded: str) -> bool:
        """Constant-time comparison; False (never an exception) for a malformed hash."""
        ...


@runtime_checkable
class AccessTokenCodec(Protocol):
    def encode(self, claims: AccessClaims) -> str: ...

    def decode(self, token: str, *, now: datetime) -> AccessClaims:
        """Raises `InvalidTokenError` for any invalid, expired or foreign token."""
        ...


@runtime_checkable
class CredentialStore(Protocol):
    async def get_password_hash(self, user_id: UUID) -> str | None: ...

    async def set_password_hash(self, user_id: UUID, password_hash: str, *, at: datetime) -> None:
        """Insert or replace the user's password hash."""
        ...


@runtime_checkable
class RefreshSessionRepository(Protocol):
    async def add(self, session: RefreshSession) -> None: ...

    async def lock_by_token_hash(self, token_hash: str) -> RefreshSession | None:
        """The session row `FOR UPDATE` (concurrent refreshes of one token serialize)."""
        ...

    async def mark_rotated(self, session_id: UUID, *, at: datetime) -> None: ...

    async def revoke_family(self, family_id: UUID, *, at: datetime) -> None: ...

    async def family_active(self, family_id: UUID, *, now: datetime) -> bool:
        """True while the family has an unrevoked, unexpired session."""
        ...
