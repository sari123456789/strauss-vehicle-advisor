"""Operator-only password administration for the fictional demo accounts (Step 4D).

Passwords are never in the repository: an operator sets them through the CLI from an
environment variable or a hidden prompt. Only the import operator, from the CLI origin, may do
this; the password is hashed here and never logged or returned.
"""

from app.business.application.errors import ForbiddenError, InvalidInput, NotFoundError
from app.business.application.ports.clock import Clock
from app.business.application.ports.credentials import PasswordHasher
from app.business.application.ports.unit_of_work import BusinessUnitOfWorkFactory
from app.contracts.tool_context import ActorRef, Origin, Role

MIN_PASSWORD_LENGTH = 12


class CredentialAdminService:
    def __init__(
        self, unit_of_work: BusinessUnitOfWorkFactory, hasher: PasswordHasher, clock: Clock
    ) -> None:
        self._unit_of_work = unit_of_work
        self._hasher = hasher
        self._clock = clock

    async def set_password(self, operator: ActorRef, login: str, password: str) -> None:
        if Role.IMPORT_OPERATOR not in operator.roles or operator.origin is not Origin.CLI:
            raise ForbiddenError("only the operator CLI may set passwords")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise InvalidInput(f"a password needs at least {MIN_PASSWORD_LENGTH} characters")
        async with self._unit_of_work() as uow:
            account = await uow.users.get_by_login(login.strip().lower())
            if account is None:
                raise NotFoundError("unknown account")
            await uow.credentials.set_password_hash(
                account.user_id, self._hasher.hash(password), at=self._clock.now()
            )
            await uow.commit()
