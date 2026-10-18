"""Employee confirmation of saved actual facts (G3) and the case-relative confirmation hint
(G4). Pure; no I/O.

G4 rule: a usable fact *requires confirmation* in a case when neither its value
(`updatedAt`) nor an employee confirmation (`lastConfirmedAt`) happened since the case was
created - i.e. the employee has not stated it in this conversation. It is informational
only: the agent asks only about the facts the current goal needs (listing vehicles never
needs salary confirmation), there is no blanket questionnaire, and there is no time-based
expiry. It is unrelated to stale business data (a newer profile/release than a run used)
and to stale comparison runs.
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from app.business.domain.cases import FactPath, profile_fact, with_profile_fact
from app.business.domain.employee_profiles import EmployeeProfile
from app.business.domain.provenance import FieldStatus, SourceType
from app.contracts.common import FieldPath, Issue, IssueCode


def confirm_fields(
    profile: EmployeeProfile,
    paths: Sequence[FactPath],
    *,
    now: datetime,
    source_message_id: UUID | None,
) -> tuple[list[FactPath], list[Issue], EmployeeProfile]:
    """Return (confirmed paths, not-confirmable issues, updated profile)."""
    confirmed: list[FactPath] = []
    not_confirmable: list[Issue] = []
    for path in paths:
        field = profile_fact(profile, path)
        if field.status is not FieldStatus.USABLE:
            code = (
                IssueCode.AMBIGUOUS_INPUT
                if field.status is FieldStatus.NEEDS_CLARIFICATION
                else IssueCode.MISSING_INPUT
            )
            not_confirmable.append(
                Issue(code=code, field=FieldPath(path.value), reason=field.status.value)
            )
            continue
        confirmed_field = replace(
            field,
            source=SourceType.EMPLOYEE,
            source_message_id=source_message_id,
            last_confirmed_at=now,
            revision=field.revision + 1,
        )
        profile = with_profile_fact(profile, path, confirmed_field)
        confirmed.append(path)
    return confirmed, not_confirmable, profile


def fields_requiring_confirmation(
    profile: EmployeeProfile, case_created_at: datetime
) -> list[FieldPath]:
    required: list[FieldPath] = []
    for path in FactPath:
        field = profile_fact(profile, path)
        if field.status is not FieldStatus.USABLE:
            continue
        stated = [t for t in (field.updated_at, field.last_confirmed_at) if t is not None]
        if not any(t >= case_created_at for t in stated):
            required.append(FieldPath(path.value))
    return required
