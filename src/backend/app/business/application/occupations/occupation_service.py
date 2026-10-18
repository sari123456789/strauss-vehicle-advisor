"""`resolve_occupation` (G2): map an employee's wording to an official occupation ID.

Deterministic, over the occupation table of the pinned release only:
- `resolved` only for an exact official ID (`OCC-...`, surrounding whitespace and letter case
  ignored) or an exact normalized official name. A name such as "מפתח/ת תוכנה" also matches
  exactly in either written form ("מפתח תוכנה", "מפתחת תוכנה"); that is a reading of the
  official name, not a synonym list.
- Otherwise every official occupation whose name contains the query, or shares a whole word
  with it, is returned as `ambiguous` for the employee to choose (even a single one).
- No query: every official occupation, `ambiguous` (the employee chooses).
- No match: `not_found`. IDs are never invented and there are no synonyms or similar-role
  guesses (T23); the result is never written to the profile by this operation.
"""

import re
import unicodedata

from app.business.application.errors import translate_errors
from app.business.application.identity.authorization import AuthorizationPolicy
from app.business.application.outcomes import Outcome
from app.business.application.ports.reference_data import ReferenceDataRepository
from app.business.application.ports.unit_of_work import BusinessUnitOfWorkFactory
from app.business.application.reference import pin_reference
from app.business.application.vehicles.mapping import occupation_candidate
from app.business.domain.occupations import OccupationPolicy
from app.contracts.catalogue import (
    ResolveOccupationRequest,
    ResolveOccupationResult,
    ResolveStatus,
)
from app.contracts.common import EnvelopeStatus, FieldPath, Issue, IssueCode
from app.contracts.tool_context import ToolContext

MAX_CANDIDATES = 20
_SPACES = re.compile(r"\s+")
_GENDERED = re.compile(r"(\S+)/(\S+)")
"""A written-form alternation inside one word, e.g. "מפתח/ת" -> "מפתח" | "מפתחת"."""


def normalize(text: str) -> str:
    return _SPACES.sub(" ", unicodedata.normalize("NFC", text)).strip().casefold()


def name_forms(name_he: str) -> frozenset[str]:
    """The official name and its two written forms (no other variants)."""
    official = normalize(name_he)
    first = _GENDERED.sub(lambda m: m.group(1), official)
    second = _GENDERED.sub(lambda m: m.group(1) + m.group(2), official)
    return frozenset({official, first, second})


def _words(forms: frozenset[str]) -> frozenset[str]:
    return frozenset(word for form in forms for word in form.split(" ") if len(word) > 1)


def resolve(
    occupations: tuple[OccupationPolicy, ...], query: str | None
) -> ResolveOccupationResult:
    if query is None or not query.strip():
        return _ambiguous(occupations)
    by_id = query.strip().upper()
    for occupation in occupations:
        if occupation.occupation_id == by_id:
            return _resolved(occupation)
    wanted = normalize(query)
    exact = [o for o in occupations if wanted in name_forms(o.name_he)]
    if len(exact) == 1:
        return _resolved(exact[0])
    query_words = set(wanted.split(" "))
    partial = [
        o
        for o in occupations
        if any(wanted in form for form in name_forms(o.name_he))
        or query_words & _words(name_forms(o.name_he))
    ]
    if not partial:
        return ResolveOccupationResult(status=ResolveStatus.NOT_FOUND)
    return _ambiguous(tuple(partial))


def _resolved(occupation: OccupationPolicy) -> ResolveOccupationResult:
    return ResolveOccupationResult(
        status=ResolveStatus.RESOLVED, candidates=[occupation_candidate(occupation)]
    )


def _ambiguous(
    occupations: tuple[OccupationPolicy, ...] | list[OccupationPolicy],
) -> ResolveOccupationResult:
    return ResolveOccupationResult(
        status=ResolveStatus.AMBIGUOUS,
        candidates=[occupation_candidate(o) for o in list(occupations)[:MAX_CANDIDATES]],
    )


class OccupationService:
    def __init__(
        self,
        unit_of_work: BusinessUnitOfWorkFactory,
        policy: AuthorizationPolicy,
        reference_data: ReferenceDataRepository,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._policy = policy
        self._reference_data = reference_data

    async def resolve_occupation(
        self, ctx: ToolContext, request: ResolveOccupationRequest
    ) -> Outcome[ResolveOccupationResult]:
        with translate_errors():
            async with self._unit_of_work() as uow:
                _actor, case = await self._policy.tool_case(uow, ctx)
            pinned = await pin_reference(self._reference_data)
            occupations = await self._reference_data.list_occupations(
                pinned.release.catalogue_version_id
            )
        result = resolve(occupations, request.query)
        issues: tuple[Issue, ...] = ()
        status = EnvelopeStatus.OK
        if result.status is not ResolveStatus.RESOLVED:
            status = EnvelopeStatus.NEEDS_INFORMATION
            code = (
                IssueCode.AMBIGUOUS_INPUT
                if result.status is ResolveStatus.AMBIGUOUS
                else IssueCode.MISSING_INPUT
            )
            issues = (Issue(code=code, field=FieldPath.OCCUPATION_ID, reason=result.status.value),)
        return Outcome(
            result,
            status=status,
            issues=issues,
            case_version=case.version,
            source_versions=pinned.versions,
        )
