"""Builders and a persistence context for Step 3A DB tests (real Domain objects, unique IDs).

Each test runs one coroutine through `run()` and creates/disposes its own engine inside it,
so connections never cross event loops.
"""

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.business.application.ports.auth import UserAccount
from app.business.application.ports.reference_data import PublishedRelease
from app.business.domain.arrangements import ArrangementKind
from app.business.domain.cases import (
    Alternative,
    AlternativeKind,
    CaseLifecycleStatus,
    ComparisonCase,
    ComparisonRunRecord,
    OptionResultSetRecord,
    RunKind,
)
from app.business.domain.employee_profiles import (
    CurrentArrangementFacts,
    EmployeeProfile,
    SalaryFacts,
)
from app.business.domain.provenance import FieldStatus, FieldValue, SourceType
from app.business.domain.representatives import (
    ExpectedValueType,
    RepresentativeFieldCode,
    RepresentativeRequest,
    RequestStatus,
)
from app.business.domain.rules import EntitlementGroup
from app.business.domain.vehicles import Track, VehicleRef
from app.business.infrastructure.database.engine import (
    AsyncEngine,
    create_engine,
    create_runtime_engine,
    create_session_factory,
)
from app.business.infrastructure.database.repositories.reference_data import (
    SqlReferenceDataRepository,
)
from app.business.infrastructure.database.unit_of_work import SqlUnitOfWorkFactory
from app.contracts.tool_context import Role

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Persistence:
    engine: AsyncEngine
    uow: SqlUnitOfWorkFactory
    reference: SqlReferenceDataRepository


@asynccontextmanager
async def persistence(url: str, *, idle_ms: int | None = None) -> AsyncIterator[Persistence]:
    engine = (
        create_engine(url)
        if idle_ms is None
        else create_runtime_engine(url, idle_in_transaction_timeout_ms=idle_ms)
    )
    sessions = create_session_factory(engine)
    try:
        yield Persistence(
            engine, SqlUnitOfWorkFactory(sessions), SqlReferenceDataRepository(sessions)
        )
    finally:
        await engine.dispose()


def usable(value: Any, source: SourceType = SourceType.EMPLOYEE) -> FieldValue[Any]:
    return FieldValue(
        value=value, status=FieldStatus.USABLE, source=source, updated_at=NOW, revision=1
    )


def profile(owner: UUID, *, version: int = 1, base: str = "18500.00") -> EmployeeProfile:
    return EmployeeProfile(
        employee_id=owner,
        version=version,
        salary=SalaryFacts(
            base_gross=usable(Decimal(base)),
            commissions_gross=usable(Decimal("0")),
            pension_employee_rate=usable(Decimal("0.06")),
        ),
        entitlement_group=usable(EntitlementGroup.GIMEL),
        current_arrangement=CurrentArrangementFacts(
            kind=usable(ArrangementKind.COMPANY_CAR),
            vehicle=usable(VehicleRef(catalogue_id="CAR-09")),
        ),
    )


def case(owner: UUID, facts: EmployeeProfile | None = None, **overrides: Any) -> ComparisonCase:
    values: dict[str, Any] = {
        "case_id": uuid4(),
        "conversation_id": uuid4(),
        "owner_subject_id": owner,
        "calculation_month": "2026-10",
        "reference_date": date(2026, 10, 1),
        "version": 0,
        "lifecycle_status": CaseLifecycleStatus.ACTIVE,
        "actual_facts": facts or profile(owner),
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return ComparisonCase(**values)


def alternative(case_id: UUID, catalogue_id: str = "CAR-10", **overrides: Any) -> Alternative:
    values: dict[str, Any] = {
        "alternative_id": uuid4(),
        "case_id": case_id,
        "version": 0,
        "kind": AlternativeKind.VEHICLE,
        "option_key": f"vehicle:{catalogue_id}",
        "catalogue_id": catalogue_id,
        "track": Track.REGULAR,
        "active": True,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return Alternative(**values)


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def request(
    case_id: UUID,
    created_by: UUID,
    *,
    alt: Alternative | None = None,
    subject: str = "CAR-10",
    key: UUID | None = None,
    question: str = "מה ההשתתפות החודשית ברכב?",
) -> RepresentativeRequest:
    return RepresentativeRequest(
        request_id=uuid4(),
        case_id=case_id,
        field_code=RepresentativeFieldCode.ALTERNATIVE_PARTICIPATION,
        expected_value_type=ExpectedValueType.MONEY,
        question_text=question,
        subject_snapshot={"catalogueId": subject},
        subject_fingerprint=fingerprint(subject),
        case_version_at_creation=0,
        request_version=0,
        status=RequestStatus.OPEN,
        idempotency_key=key or uuid4(),
        created_by=created_by,
        created_at=NOW,
        updated_at=NOW,
        alternative_id=None if alt is None else alt.alternative_id,
        alternative_version=None if alt is None else alt.version,
        option_key=None if alt is None else alt.option_key,
    )


def result_set(case_id: UUID, release: PublishedRelease, **overrides: Any) -> OptionResultSetRecord:
    values: dict[str, Any] = {
        "result_set_id": uuid4(),
        "case_id": case_id,
        "case_version_used": 0,
        "reference_release_id": release.release_id,
        "request": {"categories": ["regular"]},
        "items": [{"optionKey": "vehicle:CAR-10", "eligibility": "eligible"}],
        "unchecked_categories": ["family"],
        "scope_complete": False,
        "created_at": NOW,
    }
    values.update(overrides)
    return OptionResultSetRecord(**values)


def run_record(
    case_id: UUID, release: PublishedRelease, created_by: UUID, **overrides: Any
) -> ComparisonRunRecord:
    values: dict[str, Any] = {
        "run_id": uuid4(),
        "case_id": case_id,
        "kind": RunKind.REGULAR,
        "calculation_month": "2026-10",
        "case_version_used": 0,
        "profile_version_used": 1,
        "reference_release_id": release.release_id,
        "rules_snapshot_id": release.rules_snapshot_id,
        "catalogue_version_id": release.catalogue_version_id,
        "tax_dataset_version_id": release.tax_dataset_version_id,
        "selection": {"mode": "explicit", "optionKeys": ["vehicle:CAR-10"]},
        "include_current": True,
        "input_snapshot": {"snapshotVersion": 1, "baseGross": "18500.123456789"},
        "result": {"net": {"raw": "11692.817000982123456789", "display": "11693"}},
        "operation_id": uuid4(),
        "created_by": created_by,
        "created_at": NOW,
    }
    values.update(overrides)
    return ComparisonRunRecord(**values)


async def new_user(p: Persistence, role: Role = Role.EMPLOYEE, name: str = "עובד/ת בדיקה") -> UUID:
    user_id = uuid4()
    async with p.uow() as uow:
        await uow.users.add(
            UserAccount(user_id, f"u{user_id.hex[:16]}", frozenset({role}), display_name=name)
        )
        await uow.commit()
    return user_id


async def new_owner_with_case(p: Persistence) -> tuple[UUID, ComparisonCase]:
    owner = await new_user(p)
    stored = case(owner)
    async with p.uow() as uow:
        await uow.profiles.add(profile(owner))
        await uow.cases.add(stored)
        await uow.commit()
    return owner, stored


async def latest_release(p: Persistence) -> PublishedRelease:
    release = await p.reference.latest_release()
    assert release is not None, "business_db must have a published reference release"
    return release
