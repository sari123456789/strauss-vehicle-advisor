"""Structured model outputs. These are proposals only; code validates every value.

The schemas deliberately contain no identifiers (employee, conversation, case, request or run
IDs): the model cannot select or forge them. Option references are resolved by code against
the list actually displayed to the employee.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.common import FieldPath, Propulsion


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Intent(StrEnum):
    LIST_OPTIONS = "list_options"
    CALCULATE_OPTIONS = "calculate_options"
    COMPARE_TO_CURRENT = "compare_to_current"
    EXPLAIN_RESULT = "explain_result"
    SHOW_HISTORY = "show_history"
    UPDATE_FACTS = "update_facts"
    CONFIRM_FACTS = "confirm_facts"
    """The employee confirms that saved details the advisor asked about are still correct."""
    CANCEL = "cancel"
    ASK_REPRESENTATIVE = "ask_representative"
    GENERAL_QUESTION = "general_question"
    SMALLTALK = "smalltalk"
    UNCLEAR = "unclear"


class FactField(StrEnum):
    """The allowlisted `FieldPath` values plus one marker for an unclear salary statement."""

    BASE_GROSS = FieldPath.BASE_GROSS.value
    COMMISSIONS_GROSS = FieldPath.COMMISSIONS_GROSS.value
    BONUS_GROSS = FieldPath.BONUS_GROSS.value
    PENSION_EMPLOYEE_RATE = FieldPath.PENSION_EMPLOYEE_RATE.value
    STUDY_FUND_EMPLOYEE_RATE = FieldPath.STUDY_FUND_EMPLOYEE_RATE.value
    CREDIT_POINTS = FieldPath.CREDIT_POINTS.value
    OCCUPATION_ID = FieldPath.OCCUPATION_ID.value
    ENTITLEMENT_GROUP = FieldPath.ENTITLEMENT_GROUP.value
    QUALIFYING_CHILDREN_COUNT = FieldPath.QUALIFYING_CHILDREN_COUNT.value
    CURRENT_KIND = FieldPath.CURRENT_KIND.value
    CURRENT_VEHICLE = FieldPath.CURRENT_VEHICLE.value
    CURRENT_PROPULSION = FieldPath.CURRENT_PROPULSION.value
    CURRENT_PARTICIPATION = FieldPath.CURRENT_PARTICIPATION.value
    CURRENT_FAMILY_DEDUCTION = FieldPath.CURRENT_FAMILY_DEDUCTION.value
    CURRENT_FAMILY_DEDUCTION_INCLUDED = FieldPath.CURRENT_FAMILY_DEDUCTION_INCLUDED.value
    CURRENT_ALLOWANCE = FieldPath.CURRENT_ALLOWANCE.value
    SALARY_UNSPECIFIED = "payroll.salaryUnspecified"
    """A salary amount whose composition (base / total gross / net) is unclear."""


class FactMode(StrEnum):
    ACTUAL = "actual"
    HYPOTHETICAL = "hypothetical"
    AMBIGUOUS = "ambiguous"


class ProposedFact(_Strict):
    field: FactField
    value: str = Field(max_length=120)
    """As stated, lightly normalized (see the interpretation instructions)."""
    mode: FactMode
    quote: str = Field(max_length=200)
    """The employee's words this fact was taken from."""


class OptionReference(_Strict):
    kind: Literal["ordinal", "catalogue_id", "name"]
    value: str = Field(max_length=60)


class TurnInterpretation(_Strict):
    intent: Intent
    facts: list[ProposedFact] = Field(default_factory=list, max_length=16)
    option_refs: list[OptionReference] = Field(default_factory=list, max_length=30)
    all_options: bool = False
    propulsion: list[Propulsion] = Field(default_factory=list, max_length=4)
    include_waiver: bool = False
    unknown_fields: list[FactField] = Field(default_factory=list, max_length=16)
    """Facts the employee says they do not know."""
    confirmed_fields: list[FactField] = Field(default_factory=list, max_length=16)
    """Saved facts the employee explicitly says are still correct. Code confirms only facts
    the advisor asked about; an empty list with `confirm_facts` means all of them."""


class ComposedText(_Strict):
    text: str = Field(max_length=1500)
    """Hebrew prose; amounts appear only as {{placeholders}} rendered by code."""
