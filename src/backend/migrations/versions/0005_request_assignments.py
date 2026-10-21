"""Representative request assignments — claim/release/complete/cancel lifecycle.

Revision ID: 0005_request_assignments
Revises: 0004_representative_answers
Create Date: 2026-10-21

New table `business.request_assignments`:
- Tracks who has claimed (or previously claimed) a representative request.
- At most one row may have status='active' per request_id (partial unique index).
- All other statuses (released, completed, cancelled) have no uniqueness restriction,
  preserving full claim/release/re-claim history.
- status='cancelled' is terminal and can never become 'active' again (application-enforced).

Additive only: tables 0001–0004 are unchanged.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0005_request_assignments"
down_revision: str | None = "0004_representative_answers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "business"
AUTH = "auth"


def _uuid(name: str, nullable: bool = False) -> sa.Column[Any]:
    return sa.Column(name, UUID(as_uuid=True), nullable=nullable)


def _time(name: str, *, server_default: bool = False, nullable: bool = False) -> sa.Column[Any]:
    default = sa.func.now() if server_default else None
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, server_default=default)


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [target], name=f"fk_{table}_{column}", ondelete="RESTRICT"
    )


def upgrade() -> None:
    op.create_table(
        "request_assignments",
        _uuid("id"),
        _uuid("request_id"),
        _uuid("claimed_by"),
        _time("claimed_at"),
        sa.Column("assignment_version", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
        ),
        _time("released_at", nullable=True),
        _time("completed_at", nullable=True),
        _time("cancelled_at", nullable=True),
        _time("created_at", server_default=True),
        # primary key
        sa.PrimaryKeyConstraint("id", name="pk_request_assignments"),
        # foreign keys
        _fk(
            "request_assignments",
            "request_id",
            f"{SCHEMA}.representative_requests.id",
        ),
        _fk("request_assignments", "claimed_by", f"{AUTH}.users.id"),
        # check constraints
        sa.CheckConstraint(
            "status IN ('active', 'released', 'completed', 'cancelled')",
            name="ck_request_assignments_status",
        ),
        sa.CheckConstraint(
            "released_at IS NULL OR status IN ('released', 'completed')",
            name="ck_request_assignments_released_at",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR status = 'completed'",
            name="ck_request_assignments_completed_at",
        ),
        sa.CheckConstraint(
            "cancelled_at IS NULL OR status = 'cancelled'",
            name="ck_request_assignments_cancelled_at",
        ),
        schema=SCHEMA,
    )

    # Partial unique index: at most one active assignment per request.
    # NOTE: This is a partial index, NOT a named constraint; ON CONFLICT cannot reference it
    # by name. Application code uses SELECT FOR UPDATE + INSERT with IntegrityError handling.
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_request_assignments_one_active
            ON {SCHEMA}.request_assignments (request_id)
            WHERE status = 'active'
        """
    )

    # Supporting indexes for representative dashboard queries and counter aggregations.
    op.create_index(
        "ix_request_assignments_claimed_by_status",
        "request_assignments",
        ["claimed_by", "status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_request_assignments_request_id_status",
        "request_assignments",
        ["request_id", "status"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_request_assignments_request_id_status",
        table_name="request_assignments",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_request_assignments_claimed_by_status",
        table_name="request_assignments",
        schema=SCHEMA,
    )
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.uq_request_assignments_one_active")
    op.drop_table("request_assignments", schema=SCHEMA)
