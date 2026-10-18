"""Authorization matrix with in-memory stubs: roles always come from the identity store."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.business.application.errors import (
    ForbiddenError,
    NotFoundError,
    UnauthenticatedError,
)
from app.business.application.identity.authorization import AuthorizationPolicy
from app.business.application.ports.auth import UserAccount
from app.business.domain.cases import CaseLifecycleStatus, ComparisonCase
from app.business.domain.employee_profiles import EmployeeProfile
from app.contracts.tool_context import ActorRef, Origin, Role, ToolContext

NOW = datetime(2026, 10, 18, tzinfo=UTC)


@dataclass
class _Users:
    accounts: dict[UUID, UserAccount] = field(default_factory=dict)

    async def get(self, user_id: UUID) -> UserAccount | None:
        return self.accounts.get(user_id)


@dataclass
class _Cases:
    cases: dict[UUID, ComparisonCase] = field(default_factory=dict)

    async def get(self, case_id: UUID, *, owner_subject_id: UUID) -> ComparisonCase | None:
        found = self.cases.get(case_id)
        return found if found is not None and found.owner_subject_id == owner_subject_id else None


@dataclass
class _Uow:
    users: _Users = field(default_factory=_Users)
    cases: _Cases = field(default_factory=_Cases)


def _account(roles: set[Role], *, active: bool = True) -> UserAccount:
    user_id = uuid4()
    return UserAccount(user_id, f"u{user_id.hex[:8]}", frozenset(roles), active=active)


def _actor(account: UserAccount, claimed: set[Role] | None = None) -> ActorRef:
    roles = frozenset(claimed) if claimed else account.roles
    return ActorRef(subject_id=account.user_id, roles=roles, origin=Origin.AGENT)


def _case(owner: UUID) -> ComparisonCase:
    return ComparisonCase(
        case_id=uuid4(),
        conversation_id=uuid4(),
        owner_subject_id=owner,
        calculation_month="2026-10",
        reference_date=date(2026, 10, 1),
        version=0,
        lifecycle_status=CaseLifecycleStatus.ACTIVE,
        actual_facts=EmployeeProfile(employee_id=owner, version=0),
        created_at=NOW,
        updated_at=NOW,
    )


def _world() -> tuple[_Uow, UserAccount, ComparisonCase]:
    uow = _Uow()
    employee = _account({Role.EMPLOYEE})
    uow.users.accounts[employee.user_id] = employee
    case = _case(employee.user_id)
    uow.cases.cases[case.case_id] = case
    return uow, employee, case


def _ctx(actor: ActorRef, case: ComparisonCase, **overrides: Any) -> ToolContext:
    values = {
        "actor": actor,
        "conversation_id": case.conversation_id,
        "case_id": case.case_id,
        "trace_id": "t",
    }
    values.update(overrides)
    return ToolContext(**values)


async def test_owner_with_matching_context_is_authorized() -> None:
    uow, employee, case = _world()
    actor, found = await AuthorizationPolicy().tool_case(uow, _ctx(_actor(employee), case))  # type: ignore[arg-type]
    assert found == case and actor.roles == frozenset({Role.EMPLOYEE})


@pytest.mark.parametrize(
    "change",
    [
        lambda ctx, case: ctx.model_copy(update={"case_id": uuid4()}),  # invented case (AC27)
        lambda ctx, case: ctx.model_copy(update={"conversation_id": uuid4()}),
    ],
)
async def test_invented_or_mismatched_identifiers_are_not_found(change: Any) -> None:
    uow, employee, case = _world()
    with pytest.raises(NotFoundError):
        await AuthorizationPolicy().tool_case(uow, change(_ctx(_actor(employee), case), case))  # type: ignore[arg-type]


async def test_foreign_case_is_not_found() -> None:
    uow, _, case = _world()
    stranger = _account({Role.EMPLOYEE})
    uow.users.accounts[stranger.user_id] = stranger
    with pytest.raises(NotFoundError):
        await AuthorizationPolicy().tool_case(uow, _ctx(_actor(stranger), case))  # type: ignore[arg-type]


@pytest.mark.parametrize("role", [Role.REPRESENTATIVE, Role.IMPORT_OPERATOR])
async def test_non_employee_roles_are_forbidden(role: Role) -> None:
    uow, _, case = _world()
    other = _account({role})
    uow.users.accounts[other.user_id] = other
    with pytest.raises(ForbiddenError):
        await AuthorizationPolicy().tool_case(uow, _ctx(_actor(other), case))  # type: ignore[arg-type]


async def test_claimed_roles_are_ignored_in_favour_of_the_store() -> None:
    uow, _, case = _world()
    representative = _account({Role.REPRESENTATIVE})
    uow.users.accounts[representative.user_id] = representative
    forged = _actor(representative, claimed={Role.EMPLOYEE})
    with pytest.raises(ForbiddenError):
        await AuthorizationPolicy().employee(uow, forged)  # type: ignore[arg-type]


async def test_unknown_or_inactive_accounts_are_unauthenticated() -> None:
    uow, employee, case = _world()
    unknown = _account({Role.EMPLOYEE})
    with pytest.raises(UnauthenticatedError):
        await AuthorizationPolicy().employee(uow, _actor(unknown))  # type: ignore[arg-type]
    uow.users.accounts[employee.user_id] = _account({Role.EMPLOYEE}, active=False).__class__(
        employee.user_id, employee.login, employee.roles, active=False
    )
    with pytest.raises(UnauthenticatedError):
        await AuthorizationPolicy().tool_case(uow, _ctx(_actor(employee), case))  # type: ignore[arg-type]
