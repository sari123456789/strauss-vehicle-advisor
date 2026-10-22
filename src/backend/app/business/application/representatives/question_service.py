"""`create_representative_question` (Step 4C, employee side).

A question asks a representative for one server-recognized business fact. It never pauses the
conversation: only the dependent option (or the baseline) waits (agent-spec §2.12, §9.2).

- Authorized through the server-bound tool context; a closed case is read-only; a stale
  `expectedCaseVersion` is `VERSION_CONFLICT`. Creating a question does not change the case.
- Scope per field code (allowlist): `alternative.participation` needs a catalogue vehicle
  option; `occupation.waiverPolicy` the waiver or no option; `employee.entitlementGroup` and
  `currentArrangement.*` no option. Anything else is `INVALID_BUSINESS_INPUT`.
- An option is persisted as the case's alternative (idempotent) so the request can be tied to
  it; an alternative change later cancels the request (architecture §8).
- Idempotent: the same `idempotencyKey` with the same payload returns the stored request
  (`created=false`); with a different payload it is `OPERATION_PAYLOAD_MISMATCH`. An equivalent
  open request (case, alternative, field, subject) is reused instead of a duplicate (T34).
- The subject snapshot holds the minimal context only - never salary data (D11).
- `expectedValueType` follows the field code one to one (PROVISIONAL, open joint item:
  api-contracts §8 "fieldCode allowlist and expectedValueType").
- Lock order (B3a): operation claim, then the case row `FOR UPDATE`, then the alternative and
  the request are written - the global order, so question creation serializes with
  representative answers and comparisons on the same case.
"""

from uuid import uuid4

from app.business.application.cases.alternatives import ensure_alternatives
from app.business.application.errors import (
    InvalidInput,
    InvalidStateTransition,
    NotFoundError,
    VersionConflict,
    translate_errors,
)
from app.business.application.idempotency import canonical_request_hash
from app.business.application.identity.authorization import AuthorizationPolicy
from app.business.application.outcomes import Outcome
from app.business.application.ports.clock import Clock
from app.business.application.ports.reference_data import ReferenceDataRepository
from app.business.application.ports.repositories import OperationKey, Replay, ScopeKind
from app.business.application.ports.unit_of_work import BusinessUnitOfWorkFactory
from app.business.application.profiles.profile_service import load_profile
from app.business.application.reference import pin_reference
from app.business.domain.arrangements import WAIVER_OPTION_KEY
from app.business.domain.cases import CaseLifecycleStatus
from app.business.domain.employee_profiles import EmployeeProfile
from app.business.domain.json_values import freeze_object
from app.business.domain.representatives import (
    ExpectedValueType,
    RepresentativeFieldCode,
    RepresentativeRequest,
    RequestStatus,
)
from app.contracts.representatives import CreateQuestionRequest, CreateQuestionResult
from app.contracts.representatives import RequestStatus as ContractRequestStatus
from app.contracts.tool_context import ToolContext

CREATE_QUESTION = "create_representative_question"
VEHICLE_PREFIX = "vehicle:"
VALUE_TYPES = {
    RepresentativeFieldCode.ALTERNATIVE_PARTICIPATION: ExpectedValueType.MONEY,
    RepresentativeFieldCode.OCCUPATION_WAIVER_POLICY: ExpectedValueType.BOOLEAN,
    RepresentativeFieldCode.ENTITLEMENT_GROUP: ExpectedValueType.ENTITLEMENT_GROUP,
    RepresentativeFieldCode.VEHICLE_PROPULSION: ExpectedValueType.PROPULSION,
    RepresentativeFieldCode.VEHICLE_TAX_KEY: ExpectedValueType.TAX_KEY,
    RepresentativeFieldCode.GENERAL_QUESTION: ExpectedValueType.TEXT,
}
CASE_LEVEL = frozenset(
    {
        RepresentativeFieldCode.ENTITLEMENT_GROUP,
        RepresentativeFieldCode.VEHICLE_PROPULSION,
        RepresentativeFieldCode.VEHICLE_TAX_KEY,
        RepresentativeFieldCode.GENERAL_QUESTION,
    }
)


def _scope_error(field: RepresentativeFieldCode) -> InvalidInput:
    return InvalidInput(
        "this question does not fit the selected option",
        safe_details={"field": "optionKey", "fieldCode": field.value},
    )


def validate_scope(field: RepresentativeFieldCode, option_key: str | None) -> None:
    if field is RepresentativeFieldCode.ALTERNATIVE_PARTICIPATION:
        if option_key is None or not option_key.startswith(VEHICLE_PREFIX):
            raise _scope_error(field)
    elif field is RepresentativeFieldCode.OCCUPATION_WAIVER_POLICY:
        if option_key not in (None, WAIVER_OPTION_KEY):
            raise _scope_error(field)
    elif option_key is not None:
        raise _scope_error(field)


def subject_snapshot(
    field: RepresentativeFieldCode, option_key: str | None, profile: EmployeeProfile
) -> dict[str, object]:
    """Minimal question context (D11): no salary, no other personal data."""
    if field is RepresentativeFieldCode.GENERAL_QUESTION:
        return {}
    if field is RepresentativeFieldCode.ALTERNATIVE_PARTICIPATION:
        assert option_key is not None
        return {"catalogueId": option_key.removeprefix(VEHICLE_PREFIX)}
    if field in (
        RepresentativeFieldCode.OCCUPATION_WAIVER_POLICY,
        RepresentativeFieldCode.ENTITLEMENT_GROUP,
    ):
        occupation = profile.occupation_id
        return {"occupationId": occupation.value if occupation.is_usable else None}
    vehicle = profile.current_arrangement.vehicle
    ref = vehicle.value if vehicle.is_usable else None
    if ref is None:
        return {"currentVehicle": None}
    if ref.catalogue_id is not None:
        return {"currentVehicle": {"catalogueId": ref.catalogue_id}}
    key = ref.tax_key
    assert key is not None
    return {
        "currentVehicle": {
            "taxKey": {
                "taxYear": key.tax_year,
                "registrationYear": key.registration_year,
                "vehicleTypeCode": key.vehicle_type_code,
                "manufacturerCode": key.manufacturer_code,
                "modelCode": key.model_code,
            }
        }
    }


class RepresentativeQuestionService:
    def __init__(
        self,
        unit_of_work: BusinessUnitOfWorkFactory,
        policy: AuthorizationPolicy,
        reference_data: ReferenceDataRepository,
        clock: Clock,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._policy = policy
        self._reference_data = reference_data
        self._clock = clock

    async def create_representative_question(
        self, ctx: ToolContext, request: CreateQuestionRequest
    ) -> Outcome[CreateQuestionResult]:
        request_hash = canonical_request_hash(request.model_dump(mode="json", by_alias=True))
        field = RepresentativeFieldCode(request.field_code.value)
        with translate_errors():
            async with self._unit_of_work() as uow:
                actor, case = await self._policy.tool_case(uow, ctx)
                key = OperationKey(
                    actor.subject_id,
                    ScopeKind.CASE,
                    case.case_id,
                    CREATE_QUESTION,
                    request.idempotency_key,
                )
                claim = await uow.operations.claim(key, request_hash)
                if isinstance(claim, Replay) and claim.result is not None:
                    replayed = CreateQuestionResult.model_validate(dict(claim.result))
                    return Outcome(
                        replayed.model_copy(update={"created": False}), case_version=case.version
                    )
                locked = await uow.cases.lock(case.case_id, owner_subject_id=actor.subject_id)
                if locked is None:
                    raise NotFoundError()
                case = locked
                if case.lifecycle_status is CaseLifecycleStatus.CLOSED:
                    raise InvalidStateTransition("a closed case is read-only")
                if request.expected_case_version != case.version:
                    raise VersionConflict("the case changed in the meantime; reload first")
                validate_scope(field, request.option_key)
                now = self._clock.now()
                alternative = None
                if request.option_key is not None:
                    pinned = await pin_reference(self._reference_data)
                    (alternative,) = await ensure_alternatives(
                        uow,
                        self._reference_data,
                        pinned.release,
                        case,
                        (request.option_key,),
                        now=now,
                    )
                profile = await load_profile(uow, actor.subject_id)
                snapshot = subject_snapshot(field, request.option_key, profile)
                if field is RepresentativeFieldCode.GENERAL_QUESTION:
                    snapshot = {**snapshot, "source": request.source}
                stored, created = await uow.representative_requests.add_or_get_open(
                    RepresentativeRequest(
                        request_id=uuid4(),
                        case_id=case.case_id,
                        field_code=field,
                        expected_value_type=VALUE_TYPES[field],
                        question_text=request.question_text,
                        subject_snapshot=freeze_object(snapshot),
                        subject_fingerprint=canonical_request_hash(
                            {"fieldCode": field.value, "subject": snapshot}
                            # Each general question is its own request: its text is the subject.
                            | (
                                {"question": request.question_text}
                                if field is RepresentativeFieldCode.GENERAL_QUESTION
                                else {}
                            )
                        ),
                        case_version_at_creation=case.version,
                        request_version=0,
                        status=RequestStatus.OPEN,
                        idempotency_key=request.idempotency_key,
                        created_by=actor.subject_id,
                        created_at=now,
                        updated_at=now,
                        alternative_id=None if alternative is None else alternative.alternative_id,
                        alternative_version=None if alternative is None else alternative.version,
                        option_key=request.option_key,
                    )
                )
                result = CreateQuestionResult(
                    request_id=stored.request_id,
                    status=ContractRequestStatus(stored.status.value),
                    created=created,
                )
                stored_result = result.model_copy(update={"created": True})
                await uow.operations.complete(
                    key, freeze_object(stored_result.model_dump(mode="json", by_alias=True))
                )
                await uow.commit()
        return Outcome(result, case_version=case.version)
