"""HTTP-only DTOs of the Business API (authentication). PROVISIONAL - joint review pending.

Business operations reuse the shared contract models unchanged; only the authentication
exchange needs its own shapes. The refresh token is never in a body - only in an HttpOnly
cookie.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints

from app.contracts.common import ContractModel
from app.contracts.tool_context import Role


class LoginRequest(ContractModel):
    login: Annotated[str, StringConstraints(min_length=2, max_length=64)]
    password: str = Field(min_length=1, max_length=256, repr=False)


class ActorView(ContractModel):
    subject_id: UUID
    roles: list[Role]
    display_name: str | None = None


class SessionResponse(ContractModel):
    access_token: str = Field(repr=False)
    token_type: Literal["Bearer"] = "Bearer"
    expires_at: datetime
    actor: ActorView
