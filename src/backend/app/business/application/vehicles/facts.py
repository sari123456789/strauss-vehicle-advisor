"""Effective eligibility facts of a case and missing-fact issues (pure; no I/O).

Effective = the latest shared actual profile with this case's hypothetical overrides on top
(decisions A4/A5). Overrides stay case-local: they are read here, never written anywhere.
"""

from collections.abc import Iterable
from typing import Any

from app.business.domain.cases import FactPath, HypotheticalOverride, profile_fact
from app.business.domain.eligibility import EligibilityFacts
from app.business.domain.employee_profiles import EmployeeProfile
from app.business.domain.provenance import FieldValue, SourceType
from app.business.domain.results import MissingFact, MissingReason
from app.contracts.common import FieldPath, Issue, IssueCode


def _effective(
    profile: EmployeeProfile, overrides: tuple[HypotheticalOverride, ...], path: FactPath
) -> FieldValue[Any]:
    for override in overrides:
        if override.path is path:
            return FieldValue.usable(override.value, SourceType.EMPLOYEE)
    return profile_fact(profile, path)


def eligibility_facts(
    profile: EmployeeProfile, overrides: tuple[HypotheticalOverride, ...] = ()
) -> EligibilityFacts:
    return EligibilityFacts(
        entitlement_group=_effective(profile, overrides, FactPath.ENTITLEMENT_GROUP),
        qualifying_children_count=_effective(
            profile, overrides, FactPath.QUALIFYING_CHILDREN_COUNT
        ),
        occupation_id=_effective(profile, overrides, FactPath.OCCUPATION_ID),
    )


FIELD_PATHS = frozenset(p.value for p in FieldPath)


def missing_issues(missing: Iterable[MissingFact], *, per_option: bool = False) -> list[Issue]:
    """One issue per (field, reason) - the same missing group is reported once, not per car.

    With `per_option`, issues keep their option key (one per option). A component path that is
    not an employee field (e.g. `alternative.participation`) is carried in `reason`.
    """
    issues: list[Issue] = []
    seen: set[tuple[str, MissingReason, str | None]] = set()
    for fact in missing:
        option_key = fact.option_key if per_option else None
        if (fact.field, fact.reason, option_key) in seen:
            continue
        seen.add((fact.field, fact.reason, option_key))
        code = (
            IssueCode.AMBIGUOUS_INPUT
            if fact.reason is MissingReason.NEEDS_CLARIFICATION
            else IssueCode.MISSING_INPUT
        )
        if fact.field in FIELD_PATHS:
            issues.append(
                Issue(
                    code=code,
                    field=FieldPath(fact.field),
                    option_key=option_key,
                    reason=fact.reason.value,
                )
            )
        else:
            issues.append(
                Issue(code=code, option_key=option_key, reason=f"{fact.field}:{fact.reason.value}")
            )
    return issues
