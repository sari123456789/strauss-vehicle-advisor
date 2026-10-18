"""Immutable input snapshot of one comparison run (schema version 1, decision Q9).

Plain JSON (decimal strings, ISO timestamps, camelCase): everything the run was calculated
from - the effective facts with provenance, the fact basis, the overrides applied, the
resolved selection, each scenario's components and the pinned source versions. Stored once
with the run and never changed; `get_comparison_details` returns it as-is.
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from app.business.application.comparisons.facts import FactBasis
from app.business.application.comparisons.planner import Plan, as_json_key
from app.business.application.reference import PinnedReference
from app.business.domain.arrangements import AlternativeVehicleSpec, WaiverSpec
from app.business.domain.cases import Alternative, ComparisonCase, FactPath, profile_fact
from app.business.domain.provenance import FieldValue
from app.business.domain.vehicles import VehicleRef

SCHEMA_VERSION = 1


def json_value(value: object) -> Any:
    if value is None or isinstance(value, bool | int | str) and not isinstance(value, Enum):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, VehicleRef):
        if value.catalogue_id is not None:
            return {"catalogueId": value.catalogue_id}
        return {"taxKey": as_json_key(value.tax_key)}
    raise TypeError(f"unsupported snapshot value {type(value).__name__}")


def _fact(field: FieldValue[Any]) -> dict[str, Any]:
    return {
        "value": json_value(field.value),
        "status": field.status.value,
        "source": None if field.source is None else field.source.value,
        "revision": field.revision,
        "updatedAt": json_value(field.updated_at),
        "lastConfirmedAt": json_value(field.last_confirmed_at),
    }


def _component(value: FieldValue[Any]) -> Any:
    return json_value(value.value) if value.is_usable else None


def build_input_snapshot(
    *,
    case: ComparisonCase,
    basis: FactBasis,
    plan: Plan,
    pinned: PinnedReference,
    option_keys: list[str],
    result_set_id: UUID | None,
    alternatives: dict[str, Alternative],
    include_current: bool,
) -> dict[str, Any]:
    scenarios = []
    for option in plan.options:
        spec = option.spec
        entry: dict[str, Any] = {
            "optionKey": option.option_key,
            "alternativeId": json_value(
                alternatives[option.option_key].alternative_id
                if option.option_key in alternatives
                else None
            ),
            "alternativeVersion": alternatives[option.option_key].version
            if option.option_key in alternatives
            else None,
            "eligibility": {
                "status": option.decision.status.value,
                "reasons": [r.value for r in option.decision.reasons],
            },
            "blockers": [b.model_dump(mode="json", by_alias=True) for b in option.blockers],
        }
        if isinstance(spec, AlternativeVehicleSpec):
            assert option.vehicle is not None
            entry["vehicle"] = {
                "catalogueId": spec.catalogue_id,
                "taxKey": as_json_key(option.vehicle.tax_key),
                "track": spec.track.value,
                "propulsion": spec.propulsion.value,
                "valueOfUse": _component(spec.value_of_use),
                "participation": _component(spec.participation),
                "participationSource": spec.participation_source,
                "monthlyCompanyCost": json_value(spec.monthly_company_cost),
                "groupBudget": _component(spec.group_budget),
            }
        elif isinstance(spec, WaiverSpec):
            entry["waiver"] = {"allowance": _component(spec.allowance)}
        scenarios.append(entry)
    baseline = None
    if plan.baseline is not None:
        baseline = {
            "catalogueId": plan.baseline.catalogue_id,
            "taxKey": as_json_key(plan.baseline.tax_key),
            "calculable": plan.baseline.calculable,
            "blockers": [b.model_dump(mode="json", by_alias=True) for b in plan.baseline.blockers],
        }
    profile = plan.profile
    release = pinned.release
    return {
        "schemaVersion": SCHEMA_VERSION,
        "factBasis": basis.value,
        "calculationMonth": case.calculation_month,
        "referenceDate": case.reference_date.isoformat(),
        "caseVersionUsed": case.version,
        "profileVersionUsed": profile.version,
        "sourceVersions": {
            "referenceReleaseId": str(release.release_id),
            "rulesSnapshotId": str(release.rules_snapshot_id),
            "catalogueVersionId": str(release.catalogue_version_id),
            "taxDatasetVersionId": str(release.tax_dataset_version_id),
            "derivedPolicyVersion": release.derived_policy_version,
        },
        "facts": {path.value: _fact(profile_fact(profile, path)) for path in FactPath},
        "hypotheticalOverrides": [
            {
                "field": o.path.value,
                "value": json_value(o.value),
                "createdAt": json_value(o.created_at),
                "sourceMessageId": json_value(o.source_message_id),
            }
            for o in (case.hypothetical_overrides if basis is not FactBasis.ACTUAL else ())
        ],
        "selection": {"optionKeys": option_keys, "resultSetId": json_value(result_set_id)},
        "includeCurrent": include_current,
        "scenarios": scenarios,
        "baseline": baseline,
    }
