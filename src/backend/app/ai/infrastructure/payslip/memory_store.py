"""`InMemoryDraftStore`: SINGLE-PROCESS DEMO STORE. It does NOT provide durable persistence.

- A process restart loses every draft. Callers then get `DRAFT_NOT_FOUND` and the employee
  uploads the payslip again; nothing is recovered and the profile is never touched.
- Drafts are not shared between workers or instances: run the API as one process when this
  store is used. Do not use it in production; a durable `agent`-schema table is proposed in
  docs/proposals/payslip-import.md.
- Expired drafts are purged lazily on every `add`; the service also checks expiry on access.
- Per-owner and global limits bound memory use (`TOO_MANY_DRAFTS`).
- `lock(draft_id)` serializes corrections, approval and cancellation of one draft within this
  process. Duplicate approvals across a restart are still prevented by the Business
  idempotency key (`operationId`).
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID

from app.ai.application.payslip.errors import PayslipError, PayslipErrorCode
from app.ai.application.payslip.models import DraftStatus, PayslipDraft
from app.ai.application.payslip.ports import PayslipImportConfig


class InMemoryDraftStore:
    def __init__(self) -> None:
        self._drafts: dict[UUID, PayslipDraft] = {}
        self._locks: dict[UUID, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def add(self, draft: PayslipDraft, *, now: datetime, config: PayslipImportConfig) -> None:
        async with self._guard:
            for draft_id in [d.draft_id for d in self._drafts.values() if now >= d.expires_at]:
                self._drafts.pop(draft_id, None)
                self._locks.pop(draft_id, None)
            pending = [
                d
                for d in self._drafts.values()
                if d.owner_subject_id == draft.owner_subject_id
                and d.status is not DraftStatus.APPLIED
            ]
            if (
                len(pending) >= config.max_drafts_per_owner
                or len(self._drafts) >= config.max_drafts_total
            ):
                raise PayslipError(PayslipErrorCode.TOO_MANY_DRAFTS)
            self._drafts[draft.draft_id] = draft

    async def get(self, draft_id: UUID) -> PayslipDraft | None:
        return self._drafts.get(draft_id)

    async def replace(self, draft: PayslipDraft) -> None:
        if draft.draft_id in self._drafts:
            self._drafts[draft.draft_id] = draft

    async def remove(self, draft_id: UUID) -> None:
        self._drafts.pop(draft_id, None)

    @asynccontextmanager
    async def lock(self, draft_id: UUID) -> AsyncIterator[None]:
        lock = self._locks.setdefault(draft_id, asyncio.Lock())
        async with lock:
            yield
