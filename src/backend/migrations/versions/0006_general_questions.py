"""General questions to the representative — a text question with a text answer.

Revision ID: 0006_general_questions
Revises: 0005_request_assignments
Create Date: 2026-10-22

Widens two CHECK constraints of `business.representative_requests` with one value each:
- `field_code`: adds 'general.question';
- `expected_value_type`: adds 'text'.

Existing rows and the other values are unchanged. A general question is never applied to a
case fact (the answer service skips it), so no fact-validation function changes.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_general_questions"
down_revision: str | None = "0005_request_assignments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "business"
TABLE = "representative_requests"

FIELD_CODES = (
    "'alternative.participation', 'occupation.waiverPolicy', "
    "'employee.entitlementGroup', 'currentArrangement.propulsion', "
    "'currentArrangement.vehicle'"
)
VALUE_TYPES = "'money', 'entitlement_group', 'propulsion', 'tax_key', 'boolean'"


def _replace(name: str, condition: str) -> None:
    # Raw SQL keeps the exact existing constraint names (no naming-convention prefixing).
    constraint = f"ck_{TABLE}_{name}"
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} DROP CONSTRAINT {constraint}")
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} ADD CONSTRAINT {constraint} CHECK ({condition})")


def upgrade() -> None:
    _replace("field_code", f"field_code IN ({FIELD_CODES}, 'general.question')")
    _replace("expected_value_type", f"expected_value_type IN ({VALUE_TYPES}, 'text')")


def downgrade() -> None:
    # Rows are never deleted: the downgrade fails if general questions exist.
    _replace("field_code", f"field_code IN ({FIELD_CODES})")
    _replace("expected_value_type", f"expected_value_type IN ({VALUE_TYPES})")
