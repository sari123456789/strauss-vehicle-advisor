"""Immutable JSON values for business snapshots (standard library only).

Snapshots, result sets and request context are stored as JSON documents. Binary floats are
rejected: money and rates travel as decimal strings, so no value is ever converted through a
float. `freeze_json` validates and deep-freezes a plain JSON structure (dict -> read-only
mapping, list -> tuple).
"""

from collections.abc import Mapping
from types import MappingProxyType

from app.business.domain.errors import InvalidValueError

type JsonScalar = str | int | bool | None
type JsonValue = JsonScalar | tuple[JsonValue, ...] | Mapping[str, JsonValue]
type JsonObject = Mapping[str, JsonValue]


def freeze_json(value: object, *, path: str = "$") -> JsonValue:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        raise InvalidValueError(f"{path}: binary floats are not allowed in JSON values", field=path)
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidValueError(f"{path}: JSON object keys must be strings", field=path)
            frozen[key] = freeze_json(item, path=f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(freeze_json(item, path=f"{path}[{i}]") for i, item in enumerate(value))
    raise InvalidValueError(
        f"{path}: unsupported JSON value of type {type(value).__name__}", field=path
    )


def freeze_object(value: object, *, path: str = "$") -> JsonObject:
    frozen = freeze_json(value, path=path)
    if not isinstance(frozen, Mapping):
        raise InvalidValueError(f"{path}: expected a JSON object", field=path)
    return frozen


def freeze_array(value: object, *, path: str = "$") -> tuple[JsonValue, ...]:
    frozen = freeze_json(value, path=path)
    if not isinstance(frozen, tuple):
        raise InvalidValueError(f"{path}: expected a JSON array", field=path)
    return frozen
