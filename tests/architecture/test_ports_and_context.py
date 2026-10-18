"""Port implementation and authorization-context checks that need imports (not only AST).

Static guarantees only. Runtime ownership checks (foreign case -> NOT_FOUND, invented IDs
rejected, worker actor from persisted ownership) are Application/integration tests (3B, 4C).
"""

import inspect
import pkgutil
from importlib import import_module
from typing import Any, Protocol, get_type_hints

import pydantic

import app.business.application.ports as ports_package
import app.contracts as contracts_package
from app.bootstrap import build_demo_seed_reader, build_reference_source_reader
from app.business.application.ports.auth import UserDirectory
from app.business.application.ports.clock import Clock
from app.business.application.ports.credentials import (
    AccessTokenCodec,
    CredentialStore,
    PasswordHasher,
    RefreshSessionRepository,
)
from app.business.application.ports.demo_seed import DemoSeedSourceReader
from app.business.application.ports.reference_data import (
    ReferenceDataRepository,
    ReferenceSourceReader,
)
from app.business.application.ports.repositories import (
    AlternativeRepository,
    CaseRepository,
    ComparisonRunRepository,
    EmployeeProfileRepository,
    OperationRepository,
    OptionResultSetRepository,
    RepresentativeRequestRepository,
)
from app.business.application.ports.unit_of_work import BusinessUnitOfWork, UnitOfWork
from app.business.infrastructure.clock import SystemClock
from app.business.infrastructure.database.engine import create_engine, create_session_factory
from app.business.infrastructure.database.repositories.reference_data import (
    SqlReferenceDataRepository,
)
from app.business.infrastructure.database.unit_of_work import SqlUnitOfWorkFactory
from app.business.infrastructure.security.password_hasher import ScryptPasswordHasher
from app.business.infrastructure.security.token_codec import Hs256TokenCodec
from app.contracts.business_port import CANONICAL_TOOL_NAMES, BusinessToolsPort
from app.contracts.case_lifecycle import CaseLifecyclePort
from app.contracts.tool_context import ActorRef, ToolContext

IMPLEMENTED_PORTS = {
    "ReferenceDataRepository",
    "ReferenceSourceReader",
    "UnitOfWork",
    "UnitOfWorkFactory",
    "BusinessUnitOfWork",
    "BusinessUnitOfWorkFactory",
    "UserDirectory",
    "EmployeeProfileRepository",
    "CaseRepository",
    "AlternativeRepository",
    "OperationRepository",
    "OptionResultSetRepository",
    "ComparisonRunRepository",
    "RepresentativeRequestRepository",
    "DemoSeedSourceReader",
    "Clock",
    "PasswordHasher",
    "AccessTokenCodec",
    "CredentialStore",
    "RefreshSessionRepository",
}
PENDING_PORTS: dict[str, str] = {}
"""Business ports without an implementation yet (none: `DurableInputQueue` moved to AI, C3)."""
IDENTITY_FIELDS = {"actor", "subject_id", "owner_subject_id", "employee_id", "roles"}


def _protocols(package: Any) -> set[str]:
    names = set()
    for info in pkgutil.iter_modules(package.__path__, package.__name__ + "."):
        module = import_module(info.name)
        for name, obj in vars(module).items():
            if (
                inspect.isclass(obj)
                and obj.__module__ == module.__name__
                and Protocol in obj.__mro__[1:]
            ):
                names.add(name)
    return names


def test_every_application_port_is_implemented_or_explicitly_pending() -> None:
    assert _protocols(ports_package) == IMPLEMENTED_PORTS | set(PENDING_PORTS)


def test_infrastructure_implements_reference_data_ports() -> None:
    engine = create_engine("postgresql+psycopg://unused:unused@localhost:1/unused")
    repository = SqlReferenceDataRepository(create_session_factory(engine))
    assert isinstance(repository, ReferenceDataRepository)
    assert isinstance(build_reference_source_reader(), ReferenceSourceReader)


def test_sql_unit_of_work_implements_the_business_ports() -> None:
    engine = create_engine("postgresql+psycopg://unused:unused@localhost:1/unused")
    factory = SqlUnitOfWorkFactory(create_session_factory(engine))
    uow = factory()
    assert isinstance(uow, UnitOfWork) and isinstance(uow, BusinessUnitOfWork)
    expected = {
        "users": UserDirectory,
        "profiles": EmployeeProfileRepository,
        "cases": CaseRepository,
        "alternatives": AlternativeRepository,
        "operations": OperationRepository,
        "option_result_sets": OptionResultSetRepository,
        "comparison_runs": ComparisonRunRepository,
        "representative_requests": RepresentativeRequestRepository,
        "credentials": CredentialStore,
        "refresh_sessions": RefreshSessionRepository,
    }
    for attribute, port in expected.items():
        assert isinstance(getattr(uow, attribute), port), attribute
    assert not hasattr(uow, "input_queue")  # C2: no AI inputs in a Business transaction
    assert isinstance(SystemClock(), Clock)
    assert isinstance(build_demo_seed_reader(), DemoSeedSourceReader)
    assert isinstance(ScryptPasswordHasher(), PasswordHasher)
    codec = Hs256TokenCodec(b"x" * 32, issuer="i", audience="a")
    assert isinstance(codec, AccessTokenCodec)


def test_every_business_tool_receives_the_server_injected_tool_context() -> None:
    for name in CANONICAL_TOOL_NAMES:
        method = getattr(BusinessToolsPort, name)
        params = list(inspect.signature(method).parameters)
        assert params[:2] == ["self", "ctx"], name
        assert get_type_hints(method)["ctx"] is ToolContext, name


def test_case_creation_receives_the_authenticated_actor_and_no_transaction() -> None:
    method = CaseLifecyclePort.create_case_for_conversation
    assert list(inspect.signature(method).parameters) == ["self", "actor", "command"]
    assert get_type_hints(method)["actor"] is ActorRef
    import app.contracts.case_lifecycle as lifecycle

    assert not hasattr(lifecycle, "TransactionHandle")  # C1


def test_durable_input_queue_is_ai_internal() -> None:
    import app.ai.application.ports.input_queue as ai_queue

    assert hasattr(ai_queue, "DurableInputQueue")
    assert not any(
        info.name.endswith("input_queue")
        for info in pkgutil.iter_modules(ports_package.__path__, ports_package.__name__ + ".")
    )


def test_case_lifecycle_adapter_implements_the_contract() -> None:
    from app.business.application.cases.lifecycle_adapter import CaseLifecycleAdapter

    adapter = CaseLifecycleAdapter(cases=None)  # type: ignore[arg-type]
    assert isinstance(adapter, CaseLifecyclePort)


SERVICE_ENTRY_POINTS = {
    ("app.business.application.profiles.profile_service", "EmployeeProfileService"): {
        "get_profile": "ActorRef"
    },
    ("app.business.application.cases.case_service", "CaseService"): {
        "create_case_for_conversation": "ActorRef",
        "get_context": "ToolContext",
        "update_inputs": "ToolContext",
        "confirm_profile_fields": "ToolContext",
        "context_for_case": "ActorRef",
    },
    ("app.business.application.vehicles.catalogue_service", "CatalogueService"): {
        "list_eligible_options": "ToolContext",
        "check_eligibility": "ToolContext",
    },
    ("app.business.application.vehicles.valuation_service", "ValuationService"): {
        "resolve_vehicle": "ToolContext",
        "get_vehicle_tax_value": "ToolContext",
    },
    ("app.business.application.occupations.occupation_service", "OccupationService"): {
        "resolve_occupation": "ToolContext",
    },
    (
        "app.business.application.representatives.request_service",
        "RepresentativeRequestService",
    ): {"get_representative_request": "ToolContext"},
    ("app.business.application.comparisons.readiness_service", "ReadinessService"): {
        "check_calculation_readiness": "ToolContext",
    },
    (
        "app.business.application.representatives.question_service",
        "RepresentativeQuestionService",
    ): {"create_representative_question": "ToolContext"},
    ("app.business.application.tools_facade", "BusinessToolsFacade"): {
        name: "ToolContext" for name in CANONICAL_TOOL_NAMES
    },
    ("app.business.application.comparisons.comparison_service", "ComparisonService"): {
        "compare_scenarios": "ToolContext",
        "get_comparison_details": "ToolContext",
    },
}


def test_public_service_methods_take_a_server_built_identity_first() -> None:
    expected_types = {"ActorRef": ActorRef, "ToolContext": ToolContext}
    for (module_name, class_name), methods in SERVICE_ENTRY_POINTS.items():
        cls = getattr(import_module(module_name), class_name)
        public = {
            n
            for n, f in inspect.getmembers(cls, inspect.iscoroutinefunction)
            if not n.startswith("_")
        }
        internal = {"apply_actual"}  # takes an already authorized Unit of Work
        assert public - internal == set(methods), class_name
        for name, type_name in methods.items():
            first = list(inspect.signature(getattr(cls, name)).parameters)[1]
            assert get_type_hints(getattr(cls, name))[first] is expected_types[type_name], name


def test_tool_context_carries_only_server_established_identity() -> None:
    assert set(ToolContext.model_fields) == {"actor", "conversation_id", "case_id", "trace_id"}


def test_request_contracts_cannot_carry_actor_identity() -> None:
    """Only ToolContext/ActorRef hold identity; no model-facing contract may select an actor."""
    offenders = []
    for info in pkgutil.iter_modules(contracts_package.__path__, "app.contracts."):
        module = import_module(info.name)
        for name, obj in vars(module).items():
            if (
                inspect.isclass(obj)
                and issubclass(obj, pydantic.BaseModel)
                and obj.__module__ == module.__name__
                and obj not in (ToolContext, ActorRef)
            ):
                offenders.extend(f"{name}.{f}" for f in set(obj.model_fields) & IDENTITY_FIELDS)
    assert offenders == []
