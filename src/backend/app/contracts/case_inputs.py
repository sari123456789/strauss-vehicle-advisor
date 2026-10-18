"""`update_conversation_inputs` contract (architecture alias: `update_case`). STATUS: PROVISIONAL.

Restricted `set` / `clear` operations only. `mode` applies to the whole operation:
- `actual_update`: atomically updates the shared EmployeeProfile and the current case; requires
  both `expectedCaseVersion` and `expectedProfileVersion` (stale -> VERSION_CONFLICT, never
  last-write-wins).
- `hypothetical`: changes only the case's scenario state; the profile is never touched.
Ambiguous actual-versus-hypothetical intent must be clarified by the agent BEFORE calling.
"""

from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import Field, TypeAdapter, model_validator

from app.contracts.common import (
    ArrangementKind,
    ContractModel,
    CreditPointsStr,
    EntitlementGroup,
    FieldPath,
    Issue,
    NonNegativeMoneyStr,
    OccupationIdStr,
    Propulsion,
    RateStr,
    UpdateMode,
)
from app.contracts.profile import VehicleRef

_STRICT_INT: TypeAdapter[int] = TypeAdapter(Annotated[int, Field(ge=0, le=100, strict=True)])
_STRICT_BOOL: TypeAdapter[bool] = TypeAdapter(Annotated[bool, Field(strict=True)])

_VALUE_ADAPTERS: dict[FieldPath, TypeAdapter[Any]] = {
    FieldPath.BASE_GROSS: TypeAdapter(NonNegativeMoneyStr),
    FieldPath.COMMISSIONS_GROSS: TypeAdapter(NonNegativeMoneyStr),
    FieldPath.BONUS_GROSS: TypeAdapter(NonNegativeMoneyStr),
    FieldPath.PENSION_EMPLOYEE_RATE: TypeAdapter(RateStr),
    FieldPath.STUDY_FUND_EMPLOYEE_RATE: TypeAdapter(RateStr),
    FieldPath.CREDIT_POINTS: TypeAdapter(CreditPointsStr),
    FieldPath.OCCUPATION_ID: TypeAdapter(OccupationIdStr),
    FieldPath.ENTITLEMENT_GROUP: TypeAdapter(EntitlementGroup),
    FieldPath.QUALIFYING_CHILDREN_COUNT: _STRICT_INT,
    FieldPath.CURRENT_KIND: TypeAdapter(ArrangementKind),
    FieldPath.CURRENT_VEHICLE: TypeAdapter(VehicleRef),
    FieldPath.CURRENT_PROPULSION: TypeAdapter(Propulsion),
    FieldPath.CURRENT_PARTICIPATION: TypeAdapter(NonNegativeMoneyStr),
    FieldPath.CURRENT_FAMILY_DEDUCTION: TypeAdapter(NonNegativeMoneyStr),
    FieldPath.CURRENT_FAMILY_DEDUCTION_INCLUDED: _STRICT_BOOL,
    FieldPath.CURRENT_ALLOWANCE: TypeAdapter(NonNegativeMoneyStr),
}


class SetChange(ContractModel):
    op: Literal["set"]
    field: FieldPath
    value: VehicleRef | bool | int | str
    """Typed per field (see docs/api-contracts.md). `null` is invalid; use `clear`."""

    @model_validator(mode="after")
    def _value_type_matches_field(self) -> Self:
        raw = (
            self.value.model_dump(by_alias=True)
            if isinstance(self.value, VehicleRef)
            else self.value
        )
        _VALUE_ADAPTERS[self.field].validate_python(raw)
        return self


class ClearChange(ContractModel):
    """Marks the field unknown (`status=missing`). Never sets zero."""

    op: Literal["clear"]
    field: FieldPath


Change = Annotated[SetChange | ClearChange, Field(discriminator="op")]


class UpdateInputsRequest(ContractModel):
    mode: UpdateMode
    expected_case_version: int = Field(ge=0)
    expected_profile_version: int | None = Field(default=None, ge=0)
    operation_id: UUID
    source_message_id: UUID | None = None
    changes: list[Change] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def _consistency(self) -> Self:
        if self.mode is UpdateMode.ACTUAL_UPDATE and self.expected_profile_version is None:
            raise ValueError("actual_update requires expectedProfileVersion")
        fields = [c.field for c in self.changes]
        if len(fields) != len(set(fields)):
            raise ValueError("each field may appear at most once per operation")
        return self


class UpdateInputsResult(ContractModel):
    accepted_fields: list[FieldPath]
    conflicts: list[Issue] = Field(default_factory=list)
    """Valid but conflicting facts recorded as requiring clarification (e.g. SOURCE_CONFLICT)."""
    new_case_version: int = Field(ge=0)
    new_profile_version: int | None = Field(default=None, ge=0)
    stale_run_ids: list[UUID] = Field(default_factory=list)
    missing_information: list[Issue] = Field(default_factory=list)
