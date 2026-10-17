"""Shared Python contracts between the Business Application, the AI Engine and the HTTP API.

STATUS: PROVISIONAL (0.1.0). Proposed by the Business developer in Step 0; NOT yet jointly
approved with the AI developer. Do not change independently; see docs/api-contracts.md.

Dependencies: pydantic and the Python standard library only (no FastAPI, no SQLAlchemy).
"""

from app.contracts.common import CONTRACT_STATUS, CONTRACT_VERSION

__all__ = ["CONTRACT_STATUS", "CONTRACT_VERSION"]
