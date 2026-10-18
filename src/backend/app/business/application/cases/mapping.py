"""Domain -> shared contract DTOs. Money/rates become exact decimal strings (never floats);
missing facts stay `value: null, status: missing`.
"""

from decimal import Decimal
from enum import Enum
from typing import Any

from app.business.domain.cases import (
    Alternative,
    ComparisonCase,
    FactPath,
    HypotheticalOverride,
    profile_fact,
)
from app.business.domain.employee_profiles import EmployeeProfile
from app.business.domain.provenance import FieldValue
from app.business.domain.representatives import RepresentativeRequest
from app.business.domain.vehicles import VehicleRef
from app.contracts.cases import (
    AlternativeView,
    CaseContext,
    OpenRequestSummary,
    RunSummary,
)
from app.contracts.common import Freshness
from app.contracts.profile import EmployeeProfileView

_PROFILE_ATTRIBUTES: dict[FactPath, str] = {
    FactPath.BASE_GROSS: "base_gross",
    FactPath.COMMISSIONS_GROSS: "commissions_gross",
    FactPath.BONUS_GROSS: "bonus_gross",
    FactPath.PENSION_EMPLOYEE_RATE: "pension_employee_rate",
    FactPath.STUDY_FUND_EMPLOYEE_RATE: "study_fund_employee_rate",
    FactPath.CREDIT_POINTS: "credit_points",
    FactPath.OCCUPATION_ID: "occupation_id",
    FactPath.ENTITLEMENT_GROUP: "entitlement_group",
    FactPath.QUALIFYING_CHILDREN_COUNT: "qualifying_children_count",
}
_ARRANGEMENT_ATTRIBUTES: dict[FactPath, str] = {
    FactPath.CURRENT_KIND: "kind",
    FactPath.CURRENT_VEHICLE: "vehicle",
    FactPath.CURRENT_PROPULSION: "propulsion",
    FactPath.CURRENT_PARTICIPATION: "participation",
    FactPath.CURRENT_FAMILY_DEDUCTION: "family_deduction",
    FactPath.CURRENT_FAMILY_DEDUCTION_INCLUDED: "family_deduction_included_in_participation",
    FactPath.CURRENT_ALLOWANCE: "allowance",
}


def contract_value(value: object) -> Any:
    if value is None or isinstance(value, bool | int | str) and not isinstance(value, Enum):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, VehicleRef):
        if value.catalogue_id is not None:
            return {"catalogue_id": value.catalogue_id}
        key = value.tax_key
        assert key is not None
        return {
            "tax_key": {
                "tax_year": key.tax_year,
                "registration_year": key.registration_year,
                "vehicle_type_code": key.vehicle_type_code,
                "manufacturer_code": key.manufacturer_code,
                "model_code": key.model_code,
            }
        }
    raise TypeError(f"unsupported fact value {type(value).__name__}")


def _field(field: FieldValue[Any]) -> dict[str, Any]:
    return {
        "value": contract_value(field.value),
        "status": field.status.value,
        "source": None if field.source is None else field.source.value,
        "source_message_id": field.source_message_id,
        "updated_at": field.updated_at,
        "revision": field.revision,
        "last_confirmed_at": field.last_confirmed_at,
    }


def profile_view(profile: EmployeeProfile) -> EmployeeProfileView:
    data: dict[str, Any] = {"profile_version": profile.version}
    for path, name in _PROFILE_ATTRIBUTES.items():
        data[name] = _field(profile_fact(profile, path))
    data["current_arrangement"] = {
        name: _field(profile_fact(profile, path)) for path, name in _ARRANGEMENT_ATTRIBUTES.items()
    }
    return EmployeeProfileView.model_validate(data)


def override_view(override: HypotheticalOverride) -> dict[str, Any]:
    return {
        "field": override.path.value,
        "value": contract_value(override.value),
        "source_message_id": override.source_message_id,
    }


def alternative_view(alternative: Alternative) -> AlternativeView:
    return AlternativeView.model_validate(
        {
            "alternative_id": alternative.alternative_id,
            "alternative_version": alternative.version,
            "option_key": alternative.option_key,
            "catalogue_id": alternative.catalogue_id,
            "track": None if alternative.track is None else alternative.track.value,
            "active": alternative.active,
        }
    )


def request_summary(request: RepresentativeRequest) -> OpenRequestSummary:
    return OpenRequestSummary.model_validate(
        {
            "request_id": request.request_id,
            "status": request.status.value,
            "field_code": request.field_code.value,
            "option_key": request.option_key,
        }
    )


def case_context(
    case: ComparisonCase,
    latest_profile: EmployeeProfile,
    alternatives: tuple[Alternative, ...],
    open_requests: tuple[RepresentativeRequest, ...],
    runs: list[tuple[Any, Freshness]],
) -> CaseContext:
    return CaseContext.model_validate(
        {
            "case_id": case.case_id,
            "conversation_id": case.conversation_id,
            "case_version": case.version,
            "calculation_month": case.calculation_month,
            "reference_date": case.reference_date,
            "profile": profile_view(latest_profile),
            "salary_profile_version_used": case.salary_profile_version_used,
            "latest_profile_version": latest_profile.version,
            "profile_mismatch": case.salary_profile_version_used != latest_profile.version,
            "hypothetical_overrides": [override_view(o) for o in case.hypothetical_overrides],
            "alternatives": [alternative_view(a) for a in alternatives],
            "open_requests": [request_summary(r) for r in open_requests],
            "recent_runs": [
                RunSummary.model_validate(
                    {
                        "run_id": run.run_id,
                        "kind": run.kind.value,
                        "created_at": run.created_at,
                        "freshness": freshness,
                    }
                )
                for run, freshness in runs
            ],
        }
    )
