"""`check_calculation_readiness` and comparison selection. STATUS: PROVISIONAL.

Readiness is goal- and scope-specific: only facts needed for the current operation are
reported. Ineligible options are blocked, not merely incomplete. There is no global
questionnaire.
"""

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from app.contracts.catalogue import OptionFilters
from app.contracts.common import (
    ContractModel,
    FieldPath,
    IssueCode,
    OptionCategory,
    OptionKeyStr,
    RunKind,
)
from app.contracts.representatives import RepresentativeFieldCode


class ReadinessGoal(StrEnum):
    LIST_REGULAR = "list_regular"
    LIST_FAMILY = "list_family"
    WAIVER_ELIGIBILITY = "waiver_eligibility"
    CALCULATE_NET = "calculate_net"
    COMPARE_TO_CURRENT = "compare_to_current"


class SelectionMode(StrEnum):
    EXPLICIT = "explicit"
    RESULT_SET = "result_set"
    ALL_ELIGIBLE = "all_eligible"


class Selection(ContractModel):
    mode: SelectionMode
    option_keys: list[OptionKeyStr] | None = Field(default=None, max_length=30)
    result_set_id: UUID | None = None
    categories: list[OptionCategory] | None = None
    filters: OptionFilters | None = None

    @model_validator(mode="after")
    def _mode_fields(self) -> Self:
        if self.mode is SelectionMode.EXPLICIT:
            if not self.option_keys or self.result_set_id is not None:
                raise ValueError("explicit selection requires optionKeys only")
            if len(set(self.option_keys)) != len(self.option_keys):
                raise ValueError("optionKeys must be unique")
        elif self.mode is SelectionMode.RESULT_SET:
            if self.result_set_id is None:
                raise ValueError("result_set selection requires resultSetId")
        elif self.option_keys is not None or self.result_set_id is not None:
            raise ValueError("all_eligible selection uses categories/filters only")
        return self


class PendingRepresentativeField(ContractModel):
    request_id: UUID
    field_code: RepresentativeFieldCode
    option_key: OptionKeyStr | None = None


class BlockedScenario(ContractModel):
    option_key: OptionKeyStr
    reason: IssueCode
    fields: list[FieldPath] = Field(default_factory=list)


class ReadinessRequest(ContractModel):
    goal: ReadinessGoal
    selection: Selection | None = None
    include_current: bool = False
    expected_case_version: int = Field(ge=0)
    kind: RunKind = RunKind.REGULAR
    """E2 (approved 2026-10-20): the fact basis of the calculation being prepared, exactly as
    `compare_scenarios` uses it (`regular`: the latest actual profile; `hypothetical`: the case
    copy with its overrides). Ignored by listing goals. Omitted means `regular`."""


class ReadinessResult(ContractModel):
    ready_scenario_ids: list[OptionKeyStr] = Field(default_factory=list)
    missing_employee_fields: list[FieldPath] = Field(default_factory=list)
    ambiguous_fields: list[FieldPath] = Field(default_factory=list)
    pending_representative_fields: list[PendingRepresentativeField] = Field(default_factory=list)
    blocked_scenarios: list[BlockedScenario] = Field(default_factory=list)
    baseline_ready: bool | None = None
    """null when the goal does not involve the current baseline."""
    scope_complete: bool
