"""How applied representative answers enter a case's calculations (B3a, decision Q1).

Precedence of a fact inside one case (approved):
1. an explicit hypothetical override - only where overrides apply (hypothetical runs and the
   listing basis);
2. an applicable representative fact of this case (`case_representative_facts`);
3. the employee-reported actual value (latest profile, or the case copy for hypothetical runs).

A representative fact applies only while its effective period covers the case's reference
date; an expired or future fact is ignored, so it never hides another value. The shared
EmployeeProfile is never changed, and regular synchronization (which replaces only the case's
actual copy) never removes a representative fact.

Source conflict (precise definition): an applicable representative fact exists for a field,
the employee-reported value of that field in the same fact basis is usable, the two differ,
and no hypothetical override of that field is applied. It is reported as `SOURCE_CONFLICT`
with reason `representative_fact_differs`; it is informational - readiness is unchanged and
calculations proceed with the representative value. An intentional hypothetical override is
never a conflict.

A representative participation is stored in its alternative's `inputs` and replaces the
catalogue default for that option while its period covers the reference date. A request
answered `cannot_determine` blocks only its own option, and only while the option's
alternative is unchanged since the question (a later applied answer or a new alternative
lifts the block).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.business.application.ports.unit_of_work import BusinessUnitOfWork
from app.business.domain.cases import (
    Alternative,
    ComparisonCase,
    FactPath,
    profile_fact,
    with_profile_fact,
)
from app.business.domain.employee_profiles import EmployeeProfile
from app.business.domain.json_values import JsonObject
from app.business.domain.provenance import FieldStatus, FieldValue, SourceType
from app.business.domain.representatives import (
    RepresentativeFact,
    RepresentativeParticipation,
    RepresentativeRequest,
    RequestStatus,
    applicable_facts,
)
from app.contracts.common import FieldPath, Issue, IssueCode

PARTICIPATION_INPUT = "participation"
REPRESENTATIVE_SOURCE = "representative"
FACT_DIFFERS = "representative_fact_differs"
CANNOT_DETERMINE = "representative_cannot_determine"


@dataclass(frozen=True, slots=True)
class CaseAnswers:
    """The applied answers of one case that are valid on its reference date."""

    facts: Mapping[FactPath, RepresentativeFact] = field(default_factory=dict)
    participation: Mapping[str, RepresentativeParticipation] = field(default_factory=dict)
    """By option key, for active alternatives only."""
    cannot_determine: tuple[RepresentativeRequest, ...] = ()
    """Requests that still block their option (see module docstring)."""


def participation_inputs(participation: RepresentativeParticipation) -> dict[str, Any]:
    """The alternative `inputs` entry (plain JSON, decimal string, provenance)."""
    return {
        "value": format(participation.amount, "f"),
        "unit": "ILS",
        "source": REPRESENTATIVE_SOURCE,
        "requestId": str(participation.request_id),
        "answeredAt": participation.answered_at.isoformat(),
        "effectiveFrom": None
        if participation.effective_from is None
        else participation.effective_from.isoformat(),
        "effectiveTo": None
        if participation.effective_to is None
        else participation.effective_to.isoformat(),
    }


def read_participation(inputs: JsonObject) -> RepresentativeParticipation | None:
    entry = inputs.get(PARTICIPATION_INPUT)
    if not isinstance(entry, Mapping) or entry.get("source") != REPRESENTATIVE_SOURCE:
        return None

    def day(key: str) -> date | None:
        value = entry.get(key)
        return None if value is None else date.fromisoformat(str(value))

    return RepresentativeParticipation(
        amount=Decimal(str(entry["value"])),
        request_id=UUID(str(entry["requestId"])),
        answered_at=datetime.fromisoformat(str(entry["answeredAt"])),
        effective_from=day("effectiveFrom"),
        effective_to=day("effectiveTo"),
    )


def _participation(
    alternatives: Sequence[Alternative], case: ComparisonCase
) -> dict[str, RepresentativeParticipation]:
    result: dict[str, RepresentativeParticipation] = {}
    for alternative in alternatives:
        stored = read_participation(alternative.inputs)
        if stored is not None and stored.applies_on(case.reference_date):
            result[alternative.option_key] = stored
    return result


def _still_blocking(
    requests: Sequence[RepresentativeRequest], alternatives: Sequence[Alternative]
) -> tuple[RepresentativeRequest, ...]:
    active = {(a.alternative_id, a.version) for a in alternatives}
    return tuple(
        r
        for r in requests
        if r.option_key is not None
        and r.alternative_id is not None
        and (r.alternative_id, r.alternative_version) in active
    )


async def load_case_answers(uow: BusinessUnitOfWork, case: ComparisonCase) -> CaseAnswers:
    """Read inside the caller's (already authorized) Unit of Work."""
    facts = await uow.case_facts.list_for_case(case.case_id)
    alternatives = await uow.alternatives.list_for_case(case.case_id)
    unanswerable = await uow.representative_requests.list_for_case(
        case.case_id, [RequestStatus.CANNOT_DETERMINE]
    )
    return CaseAnswers(
        facts=applicable_facts(facts, case.reference_date),
        participation=_participation(alternatives, case),
        cannot_determine=_still_blocking(unanswerable, alternatives),
    )


def with_representative_facts(
    profile: EmployeeProfile, facts: Mapping[FactPath, RepresentativeFact]
) -> EmployeeProfile:
    """A calculation-local profile value; nothing stored is changed."""
    for path, fact in facts.items():
        current = profile_fact(profile, path)
        supplied: FieldValue[object] = FieldValue(
            value=fact.value,
            status=FieldStatus.USABLE,
            source=SourceType.REPRESENTATIVE,
            updated_at=fact.answered_at,
            revision=current.revision,
        )
        profile = with_profile_fact(profile, path, supplied)
    return profile


def representative_conflicts(
    reported: EmployeeProfile,
    facts: Mapping[FactPath, RepresentativeFact],
    *,
    overridden: frozenset[FactPath] = frozenset(),
) -> list[Issue]:
    """`SOURCE_CONFLICT` per field where the employee-reported value differs (see module)."""
    issues: list[Issue] = []
    for path, fact in facts.items():
        if path in overridden:
            continue
        employee = profile_fact(reported, path)
        if employee.status is FieldStatus.USABLE and employee.value != fact.value:
            issues.append(
                Issue(
                    code=IssueCode.SOURCE_CONFLICT,
                    field=FieldPath(path.value),
                    request_id=fact.request_id,
                    reason=FACT_DIFFERS,
                )
            )
    return issues


def cannot_determine_issue(option_key: str, request: RepresentativeRequest) -> Issue:
    return Issue(
        code=IssueCode.MISSING_INPUT,
        option_key=option_key,
        request_id=request.request_id,
        reason=CANNOT_DETERMINE,
    )
