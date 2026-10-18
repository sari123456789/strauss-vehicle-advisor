"""Canonical request hashing for operation idempotency (architecture Section 10).

The same operation ID with the same payload replays the stored result; the same ID with a
different payload is `OPERATION_PAYLOAD_MISMATCH`. The hash is SHA-256 over canonical JSON:
sorted keys, no whitespace, UTF-8. Decimals, UUIDs, dates and enums become strings; binary
floats are rejected. Numbers are not normalized: "18500.00" and "18500.0" are different
payloads (strict by design).
"""

import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID


def _canonical(value: object) -> object:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        raise TypeError("binary floats are not allowed in operation payloads")
    if isinstance(value, Enum):
        return _canonical(value.value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise TypeError("NaN/Infinity are not allowed in operation payloads")
        return format(value, "f")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Mapping):
        if not all(isinstance(k, str) for k in value):
            raise TypeError("payload object keys must be strings")
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_canonical(v) for v in value]
    raise TypeError(f"unsupported payload value of type {type(value).__name__}")


def canonical_request_hash(payload: Mapping[str, object]) -> str:
    text = json.dumps(
        _canonical(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
