"""Employee profile use cases: the shared, versioned *actual* EmployeeProfile.

- An employee reads only their own profile (no profile route for other subjects exists).
- No persisted profile yet: an all-missing profile at version 0 (decision B2); the first
  actual update inserts it at version 1.
- Actual changes are written only through `apply_actual` inside the caller's Unit of Work,
  together with the business case (CaseService); there is no standalone profile write and no
  confirm/reset operation (not approved). Hypothetical values never reach this service.
"""

from collections.abc import Sequence
from dataclasses import replace
from uuid import UUID

from app.business.application.cases.changes import FactChange, apply_actual_changes
from app.business.application.cases.mapping import profile_view
from app.business.application.errors import VersionConflict, translate_errors
from app.business.application.identity.authorization import AuthorizationPolicy
from app.business.application.ports.clock import Clock
from app.business.application.ports.repositories import DuplicateResourceError
from app.business.application.ports.unit_of_work import (
    BusinessUnitOfWork,
    BusinessUnitOfWorkFactory,
)
from app.business.domain.employee_profiles import EmployeeProfile
from app.contracts.profile import EmployeeProfileView
from app.contracts.tool_context import ActorRef


async def load_profile(uow: BusinessUnitOfWork, employee_id: UUID) -> EmployeeProfile:
    stored = await uow.profiles.get(employee_id)
    return stored if stored is not None else EmployeeProfile(employee_id=employee_id, version=0)


class EmployeeProfileService:
    def __init__(
        self,
        unit_of_work: BusinessUnitOfWorkFactory,
        policy: AuthorizationPolicy,
        clock: Clock,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._policy = policy
        self._clock = clock

    async def get_profile(self, actor: ActorRef) -> EmployeeProfileView:
        with translate_errors():
            async with self._unit_of_work() as uow:
                current = await self._policy.employee(uow, actor)
                return profile_view(await load_profile(uow, current.subject_id))

    async def apply_actual(
        self,
        uow: BusinessUnitOfWork,
        profile: EmployeeProfile,
        changes: Sequence[FactChange],
        *,
        expected_version: int,
        source_message_id: UUID | None,
    ) -> EmployeeProfile:
        """Version-checked actual update in the caller's (already authorized) Unit of Work.

        Returns the stored profile (unchanged object for a no-op). A concurrent writer from
        the same version makes this raise `VersionConflict`; nothing is last-write-wins.
        """
        if profile.version != expected_version:
            raise VersionConflict("the employee profile changed in the meantime; reload first")
        updated = apply_actual_changes(
            profile, changes, now=self._clock.now(), source_message_id=source_message_id
        )
        if updated == profile:
            return profile
        if profile.version == 0:
            first = replace(updated, version=1)
            try:
                await uow.profiles.add(first)
            except DuplicateResourceError as error:
                raise VersionConflict("the employee profile was created concurrently") from error
            return first
        with translate_errors():
            return await uow.profiles.update(updated)
