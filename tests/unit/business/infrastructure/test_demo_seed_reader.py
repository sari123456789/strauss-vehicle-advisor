"""Demo-seed reader: workbook example employees + explicit seed mapping (needs data/source)."""

import json
from dataclasses import fields
from decimal import Decimal
from pathlib import Path

import pytest

from app.bootstrap import build_demo_seed_reader
from app.business.application.ports.demo_seed import DemoEmployeeSeed
from app.business.domain.arrangements import ArrangementKind
from app.business.domain.rules import EntitlementGroup
from app.business.infrastructure.imports.demo_seed_reader import (
    DemoSeedFormatError,
    FileDemoSeedSourceReader,
    _exact_decimal,
)
from app.contracts.tool_context import Role
from tests.paths import REPO_ROOT

SEED_PATH = REPO_ROOT / "data" / "demo" / "demo-accounts.json"


def _reader(seed_path: Path = SEED_PATH) -> FileDemoSeedSourceReader:
    return FileDemoSeedSourceReader(
        repo_root=REPO_ROOT,
        manifest_path=REPO_ROOT / "data" / "source-manifest.json",
        policy_path=REPO_ROOT / "data" / "rules" / "derived-demo-policy.json",
        seed_path=seed_path,
    )


def test_exact_decimal_conversion_of_workbook_numbers() -> None:
    assert _exact_decimal(2.5, "x") == Decimal("2.5")
    assert _exact_decimal(2.75, "x") == Decimal("2.75")
    assert _exact_decimal(6, "x") == Decimal(6)
    for bad in (0.1 + 0.2, True, None, "2.5"):
        with pytest.raises(DemoSeedFormatError):
            _exact_decimal(bad, "x")


@pytest.mark.sources
def test_reads_the_six_reference_employees_and_staff() -> None:
    source = build_demo_seed_reader().read()
    by_ref = {e.employee_ref: e for e in source.employees}
    assert sorted(by_ref) == ["E-001", "E-002", "E-003", "E-004", "E-005", "E-006"]
    e1 = by_ref["E-001"]
    assert (e1.login, e1.display_name, e1.occupation_id) == ("e001", "נועה לוי", "OCC-PRODUCT-MGR")
    assert e1.entitlement_group is EntitlementGroup.GIMEL
    assert (e1.base_gross, e1.commissions_gross, e1.bonus_gross) == (18500, 0, 0)
    assert e1.pension_employee_rate == Decimal("0.06")
    assert e1.study_fund_employee_rate == Decimal("0.025")
    assert e1.credit_points == Decimal("2.75")
    assert e1.qualifying_children_count == 2
    assert e1.current.kind is ArrangementKind.COMPANY_CAR
    assert (e1.current.catalogue_id, e1.current.participation) == ("CAR-09", Decimal(300))
    e3 = by_ref["E-003"]
    assert e3.pension_employee_rate == Decimal("0.065")
    assert e3.current.kind is ArrangementKind.ALLOWANCE and e3.current.allowance == Decimal(1500)
    assert by_ref["E-002"].study_fund_employee_rate == Decimal(0)
    assert {s.login: s.roles for s in source.staff} == {
        "rep01": frozenset({Role.REPRESENTATIVE}),
        "ops01": frozenset({Role.IMPORT_OPERATOR}),
    }
    assert source.workbook.path == "data/source/challenge-data.xlsx"
    assert source.seed_file.path == "data/demo/demo-accounts.json"


@pytest.mark.sources
def test_seed_records_never_carry_the_fictional_id_number() -> None:
    names = {f.name for f in fields(DemoEmployeeSeed)}
    assert not any("id_number" in n or "national" in n for n in names)
    source = build_demo_seed_reader().read()
    text = repr(source)
    for fake_id in ("312456783", "209876549", "301122339", "287654321", "334455664"):
        assert fake_id not in text


def _seed_with(tmp_path: Path, change: object) -> Path:
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    change(seed)  # type: ignore[operator]
    path = tmp_path / "demo-accounts.json"
    path.write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.sources
@pytest.mark.parametrize(
    "change",
    [
        lambda s: s["employees"][0]["currentArrangement"].update(workbookText="COROLLA"),
        lambda s: s["employees"].pop(),
        lambda s: s.update(uuidNamespace="not-a-uuid"),
        lambda s: s["employees"][2]["currentArrangement"].update(allowance="15.5"),
        lambda s: s["staff"][0].update(roles=["admin"]),
    ],
)
def test_inconsistent_seed_mapping_fails(tmp_path: Path, change: object) -> None:
    with pytest.raises(DemoSeedFormatError):
        _reader(_seed_with(tmp_path, change)).read()
