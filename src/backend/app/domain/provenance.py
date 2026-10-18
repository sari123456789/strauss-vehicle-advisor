"""Business provenance and field status. Missing is `value=None, status=MISSING` - never zero."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Generic, TypeVar
from uuid import UUID

from app.domain.errors import InvalidValueError

T = TypeVar("T")


class FieldStatus(StrEnum):
    MISSING = "missing"
    NEEDS_CLARIFICATION = "needs_clarification"
    USABLE = "usable"


class SourceType(StrEnum):
    EMPLOYEE = "employee"
    CATALOGUE = "catalogue"
    RULES = "rules"
    TAX_DATASET = "tax_dataset"
    REPRESENTATIVE = "representative"


@dataclass(frozen=True, slots=True)
class FieldValue(Generic[T]):
    value: T | None = None
    status: FieldStatus = FieldStatus.MISSING
    source: SourceType | None = None
    source_message_id: UUID | None = None
    updated_at: datetime | None = None
    revision: int = 0
    last_confirmed_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.status is FieldStatus.MISSING and self.value is not None:
            raise InvalidValueError("a missing field must not carry a value")
        if self.status is FieldStatus.USABLE and self.value is None:
            raise InvalidValueError("a usable field requires a value")
        if self.revision < 0:
            raise InvalidValueError("revision must be non-negative")

    @classmethod
    def missing(cls) -> "FieldValue[T]":
        return cls()

    @classmethod
    def usable(cls, value: T, source: SourceType) -> "FieldValue[T]":
        return cls(value=value, status=FieldStatus.USABLE, source=source)

    @property
    def is_usable(self) -> bool:
        return self.status is FieldStatus.USABLE
