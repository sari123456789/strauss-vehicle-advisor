"""Rules JSON + derived policy mapping: exact decimals, traceable hashes, explicit failures."""

import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from app.business.bl.rules.validator import validate_supported_rules
from app.business.domain.errors import UnsupportedPolicyError
from app.business.domain.rules import EntitlementGroup
from app.business.infrastructure.imports.rules_json_loader import RulesFormatError
from app.business.infrastructure.imports.rules_loader import load_rules_config
from tests.paths import REPO_ROOT

RULES = REPO_ROOT / "data" / "source" / "business-rules.json"
POLICY = REPO_ROOT / "data" / "rules" / "derived-demo-policy.json"


def test_rules_are_loaded_with_exact_decimals_and_source_hashes() -> None:
    config = load_rules_config(RULES, POLICY)
    manifest = json.loads((REPO_ROOT / "data" / "source-manifest.json").read_text("utf-8"))
    rules_entry = next(s for s in manifest["sources"] if s["path"].endswith("business-rules.json"))
    assert config.source.sha256 == rules_entry["sha256"]
    assert config.source.supplied_version == "mock-2026-10-12"
    assert config.derived.version == "derived-demo-v2"
    assert config.national_insurance.low_rate == Decimal("0.0104")
    assert config.health_insurance.high_rate == Decimal("0.0517")
    assert config.budgets[EntitlementGroup.GIMEL] == Decimal(4500)
    assert config.brackets[-1].upper_bound is None
    assert config.value_of_use_key == ("שנת מס", "שנת רישום", "קוד סוג רכב", "קוד תוצר", "קוד דגם")


def test_supplied_rules_use_only_supported_policies() -> None:
    validate_supported_rules(load_rules_config(RULES, POLICY))


def test_participation_reducing_benefit_is_unsupported() -> None:
    config = load_rules_config(RULES, POLICY)
    changed = replace(
        config, participation=replace(config.participation, reduces_value_of_use=True)
    )
    with pytest.raises(UnsupportedPolicyError):
        validate_supported_rules(changed)


def _write(path: Path, content: dict[str, object]) -> str:
    raw = json.dumps(content, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def test_policy_for_a_different_rules_source_is_rejected(tmp_path: Path) -> None:
    """AC32: same supplied version, different content -> different hash, not silently reused."""
    rules = json.loads(RULES.read_text("utf-8"))
    rules["budgets"]["א"] = 3600
    _write(tmp_path / "rules.json", rules)
    with pytest.raises(RulesFormatError, match="different rules source"):
        load_rules_config(tmp_path / "rules.json", POLICY)


def test_missing_key_fails_explicitly(tmp_path: Path) -> None:
    rules = json.loads(RULES.read_text("utf-8"))
    del rules["netMethod"]["health"]
    digest = _write(tmp_path / "rules.json", rules)
    policy = json.loads(POLICY.read_text("utf-8"))
    policy["appliesToSource"]["sha256"] = digest
    _write(tmp_path / "policy.json", policy)
    with pytest.raises(RulesFormatError, match="netMethod.health"):
        load_rules_config(tmp_path / "rules.json", tmp_path / "policy.json")


def test_non_utf8_rules_file_fails(tmp_path: Path) -> None:
    (tmp_path / "rules.json").write_bytes('{"version": "גרסה"}'.encode("cp1255"))
    with pytest.raises(RulesFormatError, match="UTF-8"):
        load_rules_config(tmp_path / "rules.json", POLICY)
