"""Conversations the employee removed from their list.

Revision ID: 0004_conversation_hidden
Revises: 0003_response_staging
Create Date: 2026-10-22

- `conversations.hidden_at`: set when the employee deletes a conversation from their list.
  The conversation and its history are kept (a representative answer or a Business case may
  still refer to it); it is only left out of the owner's list.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_conversation_hidden"
down_revision: str | None = "0003_response_staging"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "agent"


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("conversations", "hidden_at", schema=SCHEMA)
