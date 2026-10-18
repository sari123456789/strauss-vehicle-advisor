"""Strict JSONB codec for facts documents, hypothetical overrides and JSON snapshots.

Facts document (`employee_profiles.fields`, `cases.actual_facts`), schema version 1: exactly
`schemaVersion` plus the 16 fact paths; every field is an envelope of exactly
`value, status, source, sourceMessageId, updatedAt, revision, lastConfirmedAt`. A missing
field is stored explicitly as `value: null, status: "missing"` - never as zero. Decimals are
written as plain decimal strings (`format(d, "f")`, no exponent) and parsed with `Decimal(str)`;
JSON numbers are accepted only for the integer child count and revisions. Unknown keys,
unknown enum values and type mismatches raise `PersistenceFormatError`. The database checks
the same rules again (`business.is_valid_facts_document`).
"""

import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.business.domain.arrangements import ArrangementKind
from app.business.domain.cases import (
    FactPath,
    HypotheticalOverride,
    profile_fact,
    with_profile_fact,
)
from app.business.domain.employee_profiles import EmployeeProfile
from app.business.domain.errors import DomainError
from app.business.domain.json_values import JsonObject, JsonValue, freeze_object
from app.business.domain.provenance import FieldStatus, FieldValue, SourceType
from app.business.domain.rules import EntitlementGroup
from app.business.domain.vehicles import Propulsion, VehicleRef, VehicleTaxKey

FACTS_SCHEMA_VERSION = 1
ENVELOPE_KEYS = frozenset(
    {"value", "status", "source", "sourceMessageId", "updatedAt", "revision", "lastConfirmedAt"}
)
OVERRIDE_KEYS = frozenset({"path", "value", "sourceMessageId", "createdAt"})
TAX_KEY_FIELDS = (
    ("taxYear", "tax_year"),
    ("registrationYear", "registration_year"),
    ("vehicleTypeCode", "vehicle_type_code"),
    ("manufacturerCode", "manufacturer_code"),
    ("modelCode", "model_code"),
)
_DECIMAL = re.compile(r"^\d+(\.\d+)?$")
_DECIMAL_PATHS = {
    FactPath.BASE_GROSS,
    FactPath.COMMISSIONS_GROSS,
    FactPath.BONUS_GROSS,
    FactPath.PENSION_EMPLOYEE_RATE,
    FactPath.STUDY_FUND_EMPLOYEE_RATE,
    FactPath.CREDIT_POINTS,
    FactPath.CURRENT_PARTICIPATION,
    FactPath.CURRENT_FAMILY_DEDUCTION,
    FactPath.CURRENT_ALLOWANCE,
}
_ENUM_PATHS: Mapping[FactPath, type[EntitlementGroup | ArrangementKind | Propulsion]] = {
    FactPath.ENTITLEMENT_GROUP: EntitlementGroup,
    FactPath.CURRENT_KIND: ArrangementKind,
    FactPath.CURRENT_PROPULSION: Propulsion,
}


class PersistenceFormatError(ValueError):
    """A stored JSON document does not match the persistence format."""


# --- scalars ---------------------------------------------------------------------------


def decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise PersistenceFormatError("NaN/Infinity cannot be stored")
    return format(value, "f")


def parse_decimal(value: object, where: str) -> Decimal:
    if not isinstance(value, str) or not _DECIMAL.fullmatch(value):
        raise PersistenceFormatError(f"{where}: expected a decimal string, got {value!r}")
    return Decimal(value)


def timestamp_text(value: datetime) -> str:
    if value.utcoffset() is None:
        raise PersistenceFormatError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: object, where: str) -> datetime:
    if not isinstance(value, str):
        raise PersistenceFormatError(f"{where}: expected a timestamp string, got {value!r}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise PersistenceFormatError(f"{where}: invalid timestamp {value!r}") from None
    if parsed.utcoffset() is None:
        raise PersistenceFormatError(f"{where}: timestamp without time zone")
    return parsed


def _optional(value: object, parse: Any, where: str) -> Any:
    return None if value is None else parse(value, where)


def _parse_uuid(value: object, where: str) -> UUID:
    if not isinstance(value, str):
        raise PersistenceFormatError(f"{where}: expected a UUID string")
    try:
        return UUID(value)
    except ValueError:
        raise PersistenceFormatError(f"{where}: invalid UUID {value!r}") from None


def _exact_keys(document: object, keys: frozenset[str], where: str) -> Mapping[str, Any]:
    if not isinstance(document, Mapping):
        raise PersistenceFormatError(f"{where}: expected a JSON object")
    found = set(document)
    if found != keys:
        raise PersistenceFormatError(
            f"{where}: unexpected keys {sorted(found - keys)}, missing {sorted(keys - found)}"
        )
    return document


# --- fact values -----------------------------------------------------------------------


def encode_fact_value(path: FactPath, value: object) -> Any:
    if value is None:
        return None
    if path in _DECIMAL_PATHS:
        if not isinstance(value, Decimal):
            raise PersistenceFormatError(f"{path}: expected a Decimal")
        return decimal_text(value)
    if path in _ENUM_PATHS:
        enum_type = _ENUM_PATHS[path]
        if not isinstance(value, enum_type):
            raise PersistenceFormatError(f"{path}: expected {enum_type.__name__}")
        return value.value
    if path is FactPath.CURRENT_VEHICLE:
        if not isinstance(value, VehicleRef):
            raise PersistenceFormatError(f"{path}: expected a VehicleRef")
        if value.catalogue_id is not None:
            return {"catalogueId": value.catalogue_id}
        key = value.tax_key
        assert key is not None
        return {"taxKey": {json_name: getattr(key, attr) for json_name, attr in TAX_KEY_FIELDS}}
    if path is FactPath.QUALIFYING_CHILDREN_COUNT:
        if isinstance(value, bool) or not isinstance(value, int):
            raise PersistenceFormatError(f"{path}: expected an integer")
        return value
    if path is FactPath.CURRENT_FAMILY_DEDUCTION_INCLUDED:
        if not isinstance(value, bool):
            raise PersistenceFormatError(f"{path}: expected a boolean")
        return value
    if path is FactPath.OCCUPATION_ID:
        if not isinstance(value, str):
            raise PersistenceFormatError(f"{path}: expected a string")
        return value
    raise PersistenceFormatError(f"unsupported fact path {path}")


def decode_fact_value(path: FactPath, value: object) -> object:
    where = path.value
    if value is None:
        return None
    if path in _DECIMAL_PATHS:
        return parse_decimal(value, where)
    if path in _ENUM_PATHS:
        if not isinstance(value, str):
            raise PersistenceFormatError(f"{where}: expected a string")
        try:
            return _ENUM_PATHS[path](value)
        except ValueError:
            raise PersistenceFormatError(f"{where}: unknown value {value!r}") from None
    if path is FactPath.CURRENT_VEHICLE:
        if isinstance(value, Mapping) and set(value) == {"catalogueId"}:
            catalogue_id = value["catalogueId"]
            if not isinstance(catalogue_id, str):
                raise PersistenceFormatError(f"{where}: catalogueId must be a string")
            return _domain(lambda: VehicleRef(catalogue_id=catalogue_id), where)
        if isinstance(value, Mapping) and set(value) == {"taxKey"}:
            key = _exact_keys(value["taxKey"], frozenset(n for n, _ in TAX_KEY_FIELDS), where)
            if not all(isinstance(key[n], str) for n, _ in TAX_KEY_FIELDS):
                raise PersistenceFormatError(f"{where}: tax-key codes must be strings")
            tax_key = _domain(
                lambda: VehicleTaxKey(**{attr: key[name] for name, attr in TAX_KEY_FIELDS}), where
            )
            return VehicleRef(tax_key=tax_key)
        raise PersistenceFormatError(f"{where}: expected exactly catalogueId or taxKey")
    if path is FactPath.QUALIFYING_CHILDREN_COUNT:
        if isinstance(value, bool) or not isinstance(value, int):
            raise PersistenceFormatError(f"{where}: expected an integer")
        return value
    if path is FactPath.CURRENT_FAMILY_DEDUCTION_INCLUDED:
        if not isinstance(value, bool):
            raise PersistenceFormatError(f"{where}: expected a boolean")
        return value
    if path is FactPath.OCCUPATION_ID:
        if not isinstance(value, str):
            raise PersistenceFormatError(f"{where}: expected a string")
        return value
    raise PersistenceFormatError(f"unsupported fact path {path}")


def _domain[T](build: Callable[[], T], where: str) -> T:
    try:
        result = build()
    except DomainError as error:
        raise PersistenceFormatError(f"{where}: {error}") from error
    return result


# --- facts documents -------------------------------------------------------------------


def encode_field(path: FactPath, field: FieldValue[Any]) -> dict[str, Any]:
    return {
        "value": encode_fact_value(path, field.value),
        "status": field.status.value,
        "source": None if field.source is None else field.source.value,
        "sourceMessageId": None
        if field.source_message_id is None
        else str(field.source_message_id),
        "updatedAt": None if field.updated_at is None else timestamp_text(field.updated_at),
        "revision": field.revision,
        "lastConfirmedAt": (
            None if field.last_confirmed_at is None else timestamp_text(field.last_confirmed_at)
        ),
    }


def decode_field(path: FactPath, envelope: object) -> FieldValue[Any]:
    where = path.value
    data = _exact_keys(envelope, ENVELOPE_KEYS, where)
    revision = data["revision"]
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise PersistenceFormatError(f"{where}: revision must be an integer")
    try:
        status = FieldStatus(data["status"])
        source = None if data["source"] is None else SourceType(data["source"])
    except ValueError as error:
        raise PersistenceFormatError(f"{where}: {error}") from None
    return _domain(
        lambda: FieldValue(
            value=decode_fact_value(path, data["value"]),
            status=status,
            source=source,
            source_message_id=_optional(data["sourceMessageId"], _parse_uuid, where),
            updated_at=_optional(data["updatedAt"], parse_timestamp, where),
            revision=revision,
            last_confirmed_at=_optional(data["lastConfirmedAt"], parse_timestamp, where),
        ),
        where,
    )


def encode_facts(profile: EmployeeProfile) -> dict[str, Any]:
    document: dict[str, Any] = {"schemaVersion": FACTS_SCHEMA_VERSION}
    for path in FactPath:
        document[path.value] = encode_field(path, profile_fact(profile, path))
    return document


def decode_facts(document: object, *, employee_id: UUID, version: int) -> EmployeeProfile:
    keys = frozenset({"schemaVersion", *(p.value for p in FactPath)})
    data = _exact_keys(document, keys, "facts")
    if data["schemaVersion"] != FACTS_SCHEMA_VERSION or isinstance(data["schemaVersion"], bool):
        raise PersistenceFormatError(f"facts: unsupported schemaVersion {data['schemaVersion']!r}")
    try:
        profile = EmployeeProfile(employee_id=employee_id, version=version)
        for path in FactPath:
            profile = with_profile_fact(profile, path, decode_field(path, data[path.value]))
    except DomainError as error:
        raise PersistenceFormatError(f"facts: {error}") from error
    return profile


# --- hypothetical overrides ------------------------------------------------------------


def encode_overrides(overrides: tuple[HypotheticalOverride, ...]) -> list[dict[str, Any]]:
    return [
        {
            "path": o.path.value,
            "value": encode_fact_value(o.path, o.value),
            "sourceMessageId": None if o.source_message_id is None else str(o.source_message_id),
            "createdAt": timestamp_text(o.created_at),
        }
        for o in overrides
    ]


def decode_overrides(document: object) -> tuple[HypotheticalOverride, ...]:
    if not isinstance(document, list):
        raise PersistenceFormatError("hypotheticalOverrides: expected a JSON array")
    result: list[HypotheticalOverride] = []
    for index, item in enumerate(document):
        where = f"hypotheticalOverrides[{index}]"
        data = _exact_keys(item, OVERRIDE_KEYS, where)
        try:
            path = FactPath(data["path"])
        except ValueError:
            raise PersistenceFormatError(f"{where}: unknown path {data['path']!r}") from None
        try:
            override = HypotheticalOverride(
                path=path,
                value=decode_fact_value(path, data["value"]),
                created_at=parse_timestamp(data["createdAt"], where),
                source_message_id=_optional(data["sourceMessageId"], _parse_uuid, where),
            )
        except DomainError as error:
            raise PersistenceFormatError(f"{where}: {error}") from error
        result.append(override)
    return tuple(result)


# --- generic JSON snapshots ------------------------------------------------------------


def thaw(value: JsonValue) -> Any:
    """Plain (mutable) JSON structure for storage."""
    if isinstance(value, Mapping):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [thaw(v) for v in value]
    return value


def freeze_document(value: object, where: str) -> JsonObject:
    try:
        return freeze_object(value, path=where)
    except DomainError as error:
        raise PersistenceFormatError(str(error)) from error
