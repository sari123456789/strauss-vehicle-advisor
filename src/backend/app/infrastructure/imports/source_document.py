"""Read a supplied source file once and verify it against `data/source-manifest.json`.

The manifest records the SHA-256 of each approved source edition. A different hash means a
different (unapproved) edition: the import fails until the manifest is updated explicitly.
Source files are only read, never modified.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.application.ports.reference_data import SourceFingerprint


class SourceIntegrityError(Exception):
    """A source file is missing, unreadable or differs from its manifest entry."""

    code = "SOURCE_INTEGRITY"


@dataclass(frozen=True, slots=True)
class SourceDocument:
    fingerprint: SourceFingerprint
    raw: bytes


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    path: str
    kind: str
    sha256: str
    size_bytes: int


class SourceManifest:
    def __init__(self, repo_root: Path, entries: dict[str, ManifestEntry]) -> None:
        self.repo_root = repo_root
        self._by_kind = entries

    @classmethod
    def load(cls, repo_root: Path, manifest_path: Path) -> "SourceManifest":
        try:
            content: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
            entries = {
                item["kind"]: ManifestEntry(
                    path=item["path"],
                    kind=item["kind"],
                    sha256=item["sha256"],
                    size_bytes=item["sizeBytes"],
                )
                for item in content["sources"]
            }
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise SourceIntegrityError(f"source manifest is unreadable: {error}") from error
        return cls(repo_root, entries)

    def entry(self, kind: str) -> ManifestEntry:
        try:
            return self._by_kind[kind]
        except KeyError:
            raise SourceIntegrityError(f"source manifest has no entry of kind {kind!r}") from None

    def read(self, kind: str) -> SourceDocument:
        entry = self.entry(kind)
        return read_verified(self.repo_root / entry.path, entry)


def read_verified(path: Path, entry: ManifestEntry) -> SourceDocument:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise SourceIntegrityError(f"{entry.path}: cannot read source file ({error})") from error
    digest = hashlib.sha256(raw).hexdigest()
    if digest != entry.sha256 or len(raw) != entry.size_bytes:
        raise SourceIntegrityError(
            f"{entry.path}: content differs from the manifest (sha256 {digest}, expected "
            f"{entry.sha256}); a new source edition requires an explicit manifest update"
        )
    return SourceDocument(
        fingerprint=SourceFingerprint(
            kind=entry.kind, path=entry.path, sha256=digest, size_bytes=len(raw)
        ),
        raw=raw,
    )
