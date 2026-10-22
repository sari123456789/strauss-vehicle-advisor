"""Authentication and session management (Step 4D, architecture §22, decision B1).

- **Login**: login + password -> a short-lived access token and a rotating refresh token.
  Unknown login, wrong password and inactive account give the same `UNAUTHENTICATED` answer,
  and an unknown login still pays the full hashing cost (no account enumeration). Repeated
  failures for one login from one client address are throttled (`RATE_LIMITED`, in-process window; MVP single API).
- **Access token**: carries only subject and session family - never roles. `authenticate`
  verifies it, checks that the session family is still active (logout revokes immediately)
  and builds the `ActorRef` from the *current* account: roles from the store, `Origin.HTTP`.
- **Refresh**: one-time use. Each refresh rotates the token inside one transaction, with the
  presented session locked; presenting an already rotated or revoked token revokes the whole
  family (token-theft detection). Expired sessions or inactive accounts fail.
- **Logout**: revokes the family; idempotent; never reveals whether a token existed.
Raw refresh tokens are returned to the caller once and stored only as SHA-256 digests.
"""

import hashlib
import secrets
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from app.business.application.errors import RateLimited, UnauthenticatedError
from app.business.application.ports.auth import UserAccount
from app.business.application.ports.clock import Clock
from app.business.application.ports.credentials import (
    AccessClaims,
    AccessTokenCodec,
    InvalidTokenError,
    PasswordHasher,
    RefreshSession,
)
from app.business.application.ports.unit_of_work import (
    BusinessUnitOfWork,
    BusinessUnitOfWorkFactory,
)
from app.contracts.tool_context import ActorRef, Origin

INVALID_CREDENTIALS = "the login or password is incorrect"
MAX_REFRESH_TOKEN_LENGTH = 256
MAX_TRACKED_LOGINS = 10_000


@dataclass(frozen=True, slots=True)
class AuthConfig:
    access_ttl: timedelta = timedelta(minutes=15)
    refresh_ttl: timedelta = timedelta(hours=8)
    max_failures: int = 5
    failure_window: timedelta = timedelta(minutes=15)


DEFAULT_CONFIG = AuthConfig()


@dataclass(frozen=True, slots=True)
class SessionTokens:
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime
    actor: ActorRef
    display_name: str | None


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class LoginThrottle:
    """Failed attempts per login within a sliding window (in-process)."""

    def __init__(self, max_failures: int, window: timedelta) -> None:
        self._max = max_failures
        self._window = window
        self._failures: dict[str, deque[datetime]] = {}

    def _recent(self, key: str, now: datetime) -> deque[datetime]:
        attempts = self._failures.setdefault(key, deque())
        while attempts and attempts[0] <= now - self._window:
            attempts.popleft()
        return attempts

    def check(self, key: str, now: datetime) -> None:
        attempts = self._recent(key, now)
        if len(attempts) >= self._max:
            retry = int((attempts[0] + self._window - now).total_seconds()) + 1
            raise RateLimited(
                "too many failed sign-in attempts",
                safe_details={"retryAfterSeconds": str(max(retry, 1))},
            )

    def fail(self, key: str, now: datetime) -> None:
        if len(self._failures) >= MAX_TRACKED_LOGINS:  # bounded memory under attack
            self._failures.clear()
        self._recent(key, now).append(now)

    def clear(self, key: str) -> None:
        self._failures.pop(key, None)


class AuthService:
    def __init__(
        self,
        unit_of_work: BusinessUnitOfWorkFactory,
        hasher: PasswordHasher,
        codec: AccessTokenCodec,
        clock: Clock,
        config: AuthConfig = DEFAULT_CONFIG,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._hasher = hasher
        self._codec = codec
        self._clock = clock
        self._config = config
        self._throttle = LoginThrottle(config.max_failures, config.failure_window)
        self._dummy_hash = hasher.hash(secrets.token_urlsafe(16))

    # --- login / refresh / logout ------------------------------------------------------

    async def login(
        self, login: str, password: str, *, client: str | None = None
    ) -> SessionTokens:
        """`client` (the caller's network address, when known) scopes the failure throttle:
        repeated wrong passwords block that login from that client only, not for everyone."""
        key = login.strip().lower()
        throttle_key = key if client is None else f"{key}|{client}"
        now = self._clock.now()
        self._throttle.check(throttle_key, now)
        async with self._unit_of_work() as uow:
            account = await uow.users.get_by_login(key)
            stored = (
                None
                if account is None
                else await uow.credentials.get_password_hash(account.user_id)
            )
            valid = self._hasher.verify(password, stored or self._dummy_hash)
            if account is None or stored is None or not valid or not account.active:
                self._throttle.fail(throttle_key, now)
                raise UnauthenticatedError(INVALID_CREDENTIALS)
            self._throttle.clear(throttle_key)
            tokens = await self._open_session(uow, account, family_id=uuid4(), now=now)
            await uow.commit()
            return tokens

    async def refresh(self, refresh_token: str) -> SessionTokens:
        if not refresh_token or len(refresh_token) > MAX_REFRESH_TOKEN_LENGTH:
            raise UnauthenticatedError("the session has ended; sign in again")
        now = self._clock.now()
        async with self._unit_of_work() as uow:
            session = await uow.refresh_sessions.lock_by_token_hash(token_digest(refresh_token))
            if session is None:
                raise UnauthenticatedError("the session has ended; sign in again")
            if session.rotated_at is not None or session.revoked_at is not None:
                await uow.refresh_sessions.revoke_family(session.family_id, at=now)
                await uow.commit()  # reuse of an old token: end every session of the family
                raise UnauthenticatedError("the session has ended; sign in again")
            account = await uow.users.get(session.user_id)
            if session.expires_at <= now or account is None or not account.active:
                await uow.refresh_sessions.revoke_family(session.family_id, at=now)
                await uow.commit()
                raise UnauthenticatedError("the session has ended; sign in again")
            await uow.refresh_sessions.mark_rotated(session.session_id, at=now)
            tokens = await self._open_session(uow, account, family_id=session.family_id, now=now)
            await uow.commit()
            return tokens

    async def logout(self, refresh_token: str | None) -> None:
        if not refresh_token or len(refresh_token) > MAX_REFRESH_TOKEN_LENGTH:
            return
        now = self._clock.now()
        async with self._unit_of_work() as uow:
            session = await uow.refresh_sessions.lock_by_token_hash(token_digest(refresh_token))
            if session is not None:
                await uow.refresh_sessions.revoke_family(session.family_id, at=now)
                await uow.commit()

    # --- per request -------------------------------------------------------------------

    async def authenticate(self, access_token: str) -> ActorRef:
        now = self._clock.now()
        try:
            claims = self._codec.decode(access_token, now=now)
        except InvalidTokenError:
            raise UnauthenticatedError("sign in again") from None
        async with self._unit_of_work() as uow:
            if not await uow.refresh_sessions.family_active(claims.session_family_id, now=now):
                raise UnauthenticatedError("sign in again")
            account = await uow.users.get(claims.subject_id)
        if account is None or not account.active:
            raise UnauthenticatedError("sign in again")
        return ActorRef(subject_id=account.user_id, roles=account.roles, origin=Origin.HTTP)

    # --- helpers -----------------------------------------------------------------------

    async def _open_session(
        self, uow: BusinessUnitOfWork, account: UserAccount, *, family_id: UUID, now: datetime
    ) -> SessionTokens:
        refresh_token = secrets.token_urlsafe(32)
        refresh_expires = now + self._config.refresh_ttl
        await uow.refresh_sessions.add(
            RefreshSession(
                session_id=uuid4(),
                family_id=family_id,
                user_id=account.user_id,
                token_hash=token_digest(refresh_token),
                created_at=now,
                expires_at=refresh_expires,
            )
        )
        access_expires = now + self._config.access_ttl
        access_token = self._codec.encode(
            AccessClaims(
                subject_id=account.user_id,
                session_family_id=family_id,
                issued_at=now,
                expires_at=access_expires,
                token_id=uuid4().hex,
            )
        )
        return SessionTokens(
            access_token=access_token,
            access_expires_at=access_expires,
            refresh_token=refresh_token,
            refresh_expires_at=refresh_expires,
            actor=ActorRef(subject_id=account.user_id, roles=account.roles, origin=Origin.HTTP),
            display_name=account.display_name,
        )
