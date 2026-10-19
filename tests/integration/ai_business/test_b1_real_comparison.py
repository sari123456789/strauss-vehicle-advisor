"""Stage B1: the first real employee vehicle comparison, end to end through the real facade.

Reference data is imported from the verified `data/source` files (the import service checks
the manifest hashes itself) and the six workbook employees are seeded by the Business demo
seed, both into the marked integration database. Every amount comes from the Business module;
expected values are the authoritative ones: the workbook calculation sheet (displayed values)
and Agent Spec §8 (precise values, as used by the Business reference tests).
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import uuid4

import openpyxl

from app.contracts.case_inputs import ConfirmProfileFieldsRequest
from app.contracts.catalogue import (
    EligibilityRequest,
    EligibilityStatus,
    ListOptionsRequest,
)
from app.contracts.common import EnvelopeStatus, IssueCode, RunKind
from app.contracts.comparisons import (
    CompareRequest,
    ComparisonDetailsRequest,
    ScenarioStatus,
)
from app.contracts.readiness import (
    ReadinessGoal,
    ReadinessRequest,
    Selection,
    SelectionMode,
)
from app.contracts.tool_context import ActorRef, Origin, Role, ToolContext
from tests.integration.ai_business.conftest import Stack, reference_subject
from tests.integration.ai_business.db_guard import sql
from tests.paths import REPO_ROOT

MONTH = "2026-10"
EMPLOYEE = "E-001"
ALTERNATIVE = "vehicle:CAR-10"  # ATTO 3, the workbook alternative for E-001
SPEC_RAW = ("11031.819", "11692.817", "660.998")
"""Agent Spec §8 for E-001: exact current net, alternative net and difference."""


def _workbook_row(employee: str) -> dict[str, Any]:
    book = openpyxl.load_workbook(
        REPO_ROOT / "data" / "source" / "challenge-data.xlsx", read_only=True, data_only=True
    )
    try:
        rows = list(book["חישובים"].iter_rows(values_only=True))
    finally:
        book.close()
    header = [str(h).strip() for h in rows[0]]
    return next(dict(zip(header, r, strict=False)) for r in rows[1:] if r[0] == employee)


def _employee_actor(employee: str) -> ActorRef:
    """The canonical seeded employee, used read-only (never updated or confirmed)."""
    return ActorRef(
        subject_id=reference_subject(employee), roles=frozenset({Role.EMPLOYEE}), origin=Origin.HTTP
    )


def _three(raw: str) -> Decimal:
    return Decimal(raw).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


async def _case(stack: Stack, actor: ActorRef) -> ToolContext:
    view = await stack.conversations.start_conversation(
        actor, client_request_id=uuid4(), calculation_month=MONTH
    )
    return (await stack.conversations.turn_binding(actor, view.conversation_id)).tool_context


async def test_b1_first_real_vehicle_comparison(reference_db: str, stack: Stack) -> None:
    tools = stack.business.tools
    # Confirmation writes, so the comparison runs for an isolated copy of E-001's profile.
    ctx = await _case(stack, await stack.clone_reference_employee(EMPLOYEE))

    # 1. The real saved profile (a copy of the reference employee's).
    context = (await tools.get_conversation_context(ctx)).data
    assert context is not None and context.profile.base_gross.status.value == "usable"
    case_version, profile_version = context.case_version, context.latest_profile_version

    # 2. Real catalogue and eligibility for the workbook alternative.
    listed = await tools.list_eligible_options(
        ctx, ListOptionsRequest(expected_case_version=case_version)
    )
    assert listed.data is not None
    eligible = {
        i.option_key for i in listed.data.items if i.eligibility is EligibilityStatus.ELIGIBLE
    }
    assert ALTERNATIVE in eligible
    eligibility = await tools.check_eligibility(
        ctx, EligibilityRequest(expected_case_version=case_version, option_key=ALTERNATIVE)
    )
    assert eligibility.data is not None
    assert eligibility.data.status is EligibilityStatus.ELIGIBLE

    # 3. Readiness: the alternative is ready; the saved baseline still needs confirmation here.
    selection = Selection(mode=SelectionMode.EXPLICIT, option_keys=[ALTERNATIVE])
    readiness_request = ReadinessRequest(
        goal=ReadinessGoal.COMPARE_TO_CURRENT,
        selection=selection,
        include_current=True,
        expected_case_version=case_version,
    )
    readiness = await tools.check_calculation_readiness(ctx, readiness_request)
    assert readiness.data is not None and ALTERNATIVE in readiness.data.ready_scenario_ids
    to_confirm = [
        i.field
        for i in readiness.issues
        if i.code is IssueCode.AMBIGUOUS_INPUT
        and i.reason == "confirmation_required"
        and i.field is not None
    ]
    assert to_confirm and all(f.value.startswith("currentArrangement.") for f in to_confirm)
    assert readiness.data.baseline_ready is False
    assert not set(to_confirm) & set(readiness.data.missing_employee_fields)

    # 4. An unconfirmed baseline: the alternative is calculated, the delta is never invented.
    unconfirmed = await tools.compare_scenarios(
        ctx,
        CompareRequest(
            selection=selection,
            include_current=True,
            kind=RunKind.REGULAR,
            expected_case_version=case_version,
            expected_profile_version=profile_version,
            operation_id=uuid4(),
        ),
    )
    assert unconfirmed.data is not None
    assert unconfirmed.data.current is not None
    assert unconfirmed.data.current.net is None
    assert all(a.delta is None for a in unconfirmed.data.alternatives)

    # 5. The employee confirms exactly those saved facts (values unchanged).
    confirmed = await tools.confirm_profile_fields(
        ctx,
        ConfirmProfileFieldsRequest(
            expected_case_version=unconfirmed.case_version or case_version,
            expected_profile_version=profile_version,
            operation_id=uuid4(),
            fields=to_confirm,
        ),
    )
    assert confirmed.status is EnvelopeStatus.OK and confirmed.data is not None
    assert set(confirmed.data.confirmed_fields) == set(to_confirm)
    case_version = confirmed.data.new_case_version
    profile_version = confirmed.data.new_profile_version

    ready = await tools.check_calculation_readiness(
        ctx, readiness_request.model_copy(update={"expected_case_version": case_version})
    )
    assert ready.data is not None and ready.data.baseline_ready is True

    # 6. The real comparison.
    compared = await tools.compare_scenarios(
        ctx,
        CompareRequest(
            selection=selection,
            include_current=True,
            kind=RunKind.REGULAR,
            expected_case_version=case_version,
            expected_profile_version=profile_version,
            operation_id=uuid4(),
        ),
    )
    assert compared.status is EnvelopeStatus.OK and compared.data is not None
    result = compared.data
    assert result.kind is RunKind.REGULAR and result.current is not None
    assert result.current.net is not None
    [alternative] = result.alternatives
    assert alternative.status is ScenarioStatus.CALCULATED
    assert alternative.net is not None and alternative.delta is not None

    # 7. Exact values: Agent Spec §8 (precise) and the workbook (displayed whole shekels).
    current_raw, alternative_raw, delta_raw = SPEC_RAW
    assert _three(result.current.net.raw) == Decimal(current_raw)
    assert _three(alternative.net.raw) == Decimal(alternative_raw)
    assert _three(alternative.delta.raw) == Decimal(delta_raw)
    workbook = _workbook_row(EMPLOYEE)
    assert Decimal(result.current.net.display) == Decimal(str(workbook["נטו היום"]))
    assert Decimal(alternative.net.display) == Decimal(str(workbook["נטו בחלופה"]))
    assert Decimal(alternative.delta.display) == Decimal("661")

    # 8. The Business module stored the immutable run and serves it back unchanged.
    details = await tools.get_comparison_details(
        ctx, ComparisonDetailsRequest(run_id=result.run_id)
    )
    assert details.data is not None and details.data.result == result
    assert details.data.input_snapshot
    stored = sql(
        reference_db, "SELECT id FROM business.comparison_runs WHERE id = %s", result.run_id
    )
    assert stored == [(result.run_id,)]
    print(  # reported in the Stage B1 summary
        "B1",
        EMPLOYEE,
        result.current.net.model_dump(),
        alternative.net.model_dump(),
        alternative.delta.model_dump(),
        [f.value for f in to_confirm],
    )


async def test_b1_another_employee_cannot_compare_this_case(
    reference_db: str, stack: Stack
) -> None:
    ctx = await _case(stack, await stack.clone_reference_employee(EMPLOYEE))
    intruder = _employee_actor("E-002")  # read-only use of the canonical employee
    forged = ctx.model_copy(update={"actor": intruder})
    envelope = await stack.business.tools.compare_scenarios(
        forged,
        CompareRequest(
            selection=Selection(mode=SelectionMode.EXPLICIT, option_keys=[ALTERNATIVE]),
            include_current=False,
            kind=RunKind.REGULAR,
            expected_case_version=0,
            expected_profile_version=1,
            operation_id=uuid4(),
        ),
    )
    assert envelope.status is EnvelopeStatus.ERROR and envelope.data is None
    assert envelope.error is not None and envelope.error.code.value == "NOT_FOUND"
