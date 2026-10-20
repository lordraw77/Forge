"""M2.3c Build Your Own: the natural-language requirements of a use case (max 4000 characters).

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-20
"""

from __future__ import annotations

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE use_case ADD COLUMN requirements text NOT NULL DEFAULT '' "
        "CHECK (char_length(requirements) <= 4000)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE use_case DROP COLUMN requirements")
