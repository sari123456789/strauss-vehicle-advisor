"""Authorization policy for employee-facing business operations (decision B7, AC26/AC27).

Every protected operation calls the policy inside its own Unit of Work before reading any
business data. Matrix: employees may act on their own profile and cases only;
representatives and import operators are `FORBIDDEN` for these operations. A case that does
not exist, belongs to someone else, or does not belong to the given conversation is
`NOT_FOUND` (no existence disclosure). Identifiers alone never grant access.

Representative operations (B3a) require the `representative` role, re-read from the store on
every call; everyone else is `FORBIDDEN`. DEMO-ONLY POLICY (decision Q4): every active
representative may see and answer every representative request - there is no per-team or
per-request assignment. Representatives receive only the minimal request context, never the
employee's identity, profile, salary, case or conversation.
"""

from uuid import UUID

from app.business.application.errors import ForbiddenError, NotFoundError
from app.business.application.identity.identity_service import revalidate_actor
from app.business.application.ports.unit_of_work import BusinessUnitOfWork
from app.business.domain.cases import ComparisonCase
from app.contracts.tool_context import ActorRef, Role, ToolContext


class AuthorizationPolicy:
    async def employee(self, uow: BusinessUnitOfWork, actor: ActorRef) -> ActorRef:
        current = await revalidate_actor(uow, actor)
        if Role.EMPLOYEE not in current.roles:
            raise ForbiddenError("this operation is available to employees only")
        return current

    async def representative(self, uow: BusinessUnitOfWork, actor: ActorRef) -> ActorRef:
        current = await revalidate_actor(uow, actor)
        if Role.REPRESENTATIVE not in current.roles:
            raise ForbiddenError("this operation is available to representatives only")
        return current

    async def owned_case(
        self, uow: BusinessUnitOfWork, actor: ActorRef, case_id: UUID
    ) -> ComparisonCase:
        current = await self.employee(uow, actor)
        found = await uow.cases.get(case_id, owner_subject_id=current.subject_id)
        if found is None:
            raise NotFoundError()
        return found

    async def tool_case(
        self, uow: BusinessUnitOfWork, ctx: ToolContext
    ) -> tuple[ActorRef, ComparisonCase]:
        """Server-injected tool context: actor, case and conversation must all agree."""
        current = await self.employee(uow, ctx.actor)
        found = await uow.cases.get(ctx.case_id, owner_subject_id=current.subject_id)
        if found is None or found.conversation_id != ctx.conversation_id:
            raise NotFoundError()
        return current, found
