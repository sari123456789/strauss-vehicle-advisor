"""Authentication storage (Step 4D): password hashes and refresh sessions.

Revision ID: 0003_auth_credentials
Revises: 0002_business_cases
Create Date: 2026-10-18

- `auth.credentials`: one salted scrypt hash per user (`scrypt$...`); never a password.
- `auth.refresh_sessions`: SHA-256 digests of refresh tokens only; rotation marks the old row,
  logout or token reuse revokes the whole family. Rows are kept (no deletion) for audit.
- Additive only: 0001/0002 are unchanged.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0003_auth_credentials"
down_revision: str | None = "0002_business_cases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUTH = "auth"


def _uuid(name: str, **kwargs: Any) -> sa.Column[Any]:
    return sa.Column(name, UUID(as_uuid=True), **kwargs)


def _time(name: str, nullable: bool = False) -> sa.Column[Any]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "credentials",
        _uuid("user_id", nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        _time("updated_at"),
        sa.PrimaryKeyConstraint("user_id", name="pk_credentials"),
        sa.ForeignKeyConstraint(
            ["user_id"], [f"{AUTH}.users.id"], name="fk_credentials_user_id", ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            r"password_hash ~ '^scrypt\$[0-9]+\$[0-9]+\$[0-9]+\$[A-Za-z0-9_-]+\$[A-Za-z0-9_-]+$'",
            name="password_hash",
        ),
        schema=AUTH,
    )
    op.create_table(
        "refresh_sessions",
        _uuid("id", nullable=False),
        _uuid("family_id", nullable=False),
        _uuid("user_id", nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        _time("created_at"),
        _time("expires_at"),
        _time("rotated_at", nullable=True),
        _time("revoked_at", nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_refresh_sessions"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{AUTH}.users.id"],
            name="fk_refresh_sessions_user_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("token_hash", name="uq_refresh_sessions_token_hash"),
        sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash"),
        sa.CheckConstraint("expires_at > created_at", name="expiry"),
        schema=AUTH,
    )
    op.create_index("ix_refresh_sessions_family_id", "refresh_sessions", ["family_id"], schema=AUTH)


def downgrade() -> None:
    op.drop_index("ix_refresh_sessions_family_id", "refresh_sessions", schema=AUTH)
    op.drop_table("refresh_sessions", schema=AUTH)
    op.drop_table("credentials", schema=AUTH)
