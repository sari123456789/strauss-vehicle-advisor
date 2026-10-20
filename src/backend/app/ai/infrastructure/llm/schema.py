"""Convert a Pydantic output model into the JSON Schema subset accepted by structured output.

Gemini's structured output supports a subset of JSON Schema (no `$ref`; keywords: type,
properties, required, additionalProperties, enum, format, minimum/maximum, items, prefixItems,
title, description, anyOf). References are inlined and other keywords are dropped. The provider
schema only *guides* generation: every response is still validated against the full Pydantic
model, so dropped constraints are enforced locally.

`minItems`/`maxItems` are dropped although documented: the approved free-tier model rejects the
interpretation schema with them (`400 INVALID_ARGUMENT`, verified 2026-10-20), while the same
schema without them is accepted. Array lengths stay enforced by the Pydantic models.
"""

from typing import Any

from pydantic import BaseModel

_KEPT_KEYS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "enum",
        "format",
        "minimum",
        "maximum",
        "items",
        "prefixItems",
        "title",
        "description",
        "anyOf",
    }
)
_MAX_DEPTH = 12


def _resolve(node: Any, defs: dict[str, Any], depth: int) -> Any:
    if depth > _MAX_DEPTH:
        raise ValueError("output schema is nested too deeply for structured output")
    if isinstance(node, list):
        return [_resolve(item, defs, depth + 1) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        return _resolve(defs[name], defs, depth + 1)
    if "const" in node:
        node = {**node, "enum": [node["const"]]}
    result: dict[str, Any] = {}
    for key, value in node.items():
        if key not in _KEPT_KEYS:
            continue
        if key == "properties":
            result[key] = {name: _resolve(prop, defs, depth + 1) for name, prop in value.items()}
        elif key in ("items", "anyOf", "prefixItems"):
            result[key] = _resolve(value, defs, depth + 1)
        elif key == "additionalProperties" and isinstance(value, dict):
            result[key] = _resolve(value, defs, depth + 1)
        else:
            result[key] = value
    return result


def provider_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    raw = model.model_json_schema(by_alias=True)
    defs = raw.pop("$defs", {})
    resolved = _resolve(raw, defs, 0)
    assert isinstance(resolved, dict)
    return resolved
