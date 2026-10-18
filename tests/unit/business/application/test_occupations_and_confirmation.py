"""Pure Application rules: occupation resolution (G2), confirmation (G3), the G4 hint and
`Outcome` envelopes."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from app.business.application.cases.confirmation import (
    confirm_fields,
    fields_requiring_confirmation,
)
from app.business.application.errors import NotFoundError, error_envelope
from app.business.application.occupations.occupation_service import name_forms, resolve
from app.business.application.outcomes import Outcome
from app.business.domain.cases import FactPath
from app.business.domain.employee_profiles import EmployeeProfile, SalaryFacts
from app.business.domain.occupations import OccupationPolicy
from app.business.domain.provenance import FieldStatus, FieldValue, SourceType
from app.business.domain.rules import EntitlementGroup
from app.contracts.catalogue import ResolveStatus
from app.contracts.common import EnvelopeStatus, FieldPath, Issue, IssueCode

OCCUPATIONS = (
    OccupationPolicy(
        "OCC-SOFTWARE-DEV", "מפתח/ת תוכנה", EntitlementGroup.BET, False, True, Decimal("1500")
    ),
    OccupationPolicy(
        "OCC-DATA-ANALYST", "אנליסט/ית נתונים", EntitlementGroup.BET, False, True, Decimal("1500")
    ),
    OccupationPolicy(
        "OCC-DEV-MGR", "מנהל/ת פיתוח", EntitlementGroup.GIMEL, False, True, Decimal("2000")
    ),
    OccupationPolicy(
        "OCC-PRODUCT-MGR", "מנהל/ת מוצר", EntitlementGroup.GIMEL, False, True, Decimal("2000")
    ),
)


def test_name_forms_are_the_official_name_and_its_two_written_forms() -> None:
    assert name_forms("אנליסט/ית נתונים") == {
        "אנליסט/ית נתונים",
        "אנליסט נתונים",
        "אנליסטית נתונים",
    }


@pytest.mark.parametrize(
    "query", ["OCC-DEV-MGR", "occ-dev-mgr", "מנהל/ת פיתוח", "מנהלת פיתוח", " מנהל   פיתוח "]
)
def test_exact_id_or_name_resolves(query: str) -> None:
    result = resolve(OCCUPATIONS, query)
    assert result.status is ResolveStatus.RESOLVED
    assert [c.occupation_id for c in result.candidates] == ["OCC-DEV-MGR"]


def test_partial_wording_is_a_choice_and_unknown_is_not_found() -> None:
    managers = resolve(OCCUPATIONS, "מנהל")
    assert managers.status is ResolveStatus.AMBIGUOUS
    assert {c.occupation_id for c in managers.candidates} == {"OCC-DEV-MGR", "OCC-PRODUCT-MGR"}
    single = resolve(OCCUPATIONS, "אנליסטית")
    assert single.status is ResolveStatus.AMBIGUOUS and len(single.candidates) == 1
    assert resolve(OCCUPATIONS, "פיתוח תוכנה").status is ResolveStatus.AMBIGUOUS
    assert resolve(OCCUPATIONS, "מתכנת").status is ResolveStatus.NOT_FOUND  # no synonyms
    assert resolve(OCCUPATIONS, "OCC-INVENTED").status is ResolveStatus.NOT_FOUND
    everything = resolve(OCCUPATIONS, None)
    assert everything.status is ResolveStatus.AMBIGUOUS and len(everything.candidates) == 4


CREATED = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def field(
    value: object, updated: datetime | None, confirmed: datetime | None = None
) -> FieldValue[Any]:
    return FieldValue(
        value=value,
        status=FieldStatus.USABLE,
        source=SourceType.EMPLOYEE,
        updated_at=updated,
        revision=3,
        last_confirmed_at=confirmed,
    )


def sample_profile() -> EmployeeProfile:
    earlier = CREATED - timedelta(days=40)
    return EmployeeProfile(
        employee_id=uuid4(),
        version=4,
        salary=SalaryFacts(
            base_gross=field(Decimal("18500"), earlier),
            commissions_gross=field(Decimal("0"), CREATED + timedelta(minutes=1)),
            bonus_gross=field(Decimal("0"), earlier, CREATED + timedelta(minutes=2)),
            credit_points=FieldValue(value=Decimal("2"), status=FieldStatus.NEEDS_CLARIFICATION),
        ),
        entitlement_group=field(EntitlementGroup.BET, None),
    )


def test_fields_requiring_confirmation_are_case_relative() -> None:
    required = fields_requiring_confirmation(sample_profile(), CREATED)
    assert required == [FieldPath.BASE_GROSS, FieldPath.ENTITLEMENT_GROUP]
    # stated in this conversation (updated or confirmed after creation) -> not required;
    # missing/unclear facts are asked when needed, not "confirmed"; no time-based expiry
    later_case = CREATED - timedelta(days=400)
    assert fields_requiring_confirmation(sample_profile(), later_case) == [
        FieldPath.ENTITLEMENT_GROUP  # never stated by the employee (no timestamps)
    ]


def test_confirm_changes_metadata_only() -> None:
    profile = sample_profile()
    now = CREATED + timedelta(hours=1)
    message = uuid4()
    confirmed, not_confirmable, updated = confirm_fields(
        profile,
        [FactPath.BASE_GROSS, FactPath.CREDIT_POINTS, FactPath.STUDY_FUND_EMPLOYEE_RATE],
        now=now,
        source_message_id=message,
    )
    assert confirmed == [FactPath.BASE_GROSS]
    assert [(i.code, i.field) for i in not_confirmable] == [
        (IssueCode.AMBIGUOUS_INPUT, FieldPath.CREDIT_POINTS),
        (IssueCode.MISSING_INPUT, FieldPath.STUDY_FUND_EMPLOYEE_RATE),
    ]
    before, after = profile.salary.base_gross, updated.salary.base_gross
    assert after.value == before.value and after.updated_at == before.updated_at
    assert after.last_confirmed_at == now and after.source_message_id == message
    assert after.revision == before.revision + 1
    assert updated.salary.credit_points == profile.salary.credit_points
    assert updated.version == profile.version  # the repository bumps the version


def test_outcomes_and_error_envelopes_carry_issues() -> None:
    issue = Issue(code=IssueCode.VEHICLE_NOT_FOUND)
    envelope = Outcome("data", status=EnvelopeStatus.PARTIAL, issues=(issue,)).envelope("t")
    assert envelope.status is EnvelopeStatus.PARTIAL and envelope.issues == [issue]
    with pytest.raises(ValueError):
        Outcome("data", status=EnvelopeStatus.ERROR)
    error = error_envelope(NotFoundError(issues=[issue]), "t")
    assert error.error is not None and error.issues == [issue] and error.data is None
    assert error_envelope(NotFoundError(), "t").issues == []
