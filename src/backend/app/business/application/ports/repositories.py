"""Business repository ports (Step 3A). Infrastructure implements them; bootstrap wires them.

Only Domain types cross this boundary. Every lookup of a case-scoped resource is scoped by
the case (and a case by its owner): an identifier alone never retrieves a resource. This
scoping supports, but does not replace, Application-level authorization (role, ownership,
`NOT_FOUND` without existence disclosure), which runs before any repository call.

Optimistic concurrency: `update(entity)` writes only if the stored version still equals
`entity.version` (the version that was read) and returns the entity with version + 1;
otherwise it raises `VersionConflictError`. There is never a last-write-wins update.
"""

import re
from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.business.domain.cases import (
    Alternative,
    ComparisonCase,
    ComparisonRunRecord,
    OptionResultSetRecord,
)
from app.business.domain.employee_profiles import EmployeeProfile
from app.business.domain.json_values import JsonObject
from app.business.domain.representatives import RepresentativeRequest, RequestStatus

_OPERATION_TYPE = re.compile(r"^[a-z_]{3,64}$")


class VersionConflictError(Exception):
    """The stored version differs from the expected one (stale write)."""

    code = "VERSION_CONFLICT"

    def __init__(self, resource: str, resource_id: UUID, expected_version: int) -> None:
        super().__init__(f"{resource} {resource_id}: version {expected_version} is stale")
        self.resource, self.resource_id, self.expected_version = (
            resource,
            resource_id,
            expected_version,
        )


class OperationPayloadMismatchError(Exception):
    """An operation ID was reused with a different request payload."""

    code = "OPERATION_PAYLOAD_MISMATCH"


class DuplicateResourceError(Exception):
    """A uniquely identified resource already exists (e.g. a case for the conversation)."""

    code = "DUPLICATE_RESOURCE"


class ScopeKind(StrEnum):
    CONVERSATION = "conversation"
    CASE = "case"
    REPRESENTATIVE_REQUEST = "representative_request"
    """Reserved for the representative-answer stage."""


@dataclass(frozen=True, slots=True)
class OperationKey:
    actor_subject_id: UUID
    scope_kind: ScopeKind
    scope_id: UUID
    operation_type: str
    operation_id: UUID

    def __post_init__(self) -> None:
        if not _OPERATION_TYPE.fullmatch(self.operation_type):
            raise ValueError(f"invalid operation type {self.operation_type!r}")


@dataclass(frozen=True, slots=True)
class Claimed:
    """First execution: the caller performs the mutation and `complete`s it in the same UoW."""

    key: OperationKey


@dataclass(frozen=True, slots=True)
class Replay:
    """The same operation (same payload hash) was already committed: return its result."""

    key: OperationKey
    result: JsonObject | None


ClaimResult = Claimed | Replay


@runtime_checkable
class EmployeeProfileRepository(Protocol):
    async def get(self, employee_id: UUID) -> EmployeeProfile | None: ...

    async def add(self, profile: EmployeeProfile, *, origin: JsonObject | None = None) -> None:
        """Insert a new profile; `origin` is internal provenance (e.g. demo seed), not shared."""
        ...

    async def update(self, profile: EmployeeProfile) -> EmployeeProfile: ...

    async def get_origin(self, employee_id: UUID) -> JsonObject | None: ...

    async def lock(self, employee_id: UUID) -> EmployeeProfile | None:
        """Read the profile row `FOR UPDATE` (held until the Unit of Work ends).

        Lock order for calculations is fixed - operation claim, then profile, then case - the
        same order as every profile + case write, so no lock cycle can occur."""
        ...


@runtime_checkable
class CaseRepository(Protocol):
    async def get(self, case_id: UUID, *, owner_subject_id: UUID) -> ComparisonCase | None: ...

    async def get_by_conversation(
        self, conversation_id: UUID, *, owner_subject_id: UUID
    ) -> ComparisonCase | None: ...

    async def add(self, case: ComparisonCase) -> None:
        """Raises `DuplicateResourceError` if the conversation already has a case."""
        ...

    async def update(self, case: ComparisonCase) -> ComparisonCase: ...

    async def lock(self, case_id: UUID, *, owner_subject_id: UUID) -> ComparisonCase | None:
        """Owner-scoped read `FOR UPDATE`; taken after the profile lock (see profiles)."""
        ...


@runtime_checkable
class AlternativeRepository(Protocol):
    async def list_for_case(
        self, case_id: UUID, *, active_only: bool = True
    ) -> tuple[Alternative, ...]: ...

    async def get(self, case_id: UUID, alternative_id: UUID) -> Alternative | None: ...

    async def find_active(self, case_id: UUID, option_key: str) -> Alternative | None: ...

    async def add(self, alternative: Alternative) -> None: ...

    async def update(self, alternative: Alternative) -> Alternative: ...


@runtime_checkable
class OperationRepository(Protocol):
    async def claim(self, key: OperationKey, request_hash: str) -> ClaimResult:
        """Raises `OperationPayloadMismatchError` for the same key with a different hash."""
        ...

    async def complete(self, key: OperationKey, result: JsonObject) -> None: ...


@runtime_checkable
class OptionResultSetRepository(Protocol):
    async def add(self, record: OptionResultSetRecord) -> None: ...

    async def get(self, case_id: UUID, result_set_id: UUID) -> OptionResultSetRecord | None: ...

    async def add_or_get_identical(
        self, record: OptionResultSetRecord
    ) -> tuple[OptionResultSetRecord, bool]:
        """Store the set unless the case already has one with the same `inputFingerprint`
        (in `record.request`); returns (stored set, created). Concurrent identical calls create
        one set (decision A12: retries do not multiply result sets; no public operation ID)."""
        ...


@runtime_checkable
class ComparisonRunRepository(Protocol):
    async def add(self, record: ComparisonRunRecord) -> None: ...

    async def get(self, case_id: UUID, run_id: UUID) -> ComparisonRunRecord | None: ...

    async def list_recent(self, case_id: UUID, limit: int) -> tuple[ComparisonRunRecord, ...]: ...


@runtime_checkable
class RepresentativeRequestRepository(Protocol):
    async def add_or_get_open(
        self, request: RepresentativeRequest
    ) -> tuple[RepresentativeRequest, bool]:
        """Return (stored request, created). An existing request with the same idempotency key
        or an equivalent open request (case, alternative, field, subject) is returned instead.
        """
        ...

    async def get(self, case_id: UUID, request_id: UUID) -> RepresentativeRequest | None: ...

    async def list_for_case(
        self, case_id: UUID, statuses: Collection[RequestStatus] | None = None
    ) -> tuple[RepresentativeRequest, ...]: ...

    async def update(self, request: RepresentativeRequest) -> RepresentativeRequest:
        """Optimistic on `request_version`; returns the request with request_version + 1."""
        ...
