"""Read the supplied rules JSON and the derived demo policy JSON (UTF-8, exact Decimals, SHA-256).

Reading only: no interpretation of free-text notes. Source files are never modified.
"""

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any


class RulesFormatError(Exception):
    """A rules document is unreadable or structurally invalid."""


@dataclass(frozen=True, slots=True)
class JsonDocument:
    path: Path
    sha256: str
    content: dict[str, Any]


def read_json_document(path: Path) -> JsonDocument:
    return parse_json_document(path, path.read_bytes())


def parse_json_document(path: Path, raw: bytes) -> JsonDocument:
    """Parse already-read bytes (e.g. verified against the source manifest)."""
    try:
        text = raw.decode("utf-8")
        content = json.loads(text, parse_float=Decimal, parse_int=int)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RulesFormatError(f"{path.name}: not valid UTF-8 JSON ({error})") from error
    if not isinstance(content, dict):
        raise RulesFormatError(f"{path.name}: top-level JSON value must be an object")
    return JsonDocument(path=path, sha256=hashlib.sha256(raw).hexdigest(), content=content)
