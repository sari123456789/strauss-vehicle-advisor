"""ORM tables of the `auth` schema: identities (3A), password hashes and refresh sessions (4D).

Only hashes are stored: never a password or a raw refresh token.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKeyConstraint, Index, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.business.infrastructure.database.models.base import Base

AUTH_SCHEMA = "auth"


class UserRow(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("login", name="uq_users_login"),
        {"schema": AUTH_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    login: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    roles: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


USERS_ID = f"{AUTH_SCHEMA}.users.id"


class CredentialRow(Base):
    __tablename__ = "credentials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"], [USERS_ID], name="fk_credentials_user_id", ondelete="RESTRICT"
        ),
        {"schema": AUTH_SCHEMA},
    )

    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RefreshSessionRow(Base):
    __tablename__ = "refresh_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"], [USERS_ID], name="fk_refresh_sessions_user_id", ondelete="RESTRICT"
        ),
        UniqueConstraint("token_hash", name="uq_refresh_sessions_token_hash"),
        Index("ix_refresh_sessions_family_id", "family_id"),
        {"schema": AUTH_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    family_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
