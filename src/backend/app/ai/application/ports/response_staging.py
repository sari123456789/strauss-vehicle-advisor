"""Response staging (AI-internal port, migration 0003).

A turn's assistant messages are staged durably, under the input claim, before the graph
checkpoint is written. The checkpoint then holds only `MessageRef`s (message ID + content hash)
- never the text, which may contain calculated amounts. `ConversationStore.complete_input`
publishes exactly the staged response whose manifest matches the checkpoint's references,
together with completing the input, in one AI transaction.

Staged text is conversation content kept only for durable recovery; it is not a Business data
store: there are no structured amount columns and Business remains authoritative.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class MessageRef:
    message_id: str
    content_sha256: str


@dataclass(frozen=True)
class StagedMessage:
    message_id: str
    kind: str
    text: str
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def ref(self) -> MessageRef:
        return MessageRef(self.message_id, content_sha256(self))


def content_sha256(message: StagedMessage) -> str:
    """Hash of everything that is published for a message (ID, kind, text, payload)."""
    canonical = json.dumps(
        {
            "messageId": message.message_id,
            "kind": message.kind,
            "text": message.text,
            "payload": message.payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def manifest_sha256(refs: Sequence[MessageRef]) -> str:
    """Hash of the ordered response (an empty response has a manifest too)."""
    canonical = json.dumps([[r.message_id, r.content_sha256] for r in refs], separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@runtime_checkable
class ResponseStager(Protocol):
    async def stage(self, input_id: str, messages: Sequence[StagedMessage]) -> None:
        """Durably stage the complete response for the current claim (fenced, idempotent).
        Raises `StaleClaimError` if the claim no longer owns the input."""
        ...
