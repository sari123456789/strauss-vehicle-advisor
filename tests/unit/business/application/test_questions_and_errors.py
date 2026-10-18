"""Step 4C pure rules: question scope, minimal subject snapshots, T61 error mapping."""

from decimal import Decimal
from uuid import uuid4

import pytest

from app.business.application.errors import (
    InfrastructureUnavailable,
    InvalidInput,
    to_business_error,
)
from app.business.application.ports.reference_data import ReferenceDataUnavailableError
from app.business.application.representatives.question_service import (
    VALUE_TYPES,
    subject_snapshot,
    validate_scope,
)
from app.business.domain.employee_profiles import EmployeeProfile, SalaryFacts
from app.business.domain.provenance import FieldValue, SourceType
from app.business.domain.representatives import RepresentativeFieldCode as Code
from app.contracts.common import ErrorCode


@pytest.mark.parametrize(
    ("code", "option", "ok"),
    [
        (Code.ALTERNATIVE_PARTICIPATION, "vehicle:CAR-10", True),
        (Code.ALTERNATIVE_PARTICIPATION, None, False),
        (Code.ALTERNATIVE_PARTICIPATION, "waiver", False),
        (Code.OCCUPATION_WAIVER_POLICY, "waiver", True),
        (Code.OCCUPATION_WAIVER_POLICY, None, True),
        (Code.OCCUPATION_WAIVER_POLICY, "vehicle:CAR-10", False),
        (Code.ENTITLEMENT_GROUP, None, True),
        (Code.ENTITLEMENT_GROUP, "vehicle:CAR-10", False),
        (Code.VEHICLE_PROPULSION, None, True),
        (Code.VEHICLE_TAX_KEY, "waiver", False),
    ],
)
def test_question_scope(code: Code, option: str | None, ok: bool) -> None:
    if ok:
        validate_scope(code, option)
    else:
        with pytest.raises(InvalidInput):
            validate_scope(code, option)


def test_every_field_code_has_a_value_type() -> None:
    assert set(VALUE_TYPES) == set(Code)


def test_subject_snapshot_never_contains_salary() -> None:
    profile = EmployeeProfile(
        uuid4(),
        3,
        salary=SalaryFacts(base_gross=FieldValue.usable(Decimal("18500"), SourceType.EMPLOYEE)),
        occupation_id=FieldValue.usable("OCC-SOFTWARE-DEV", SourceType.EMPLOYEE),
    )
    for code in Code:
        option = "vehicle:CAR-10" if code is Code.ALTERNATIVE_PARTICIPATION else None
        snapshot = subject_snapshot(code, option, profile)
        assert "18500" not in repr(snapshot)
        assert set(snapshot) <= {"catalogueId", "occupationId", "currentVehicle"}


def test_reference_data_unavailable_is_infra_unavailable() -> None:
    error = to_business_error(ReferenceDataUnavailableError("down"))
    assert isinstance(error, InfrastructureUnavailable)
    assert error.code is ErrorCode.INFRA_UNAVAILABLE
    assert error.safe_details == {"reason": "reference_data_unavailable"}
