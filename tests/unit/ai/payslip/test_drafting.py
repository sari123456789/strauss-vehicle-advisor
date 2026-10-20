"""Draft fields from deterministic candidates and the (scripted) AI interpretation."""

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.ai.application.payslip.candidates import build_candidates
from app.ai.application.payslip.drafting import build_fields
from app.ai.application.payslip.extraction import (
    AiFieldMapping,
    PayslipInterpretation,
    interpretation_task,
)
from app.ai.application.payslip.models import (
    Candidate,
    DocumentText,
    DraftField,
    FieldIssue,
    FieldStatus,
)
from app.ai.application.payslip.ports import PayslipImportConfig
from app.ai.application.ports.llm import LlmTaskKind
from app.ai.infrastructure.payslip.pdf_reader import PypdfTextReader
from app.contracts.common import FieldPath
from tests.fakes.payslip import fixture

F = FieldPath


def _candidates(name: str) -> tuple[Candidate, ...]:
    return build_candidates(PypdfTextReader().read(fixture(name), PayslipImportConfig()))


def _lines(*lines: str) -> tuple[Candidate, ...]:
    return build_candidates(DocumentText(pages=(tuple(lines),), byte_size=0))


def _by_field(fields: tuple[DraftField, ...]) -> dict[FieldPath, DraftField]:
    return {f.field: f for f in fields}


def _cid(candidates: tuple[Candidate, ...], text: str) -> str:
    return next(c.candidate_id for c in candidates if text in c.evidence)


def _map(field: FieldPath, cid: str, **overrides: Any) -> AiFieldMapping:
    values: dict[str, Any] = {
        "field": field.value,
        "candidate_id": cid,
        "value_kind": "percent"
        if field in {F.PENSION_EMPLOYEE_RATE, F.STUDY_FUND_EMPLOYEE_RATE}
        else "amount",
        "value_index": 0,
        "party": "employee",
        "period": "monthly",
        "confidence": "high",
    }
    values.update(overrides)
    return AiFieldMapping.model_validate(values)


def _basic_interpretation(candidates: tuple[Candidate, ...]) -> PayslipInterpretation:
    return PayslipInterpretation(
        mappings=[
            _map(F.BASE_GROSS, _cid(candidates, "18,500.00")),
            _map(F.COMMISSIONS_GROSS, _cid(candidates, "1,200.00")),
            _map(F.BONUS_GROSS, _cid(candidates, "2,000.00")),
            _map(F.PENSION_EMPLOYEE_RATE, _cid(candidates, "1,110.00")),
            _map(F.STUDY_FUND_EMPLOYEE_RATE, _cid(candidates, "462.50")),
            _map(F.CREDIT_POINTS, _cid(candidates, "2.25")),
        ]
    )


def test_rules_and_ai_agreement_extracts_all_fields_with_evidence() -> None:
    candidates = _candidates("basic_he.pdf")
    fields = _by_field(build_fields(candidates, _basic_interpretation(candidates)))
    expected = {
        F.BASE_GROSS: Decimal("18500.00"),
        F.COMMISSIONS_GROSS: Decimal("1200.00"),
        F.BONUS_GROSS: Decimal("2000.00"),
        F.PENSION_EMPLOYEE_RATE: Decimal("0.06"),
        F.STUDY_FUND_EMPLOYEE_RATE: Decimal("0.025"),
        F.CREDIT_POINTS: Decimal("2.25"),
    }
    for path, value in expected.items():
        assert fields[path].status is FieldStatus.EXTRACTED, path
        assert fields[path].value == value
        (evidence,) = fields[path].evidence
        assert evidence.page == 1 and evidence.line_index >= 0
    assert "18,500.00" in fields[F.BASE_GROSS].evidence[0].text


def test_rules_only_proposes_values_for_review_never_as_extracted() -> None:
    fields = build_fields(_candidates("basic_he.pdf"), None)
    assert {f.status for f in fields} == {FieldStatus.NEEDS_REVIEW}
    assert all(FieldIssue.AI_UNAVAILABLE in f.issues for f in fields)


def test_employer_cumulative_net_and_tax_lines_are_never_used() -> None:
    candidates = _candidates("basic_he.pdf")
    # The AI points at the employer pension line, the cumulative base and the net line.
    bad = PayslipInterpretation(
        mappings=[
            _map(F.PENSION_EMPLOYEE_RATE, _cid(candidates, "1,202.50"), party="employee"),
            _map(F.BASE_GROSS, _cid(candidates, "185,000.00"), period="monthly"),
            _map(F.BONUS_GROSS, _cid(candidates, "16,431.50")),
        ]
    )
    fields = _by_field(build_fields(candidates, bad))
    assert fields[F.PENSION_EMPLOYEE_RATE].value == Decimal("0.06")  # employee line only
    assert fields[F.BASE_GROSS].value == Decimal("18500.00")
    assert fields[F.BONUS_GROSS].value == Decimal("2000.00")
    for path in (F.PENSION_EMPLOYEE_RATE, F.BASE_GROSS, F.BONUS_GROSS):
        assert fields[path].status is FieldStatus.NEEDS_REVIEW
        assert FieldIssue.NOT_CONFIRMED_BY_AI in fields[path].issues


def test_only_employer_or_cumulative_evidence_is_rejected() -> None:
    fields = _by_field(
        build_fields(
            _lines("1,202.50 6.50% מעסיק הפרשת - פנסיה", "185,000.00 מצטבר יסוד שכר"), None
        )
    )
    assert fields[F.PENSION_EMPLOYEE_RATE].status is FieldStatus.REJECTED
    assert fields[F.PENSION_EMPLOYEE_RATE].issues == (FieldIssue.EMPLOYER_SHARE_ONLY,)
    assert fields[F.BASE_GROSS].status is FieldStatus.REJECTED
    assert fields[F.BASE_GROSS].issues == (FieldIssue.CUMULATIVE_ONLY,)
    assert fields[F.BASE_GROSS].value is None


def test_ambiguous_payslip_surfaces_conflicts_and_missing_values() -> None:
    fields = _by_field(build_fields(_candidates("ambiguous.pdf"), None))
    base = fields[F.BASE_GROSS]
    assert base.status is FieldStatus.CONFLICT and base.value is None
    assert set(base.alternatives) == {Decimal("15000.00"), Decimal("82.42"), Decimal("182.00")}
    bonus = fields[F.BONUS_GROSS]
    assert bonus.status is FieldStatus.CONFLICT and bonus.value is None  # never summed
    assert set(bonus.alternatives) == {Decimal("1000.00"), Decimal("2500.00")}
    assert fields[F.PENSION_EMPLOYEE_RATE].status is FieldStatus.CONFLICT
    study = fields[F.STUDY_FUND_EMPLOYEE_RATE]
    assert study.status is FieldStatus.REJECTED  # amount only: the rate is never derived
    assert study.issues == (FieldIssue.RATE_NOT_PRINTED,)
    assert fields[F.CREDIT_POINTS].status is FieldStatus.MISSING
    assert fields[F.COMMISSIONS_GROSS].status is FieldStatus.MISSING


def test_ai_may_resolve_a_multi_number_line_only_to_a_printed_number_for_review() -> None:
    candidates = _candidates("ambiguous.pdf")
    base_line = _cid(candidates, "15,000.00")
    index = next(c for c in candidates if c.candidate_id == base_line).amounts.index(
        Decimal("15000.00")
    )
    fields = _by_field(
        build_fields(
            candidates,
            PayslipInterpretation(mappings=[_map(F.BASE_GROSS, base_line, value_index=index)]),
        )
    )
    assert fields[F.BASE_GROSS].status is FieldStatus.NEEDS_REVIEW
    assert fields[F.BASE_GROSS].value == Decimal("15000.00")


def test_hallucinated_references_are_ignored() -> None:
    candidates = _candidates("basic_he.pdf")
    base = _cid(candidates, "18,500.00")
    fields = _by_field(
        build_fields(
            candidates,
            PayslipInterpretation(
                mappings=[
                    _map(F.COMMISSIONS_GROSS, "c999"),  # no such line
                    _map(F.BONUS_GROSS, base, value_index=7),  # no such number
                    _map(F.PENSION_EMPLOYEE_RATE, base),  # percent on a line without one
                ]
            ),
        )
    )
    assert fields[F.COMMISSIONS_GROSS].value == Decimal("1200.00")
    assert fields[F.BONUS_GROSS].value == Decimal("2000.00")
    assert fields[F.PENSION_EMPLOYEE_RATE].value == Decimal("0.06")


def test_ai_output_schema_cannot_carry_values_or_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        AiFieldMapping.model_validate({**_map(F.BASE_GROSS, "c1").model_dump(), "value": "99999"})
    with pytest.raises(ValidationError):
        AiFieldMapping.model_validate(
            {**_map(F.BASE_GROSS, "c1").model_dump(), "field": "payroll.netPay"}
        )


def test_ai_only_mapping_needs_review() -> None:
    candidates = _lines("4,000.00 חודשי תשלום")
    fields = _by_field(
        build_fields(
            candidates,
            PayslipInterpretation(mappings=[_map(F.BASE_GROSS, candidates[0].candidate_id)]),
        )
    )
    assert fields[F.BASE_GROSS].status is FieldStatus.NEEDS_REVIEW
    assert FieldIssue.AI_ONLY in fields[F.BASE_GROSS].issues


def test_injected_lines_are_never_candidates_for_values() -> None:
    candidates = _candidates("injection.pdf")
    suspicious = [c for c in candidates if c.suspicious]
    assert len(suspicious) == 2
    injected = PayslipInterpretation(
        mappings=[
            _map(F.BASE_GROSS, suspicious[0].candidate_id),
            _map(F.BONUS_GROSS, suspicious[1].candidate_id),
        ]
    )
    fields = _by_field(build_fields(candidates, injected))
    assert fields[F.BASE_GROSS].value == Decimal("12000.00")
    assert fields[F.BONUS_GROSS].status is FieldStatus.MISSING
    assert Decimal("99999.00") not in fields[F.BASE_GROSS].alternatives


def test_negative_and_out_of_contract_values_are_rejected() -> None:
    fields = _by_field(build_fields(_lines("2,000.00- בונוס", "150% עובד פנסיה"), None))
    assert fields[F.BONUS_GROSS].status is FieldStatus.REJECTED
    assert FieldIssue.NEGATIVE_AMOUNT in fields[F.BONUS_GROSS].issues
    assert fields[F.PENSION_EMPLOYEE_RATE].status is FieldStatus.REJECTED
    assert FieldIssue.INVALID_VALUE in fields[F.PENSION_EMPLOYEE_RATE].issues


def test_interpretation_task_wraps_redacted_candidates_as_untrusted_data() -> None:
    candidates = _candidates("basic_he.pdf")
    task = interpretation_task(candidates)
    assert task.kind is LlmTaskKind.INTERPRET
    assert task.output_type is PayslipInterpretation
    prompt = task.messages[0].text
    assert prompt.startswith("<document>") and prompt.endswith("</document>")
    assert "untrusted data" in task.system_instruction
    for secret in ("000000018", "1234567", "ישראלי", "סניף"):
        assert secret not in prompt
