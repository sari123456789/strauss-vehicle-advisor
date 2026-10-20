"""Ports of the payslip-import use case (implemented in `app.ai.infrastructure.payslip` or by
existing AI services; wired by the composition root)."""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.ai.application.payslip.models import DocumentText, PayslipDraft
from app.contracts.tool_context import ActorRef, ToolContext


@dataclass(frozen=True, slots=True)
class PayslipImportConfig:
    max_bytes: int = 2 * 1024 * 1024
    max_pages: int = 5
    min_text_chars: int = 40
    """Below this many non-space characters the PDF is treated as scanned/image-only."""
    max_text_chars: int = 60_000
    extraction_timeout_seconds: float = 10.0
    draft_ttl_seconds: int = 24 * 60 * 60
    max_drafts_per_owner: int = 3
    max_drafts_total: int = 500
    external_llm_enabled: bool = False
    """Send redacted candidate lines to the configured LLM provider. Off by default: real
    payslips must not reach an external provider without explicit authorization and an
    approved data-handling policy. Enable only for synthetic payslips."""


class PdfTextReader(Protocol):
    def read(self, data: bytes, config: PayslipImportConfig) -> DocumentText:
        """Synchronous, in-memory extraction. Raises `PayslipError` for unsupported input."""
        ...


class DraftStore(Protocol):
    async def add(self, draft: PayslipDraft, *, now: datetime, config: PayslipImportConfig) -> None:
        """Store a new draft after purging expired ones; may raise TOO_MANY_DRAFTS."""
        ...

    async def get(self, draft_id: UUID) -> PayslipDraft | None: ...

    async def replace(self, draft: PayslipDraft) -> None: ...

    async def remove(self, draft_id: UUID) -> None: ...

    def lock(self, draft_id: UUID) -> AbstractAsyncContextManager[None]:
        """Serializes state changes of one draft (corrections, approval, cancellation)."""
        ...


class HasToolContext(Protocol):
    @property
    def tool_context(self) -> ToolContext: ...


class ConversationBinding(Protocol):
    """Implemented by `ConversationService.turn_binding`: verifies that the actor owns the
    active conversation and returns the server-built, trusted `ToolContext`."""

    async def turn_binding(
        self, actor: ActorRef, conversation_id: UUID, *, trace_id: str | None = None
    ) -> HasToolContext: ...


class Clock(Protocol):
    def now(self) -> datetime: ...
