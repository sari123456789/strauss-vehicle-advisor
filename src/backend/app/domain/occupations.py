"""Occupation policy data (stable identifiers, decision R6)."""

import re
from dataclasses import dataclass
from decimal import Decimal

from app.domain.errors import InvalidValueError
from app.domain.rules import EntitlementGroup
from app.domain.values import require_non_negative

_OCCUPATION_ID = re.compile(r"^OCC-[A-Z0-9-]{2,40}$")


@dataclass(frozen=True, slots=True)
class OccupationPolicy:
    occupation_id: str
    name_he: str
    group: EntitlementGroup
    mandatory_car: bool
    waiver_allowed: bool
    waiver_allowance: Decimal

    def __post_init__(self) -> None:
        if not _OCCUPATION_ID.fullmatch(self.occupation_id):
            raise InvalidValueError(f"invalid occupation id {self.occupation_id!r}")
        require_non_negative(self.waiver_allowance, "waiverAllowance")
        if self.mandatory_car and self.waiver_allowed:
            raise InvalidValueError("a mandatory-car occupation cannot allow a waiver")
