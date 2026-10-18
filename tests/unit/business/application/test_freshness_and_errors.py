"""Freshness rules and error translation (stable codes, no details for NOT_FOUND)."""

from datetime import UTC, datetime
from uuid import uuid4

from app.business.application.cases.freshness import run_freshness
from app.business.application.errors import (
    InvalidInput,
    InvalidStateTransition,
    NotFoundError,
    OutOfSupportedRange,
    PayloadMismatch,
    VersionConflict,
    error_envelope,
    to_business_error,
)
from app.business.application.ports.repositories import (
    OperationPayloadMismatchError,
    VersionConflictError,
)
from app.business.domain.cases import ComparisonRunRecord, RunKind
from app.business.domain.errors import InvalidValueError, OutOfSupportedRangeError
from app.business.domain.representatives import InvalidStateTransitionError
from app.contracts.common import EnvelopeStatus, ErrorCode, Freshness

RELEASE = uuid4()


def _run(kind: RunKind = RunKind.REGULAR) -> ComparisonRunRecord:
    return ComparisonRunRecord(
        run_id=uuid4(),
        case_id=uuid4(),
        kind=kind,
        calculation_month="2026-10",
        case_version_used=3,
        profile_version_used=5,
        reference_release_id=RELEASE,
        rules_snapshot_id=uuid4(),
        catalogue_version_id=uuid4(),
        tax_dataset_version_id=uuid4(),
        selection={},
        include_current=True,
        input_snapshot={},
        result={},
        operation_id=uuid4(),
        created_by=uuid4(),
        created_at=datetime(2026, 10, 18, tzinfo=UTC),
    )


def test_run_freshness() -> None:
    regular, hypothetical = _run(), _run(RunKind.HYPOTHETICAL)
    current = {"case_version": 3, "latest_profile_version": 5, "latest_release_id": RELEASE}
    assert run_freshness(regular, **current) is Freshness.CURRENT
    assert run_freshness(regular, **{**current, "case_version": 4}) is Freshness.STALE
    assert run_freshness(regular, **{**current, "latest_release_id": uuid4()}) is Freshness.STALE
    assert run_freshness(regular, **{**current, "latest_profile_version": 6}) is Freshness.STALE
    # a hypothetical run keeps its fixed baseline: a profile change alone does not stale it
    assert (
        run_freshness(hypothetical, **{**current, "latest_profile_version": 6}) is Freshness.CURRENT
    )


def test_error_translation_and_envelopes() -> None:
    assert isinstance(to_business_error(VersionConflictError("case", uuid4(), 1)), VersionConflict)
    assert isinstance(to_business_error(OperationPayloadMismatchError("x")), PayloadMismatch)
    assert isinstance(to_business_error(InvalidStateTransitionError("x")), InvalidStateTransition)
    out_of_range = to_business_error(OutOfSupportedRangeError("x", field="payroll.baseGross"))
    assert isinstance(out_of_range, OutOfSupportedRange)
    assert out_of_range.safe_details == {"field": "payroll.baseGross"}
    assert isinstance(to_business_error(InvalidValueError("x")), InvalidInput)
    assert to_business_error(RuntimeError("x")) is None

    envelope = error_envelope(NotFoundError(), "trace-1")
    assert envelope.status is EnvelopeStatus.ERROR and envelope.data is None
    assert envelope.error is not None and envelope.error.code is ErrorCode.NOT_FOUND
    assert envelope.error.safe_details == {}
