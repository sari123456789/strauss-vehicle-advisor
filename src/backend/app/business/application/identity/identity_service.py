"""Resolve and revalidate the acting identity from the identity store (`auth.users`).

An `ActorRef` is created only by trusted server code: the API after authentication (4D),
the AI worker from persisted conversation ownership, or a CLI. It is never taken from model
output. Every business operation revalidates it against the *current* account: unknown or
inactive accounts are `UNAUTHENTICATED`, and roles always come from the store, never from
the presented `ActorRef` (a stale or forged role claim has no effect).
"""

from uuid import UUID

from app.business.application.errors import UnauthenticatedError
from app.business.application.ports.unit_of_work import (
    BusinessUnitOfWork,
    BusinessUnitOfWorkFactory,
)
from app.contracts.tool_context import ActorRef, Origin


async def revalidate_actor(uow: BusinessUnitOfWork, actor: ActorRef) -> ActorRef:
    """The actor as currently stored: same subject and origin, roles from the store."""
    account = await uow.users.get(actor.subject_id)
    if account is None or not account.active:
        raise UnauthenticatedError("the account is unknown or inactive")
    return ActorRef(subject_id=account.user_id, roles=account.roles, origin=actor.origin)


class IdentityService:
    def __init__(self, unit_of_work: BusinessUnitOfWorkFactory) -> None:
        self._unit_of_work = unit_of_work

    async def resolve(self, subject_id: UUID, origin: Origin) -> ActorRef:
        """Build an `ActorRef` for an already authenticated / persisted subject.

        For the AI worker (`Origin.WORKER`): the subject comes from the persisted conversation
        owner; this call checks that the account still exists and is active, and the actor is
        revalidated again by every business operation.
        """
        async with self._unit_of_work() as uow:
            account = await uow.users.get(subject_id)
        if account is None or not account.active:
            raise UnauthenticatedError("the account is unknown or inactive")
        return ActorRef(subject_id=account.user_id, roles=account.roles, origin=origin)
