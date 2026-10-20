"""Bounded retry of `submit_answer` (B3a): only a database deadlock/serialization abort
(`TransientConflictError`, everything rolled back) is retried, never a business conflict.
"""

from typing import Any
from uuid import uuid4

import pytest

from app.business.application.errors import InfrastructureUnavailable, VersionConflict
from app.business.application.identity.authorization import AuthorizationPolicy
from app.business.application.ports.unit_of_work import TransientConflictError
from app.business.application.representatives import answer_service
from app.business.application.representatives.answer_service import (
    MAX_ATTEMPTS,
    RepresentativeAnswerService,
)
from app.contracts.representatives import AnswerSubmit, AnswerView
from app.contracts.tool_context import ActorRef, Origin, Role

REP = ActorRef(subject_id=uuid4(), roles=frozenset({Role.REPRESENTATIVE}), origin=Origin.HTTP)
SUBMIT = AnswerSubmit.model_validate(
    {"operationId": str(uuid4()), "expectedRequestVersion": 0, "outcome": "cannot_determine"}
)
VIEW = AnswerView.model_validate(
    {"outcome": "cannot_determine", "answeredAt": "2026-10-20T09:00:00+00:00"}
)


def service(outcomes: list[Any], monkeypatch: pytest.MonkeyPatch) -> tuple[Any, list[int]]:
    calls: list[int] = []
    monkeypatch.setattr(answer_service, "RETRY_DELAY_SECONDS", 0)
    target = RepresentativeAnswerService(None, AuthorizationPolicy(), None, None)  # type: ignore[arg-type]

    async def once(*_args: object) -> AnswerView:
        calls.append(1)
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome  # type: ignore[no-any-return]

    target._submit_once = once  # type: ignore[assignment]
    return target, calls


async def test_transient_conflicts_are_retried_then_succeed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, calls = service([TransientConflictError(), TransientConflictError(), VIEW], monkeypatch)
    assert await target.submit_answer(REP, uuid4(), SUBMIT) == VIEW
    assert len(calls) == 3


async def test_retries_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    target, calls = service([TransientConflictError()] * MAX_ATTEMPTS, monkeypatch)
    with pytest.raises(InfrastructureUnavailable) as error:
        await target.submit_answer(REP, uuid4(), SUBMIT)
    assert error.value.safe_details == {"reason": "transient_conflict"}
    assert len(calls) == MAX_ATTEMPTS


async def test_business_conflicts_are_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    target, calls = service([VersionConflict("stale"), VIEW], monkeypatch)
    with pytest.raises(VersionConflict):
        await target.submit_answer(REP, uuid4(), SUBMIT)
    assert len(calls) == 1
