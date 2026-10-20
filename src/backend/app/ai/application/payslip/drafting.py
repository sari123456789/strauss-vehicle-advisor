"""Combine deterministic label rules and the (optional) AI interpretation into draft fields,
and validate every value against the authoritative Business contracts.

Rules always win over the AI: a line the rules classify as employer share, cumulative, net/
total/tax, or suspicious is never used, whatever the model says. A field is `extracted` only
when the rules and the AI independently agree on one value from an unambiguous line;
everything else is `needs_review`, `conflict`, `rejected` or `missing`. Values are never
summed, derived (e.g. amount / salary -> rate) or invented.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from pydantic import ValidationError

from app.ai.application.payslip.extraction import AiFieldMapping, PayslipInterpretation
from app.ai.application.payslip.models import (
    PAYSLIP_FIELDS,
    RATE_FIELDS,
    Candidate,
    Confidence,
    DraftField,
    Evidence,
    FieldIssue,
    FieldStatus,
    Party,
    Period,
)
from app.contracts.case_inputs import SetChange
from app.contracts.common import FieldPath

_HUNDRED = Decimal(100)


def contract_value(field: FieldPath, value: Decimal) -> str:
    """The decimal string the Business contract expects for `field` (no float involved)."""
    return format(value, "f")


def is_valid_value(field: FieldPath, value: Decimal) -> bool:
    """Validate with the authoritative contract model (`SetChange` value adapters)."""
    try:
        SetChange(op="set", field=field, value=contract_value(field, value))
    except ValidationError:
        return False
    return True


def _to_profile_units(field: FieldPath, printed: Decimal) -> Decimal:
    """Printed percentages become contract fractions ("6.00%" -> 0.0600). This is a unit
    conversion of a printed rate, not a calculation from other amounts."""
    return printed / _HUNDRED if field in RATE_FIELDS else printed


def _printed_values(field: FieldPath, candidate: Candidate) -> tuple[Decimal, ...]:
    return candidate.percents if field in RATE_FIELDS else candidate.amounts


@dataclass(frozen=True, slots=True)
class _Proposal:
    candidate: Candidate
    values: tuple[Decimal, ...]
    """Profile-unit values printed on the line for this field."""


def _evidence(candidate: Candidate) -> Evidence:
    return Evidence(
        candidate.candidate_id, candidate.page, candidate.line_index, candidate.evidence
    )


def _non_applicable_issue(field: FieldPath, candidate: Candidate) -> FieldIssue | None:
    if candidate.party is Party.EMPLOYER:
        return FieldIssue.EMPLOYER_SHARE_ONLY
    if candidate.period is Period.CUMULATIVE:
        return FieldIssue.CUMULATIVE_ONLY
    if not _printed_values(field, candidate):
        return FieldIssue.RATE_NOT_PRINTED if field in RATE_FIELDS else FieldIssue.INVALID_VALUE
    return None


def _ai_value(
    mapping: AiFieldMapping, field: FieldPath, by_id: dict[str, Candidate]
) -> tuple[Candidate, Decimal] | None:
    candidate = by_id.get(mapping.candidate_id)
    if candidate is None or candidate.excluded or candidate.suspicious:
        return None
    expected_kind = "percent" if field in RATE_FIELDS else "amount"
    if mapping.value_kind != expected_kind:
        return None
    if mapping.party == "employer" or mapping.period == "cumulative":
        return None
    if _non_applicable_issue(field, candidate) is not None:
        return None
    printed = _printed_values(field, candidate)
    if mapping.value_index >= len(printed):
        return None
    return candidate, _to_profile_units(field, printed[mapping.value_index])


def build_field(
    field: FieldPath,
    candidates: Sequence[Candidate],
    interpretation: PayslipInterpretation | None,
) -> DraftField:
    by_id = {c.candidate_id: c for c in candidates}
    applicable: list[_Proposal] = []
    blocked: list[tuple[Candidate, FieldIssue]] = []
    for candidate in candidates:
        if field not in candidate.keyword_fields or candidate.excluded or candidate.suspicious:
            continue
        issue = _non_applicable_issue(field, candidate)
        if issue is not None:
            blocked.append((candidate, issue))
            continue
        values = tuple(_to_profile_units(field, v) for v in _printed_values(field, candidate))
        applicable.append(_Proposal(candidate, values))

    ai_hits: list[tuple[Candidate, Decimal, AiFieldMapping]] = []
    if interpretation is not None:
        for mapping in interpretation.mappings:
            if mapping.field != field.value:
                continue
            hit = _ai_value(mapping, field, by_id)
            if hit is not None:
                ai_hits.append((hit[0], hit[1], mapping))

    if not applicable and not ai_hits:
        if blocked:
            return DraftField(
                field=field,
                status=FieldStatus.REJECTED,
                evidence=tuple(_evidence(c) for c, _ in blocked),
                issues=tuple(dict.fromkeys(issue for _, issue in blocked)),
            )
        return DraftField(field=field, status=FieldStatus.MISSING)

    issues: list[FieldIssue] = []
    evidence: dict[str, Evidence] = {}
    rule_values: list[Decimal] = []
    ambiguous_lines: list[_Proposal] = []
    for proposal in applicable:
        evidence[proposal.candidate.candidate_id] = _evidence(proposal.candidate)
        if len(proposal.values) == 1:
            rule_values.append(proposal.values[0])
        else:
            ambiguous_lines.append(proposal)
            issues.append(FieldIssue.MULTIPLE_AMOUNTS_ON_LINE)
    for candidate, _, _ in ai_hits:
        evidence.setdefault(candidate.candidate_id, _evidence(candidate))

    ai_values = [value for _, value, _ in ai_hits]
    # An AI pick may resolve a line with several numbers, but only to one of those numbers.
    resolved = [v for v in ai_values if any(v in p.values for p in ambiguous_lines)]
    proposed = list(dict.fromkeys([*rule_values, *ai_values]))
    if ambiguous_lines and not resolved:
        alternatives = [v for p in ambiguous_lines for v in p.values]
        return DraftField(
            field=field,
            status=FieldStatus.CONFLICT,
            evidence=tuple(evidence.values()),
            alternatives=tuple(dict.fromkeys([*proposed, *alternatives])),
            issues=tuple(dict.fromkeys(issues)),
        )
    if len(proposed) > 1:
        return DraftField(
            field=field,
            status=FieldStatus.CONFLICT,
            evidence=tuple(evidence.values()),
            alternatives=tuple(proposed),
            issues=tuple(dict.fromkeys([*issues, FieldIssue.MULTIPLE_VALUES])),
        )

    value = proposed[0]
    if value < 0:
        return DraftField(
            field=field,
            status=FieldStatus.REJECTED,
            evidence=tuple(evidence.values()),
            alternatives=(value,),
            issues=tuple(dict.fromkeys([*issues, FieldIssue.NEGATIVE_AMOUNT])),
        )
    if not is_valid_value(field, value):
        return DraftField(
            field=field,
            status=FieldStatus.REJECTED,
            evidence=tuple(evidence.values()),
            alternatives=(value,),
            issues=tuple(dict.fromkeys([*issues, FieldIssue.INVALID_VALUE])),
        )

    rule_supported = value in rule_values
    ai_supported = any(v == value and m.confidence != "low" for _, v, m in ai_hits)
    if interpretation is None:
        issues.append(FieldIssue.AI_UNAVAILABLE)
    elif not ai_hits:
        issues.append(FieldIssue.NOT_CONFIRMED_BY_AI)
    elif not rule_supported:
        issues.append(FieldIssue.AI_ONLY)
    party_known = all(
        p.candidate.party is Party.EMPLOYEE for p in applicable if field in RATE_FIELDS
    )
    if rule_supported and ai_supported and not ambiguous_lines and party_known and not issues:
        return DraftField(
            field=field,
            status=FieldStatus.EXTRACTED,
            value=value,
            confidence=Confidence.HIGH,
            evidence=tuple(evidence.values()),
        )
    return DraftField(
        field=field,
        status=FieldStatus.NEEDS_REVIEW,
        value=value,
        confidence=Confidence.MEDIUM if rule_supported else Confidence.LOW,
        evidence=tuple(evidence.values()),
        issues=tuple(dict.fromkeys(issues)),
    )


def build_fields(
    candidates: Sequence[Candidate], interpretation: PayslipInterpretation | None
) -> tuple[DraftField, ...]:
    return tuple(build_field(f, candidates, interpretation) for f in PAYSLIP_FIELDS)
