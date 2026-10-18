"""`ResponseStager` over AI persistence: one short, fenced transaction per staging."""

from collections.abc import Sequence

from app.ai.application.ports.conversation_store import InputClaim, StaleClaimError
from app.ai.application.ports.response_staging import StagedMessage
from app.ai.infrastructure.persistence.store import AgentPersistence


class SqlResponseStager:
    def __init__(self, persistence: AgentPersistence, claim: InputClaim) -> None:
        self._persistence = persistence
        self._claim = claim

    async def stage(self, input_id: str, messages: Sequence[StagedMessage]) -> None:
        if input_id != str(self._claim.input.input_id):
            raise StaleClaimError(input_id)  # a stager is bound to exactly one claimed input
        async with self._persistence.transaction() as store:
            await store.stage_response(self._claim, messages)
