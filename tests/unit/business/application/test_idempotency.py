"""Canonical request hashing for operation idempotency."""

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

import pytest

from app.business.application.idempotency import canonical_request_hash
from app.business.application.ports.repositories import OperationKey, ScopeKind

OPERATION = UUID("11111111-1111-4111-8111-111111111111")


class Mode(StrEnum):
    ACTUAL = "actual_update"


def test_hash_ignores_key_order_and_is_deterministic() -> None:
    first = canonical_request_hash({"a": "1", "b": {"y": 2, "x": [1, 2]}})
    second = canonical_request_hash({"b": {"x": [1, 2], "y": 2}, "a": "1"})
    assert first == second
    assert len(first) == 64


def test_values_are_canonicalized_to_strings() -> None:
    payload = {
        "mode": Mode.ACTUAL,
        "operationId": OPERATION,
        "amount": Decimal("18500.00"),
        "month": date(2026, 10, 1),
        "at": datetime(2026, 10, 18, tzinfo=UTC),
    }
    expected = {
        "mode": "actual_update",
        "operationId": str(OPERATION),
        "amount": "18500.00",
        "month": "2026-10-01",
        "at": "2026-10-18T00:00:00+00:00",
    }
    assert canonical_request_hash(payload) == canonical_request_hash(expected)


def test_any_payload_difference_changes_the_hash() -> None:
    base = canonical_request_hash({"value": "18500.00"})
    assert base != canonical_request_hash({"value": "18500.0"})
    assert base != canonical_request_hash({"value": "18501.00"})
    assert canonical_request_hash({"v": 1}) != canonical_request_hash({"v": "1"})


@pytest.mark.parametrize(
    "payload", [{"v": 0.1}, {"v": Decimal("NaN")}, {"v": {1: "x"}}, {"v": object()}]
)
def test_unsupported_values_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(TypeError):
        canonical_request_hash(payload)


def test_operation_key_validates_the_operation_type() -> None:
    OperationKey(OPERATION, ScopeKind.CASE, OPERATION, "update_inputs", OPERATION)
    with pytest.raises(ValueError):
        OperationKey(OPERATION, ScopeKind.CASE, OPERATION, "Update Inputs", OPERATION)
