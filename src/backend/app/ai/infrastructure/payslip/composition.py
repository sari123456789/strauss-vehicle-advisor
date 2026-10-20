"""`build_payslip_service`: composes the payslip import from EXISTING runtime services.

Called once by the shared lifespan (integration stage). It creates no second Business service,
authentication or conversation store: it receives the application's `ConversationService`
(trusted `ToolContext` + ownership), its `BusinessToolsPort` and, optionally, its LLM port.
"""

from dataclasses import replace
from datetime import UTC, datetime

from app.ai.application.payslip.ports import Clock, ConversationBinding, PayslipImportConfig
from app.ai.application.payslip.service import PayslipImportService
from app.ai.application.ports.llm import LlmPort
from app.ai.infrastructure.payslip.memory_store import InMemoryDraftStore
from app.ai.infrastructure.payslip.pdf_reader import PypdfTextReader
from app.contracts.business_port import BusinessToolsPort


class UtcClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def build_payslip_service(
    *,
    conversations: ConversationBinding,
    business: BusinessToolsPort,
    llm: LlmPort | None = None,
    external_llm_enabled: bool = False,
    clock: Clock | None = None,
    config: PayslipImportConfig | None = None,
) -> PayslipImportService:
    """`external_llm_enabled` must come from settings and stays False unless an approved
    data-handling policy allows sending (redacted) payslip lines to the provider."""
    effective = replace(
        config or PayslipImportConfig(),
        external_llm_enabled=external_llm_enabled and llm is not None,
    )
    return PayslipImportService(
        reader=PypdfTextReader(),
        store=InMemoryDraftStore(),  # single-process demo store (see memory_store.py)
        conversations=conversations,
        business=business,
        clock=clock or UtcClock(),
        llm=llm,
        config=effective,
    )
