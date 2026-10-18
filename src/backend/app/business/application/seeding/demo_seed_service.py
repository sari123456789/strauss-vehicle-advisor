"""Seed the fictional demo accounts and their actual EmployeeProfiles (decision Q8, option A).

Provenance: seeded facts are `usable` so the demo calculations can run, but they are NOT
employee-confirmed: `lastConfirmedAt` stays null and `sourceMessageId` is null. The shared
contract has no "demo import" source value, so the field source is `employee` (the only
value meaning "an employee fact") except the current vehicle's propulsion, which comes from
the catalogue. The demo origin is preserved internally in the profile's `origin` record
(seed version, source hashes, employee reference, seed time); it is not part of any contract.
A field still carries the seeded value while its `updatedAt` equals `origin.seededAt`.

Idempotent: missing accounts/profiles are created; an existing profile is never overwritten.
Validation against the latest reference release happens before any write; all writes are one
transaction.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid5

from app.business.application.ports.auth import UserAccount
from app.business.application.ports.demo_seed import (
    DemoEmployeeSeed,
    DemoSeedSource,
    DemoSeedSourceReader,
)
from app.business.application.ports.reference_data import PublishedRelease, ReferenceDataRepository
from app.business.application.ports.unit_of_work import BusinessUnitOfWorkFactory
from app.business.domain.arrangements import ArrangementKind
from app.business.domain.employee_profiles import (
    CurrentArrangementFacts,
    EmployeeProfile,
    SalaryFacts,
)
from app.business.domain.json_values import JsonObject, freeze_object
from app.business.domain.provenance import FieldStatus, FieldValue, SourceType
from app.business.domain.vehicles import VehicleRef
from app.contracts.tool_context import ActorRef, Role

SEEDED_PROFILE_VERSION = 1
SEEDED_FIELD_REVISION = 1


class DemoSeedNotAuthorizedError(Exception):
    code = "FORBIDDEN"


class DemoSeedConflictError(Exception):
    """The seed mapping disagrees with the reference data or with existing accounts."""

    code = "SOURCE_CONFLICT"


@dataclass(frozen=True, slots=True)
class DemoSeedReport:
    dry_run: bool
    seed_version: str
    seed_file_sha256: str
    workbook_sha256: str
    reference_release_id: UUID
    users_created: tuple[str, ...]
    users_existing: tuple[str, ...]
    profiles_created: tuple[str, ...]
    profiles_existing: tuple[str, ...]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _seeded[T](value: T, at: datetime, source: SourceType = SourceType.EMPLOYEE) -> FieldValue[T]:
    return FieldValue(
        value=value,
        status=FieldStatus.USABLE,
        source=source,
        source_message_id=None,
        updated_at=at,
        revision=SEEDED_FIELD_REVISION,
        last_confirmed_at=None,
    )


def employee_subject_id(namespace: UUID, employee_ref: str) -> UUID:
    return uuid5(namespace, f"employee:{employee_ref}")


def staff_subject_id(namespace: UUID, login: str) -> UUID:
    return uuid5(namespace, f"staff:{login}")


class DemoSeedService:
    def __init__(
        self,
        reader: DemoSeedSourceReader,
        reference_data: ReferenceDataRepository,
        unit_of_work: BusinessUnitOfWorkFactory,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._reader = reader
        self._reference_data = reference_data
        self._unit_of_work = unit_of_work
        self._clock = clock

    async def seed(self, actor: ActorRef, *, dry_run: bool) -> DemoSeedReport:
        if Role.IMPORT_OPERATOR not in actor.roles:
            raise DemoSeedNotAuthorizedError("seeding demo data requires the import_operator role")
        source = self._reader.read()
        release = await self._reference_data.latest_release()
        if release is None:
            raise DemoSeedConflictError("no reference release: import reference data first")
        seeded_at = self._clock()
        profiles = [await self._profile(e, source, release, seeded_at) for e in source.employees]
        accounts = [
            UserAccount(
                user_id=employee_subject_id(source.uuid_namespace, e.employee_ref),
                login=e.login,
                roles=frozenset({Role.EMPLOYEE}),
                display_name=e.display_name,
            )
            for e in source.employees
        ] + [
            UserAccount(
                user_id=staff_subject_id(source.uuid_namespace, s.login),
                login=s.login,
                roles=s.roles,
                display_name=s.display_name,
            )
            for s in source.staff
        ]
        users_created: list[str] = []
        users_existing: list[str] = []
        profiles_created: list[str] = []
        profiles_existing: list[str] = []
        async with self._unit_of_work() as uow:
            for account in accounts:
                existing = await uow.users.get(account.user_id)
                if existing is None:
                    if await uow.users.get_by_login(account.login) is not None:
                        raise DemoSeedConflictError(
                            f"login {account.login!r} belongs to another user"
                        )
                    await uow.users.add(account)
                    users_created.append(account.login)
                elif (existing.login, existing.roles) != (account.login, account.roles):
                    raise DemoSeedConflictError(f"account {account.login!r} differs from the seed")
                else:
                    users_existing.append(account.login)
            for employee, profile in zip(source.employees, profiles, strict=True):
                if await uow.profiles.get(profile.employee_id) is not None:
                    profiles_existing.append(employee.employee_ref)
                    continue
                await uow.profiles.add(profile, origin=_origin(employee, source, seeded_at))
                profiles_created.append(employee.employee_ref)
            if not dry_run:
                await uow.commit()
        return DemoSeedReport(
            dry_run=dry_run,
            seed_version=source.seed_version,
            seed_file_sha256=source.seed_file.sha256,
            workbook_sha256=source.workbook.sha256,
            reference_release_id=release.release_id,
            users_created=tuple(users_created),
            users_existing=tuple(users_existing),
            profiles_created=tuple(profiles_created),
            profiles_existing=tuple(profiles_existing),
        )

    async def _profile(
        self,
        employee: DemoEmployeeSeed,
        source: DemoSeedSource,
        release: PublishedRelease,
        at: datetime,
    ) -> EmployeeProfile:
        where = employee.employee_ref
        catalogue_version = release.catalogue_version_id
        occupation = await self._reference_data.get_occupation(
            catalogue_version, employee.occupation_id
        )
        if occupation is None:
            raise DemoSeedConflictError(f"{where}: unknown occupation {employee.occupation_id!r}")
        current = employee.current
        if current.kind is ArrangementKind.COMPANY_CAR:
            if current.catalogue_id is None or current.participation is None:
                raise DemoSeedConflictError(f"{where}: company car needs vehicle and participation")
            vehicle = await self._reference_data.get_vehicle(
                catalogue_version, current.catalogue_id
            )
            if vehicle is None:
                raise DemoSeedConflictError(f"{where}: unknown vehicle {current.catalogue_id!r}")
            if vehicle.commercial_name != current.expected_commercial_name:
                raise DemoSeedConflictError(
                    f"{where}: {current.catalogue_id} is {vehicle.commercial_name!r}, the workbook "
                    f"says {current.expected_commercial_name!r}"
                )
            arrangement = CurrentArrangementFacts(
                kind=_seeded(current.kind, at),
                vehicle=_seeded(VehicleRef(catalogue_id=vehicle.catalogue_id), at),
                propulsion=_seeded(vehicle.propulsion, at, SourceType.CATALOGUE),
                participation=_seeded(current.participation, at),
            )
        elif current.kind is ArrangementKind.ALLOWANCE:
            if current.allowance is None or current.allowance != occupation.waiver_allowance:
                raise DemoSeedConflictError(
                    f"{where}: allowance {current.allowance} differs from the occupation table "
                    f"({occupation.waiver_allowance})"
                )
            arrangement = CurrentArrangementFacts(
                kind=_seeded(current.kind, at), allowance=_seeded(current.allowance, at)
            )
        else:
            arrangement = CurrentArrangementFacts(kind=_seeded(current.kind, at))
        return EmployeeProfile(
            employee_id=employee_subject_id(source.uuid_namespace, employee.employee_ref),
            version=SEEDED_PROFILE_VERSION,
            salary=SalaryFacts(
                base_gross=_seeded(employee.base_gross, at),
                commissions_gross=_seeded(employee.commissions_gross, at),
                bonus_gross=_seeded(employee.bonus_gross, at),
                pension_employee_rate=_seeded(employee.pension_employee_rate, at),
                study_fund_employee_rate=_seeded(employee.study_fund_employee_rate, at),
                credit_points=_seeded(employee.credit_points, at),
            ),
            occupation_id=_seeded(employee.occupation_id, at),
            entitlement_group=_seeded(employee.entitlement_group, at),
            qualifying_children_count=_seeded(employee.qualifying_children_count, at),
            current_arrangement=arrangement,
        )


def _origin(employee: DemoEmployeeSeed, source: DemoSeedSource, at: datetime) -> JsonObject:
    return freeze_object(
        {
            "kind": "demo_seed",
            "seedVersion": source.seed_version,
            "seedFileSha256": source.seed_file.sha256,
            "workbookSha256": source.workbook.sha256,
            "employeeRef": employee.employee_ref,
            "seededAt": at.isoformat(),
        }
    )
