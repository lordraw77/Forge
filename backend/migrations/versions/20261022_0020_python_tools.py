"""E3.4: Python tools (sandbox) as a tool kind.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-22
"""

from __future__ import annotations

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tool DROP CONSTRAINT IF EXISTS tool_kind_check")
    op.execute(
        "ALTER TABLE tool ADD CONSTRAINT tool_kind_check "
        "CHECK (kind IN ('http', 'sql', 'mcp', 'python'))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM tool WHERE kind = 'python'")
    op.execute("ALTER TABLE tool DROP CONSTRAINT IF EXISTS tool_kind_check")
    op.execute(
        "ALTER TABLE tool ADD CONSTRAINT tool_kind_check CHECK (kind IN ('http', 'sql', 'mcp'))"
    )
