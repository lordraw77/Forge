"""The MCP server gets its own permission, `mcp.invoke`, instead of sharing `usecase.run`
(docs/architecture/m3.5-mcp-widget.md): a token that may run use cases no longer reaches the
exposed agents and data by default, and the other way round.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-25
"""

from __future__ import annotations

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

# Who may call the project's MCP server: those who may already build or operate.
ROLES = ("project_admin", "builder", "operator")


def upgrade() -> None:
    keys = ",".join(f"'{k}'" for k in ROLES)
    op.execute(
        f"UPDATE role_type SET permissions = array_append(permissions, 'mcp.invoke') "
        f"WHERE key IN ({keys}) AND NOT ('mcp.invoke' = ANY(permissions))"
    )


def downgrade() -> None:
    op.execute("UPDATE role_type SET permissions = array_remove(permissions, 'mcp.invoke')")
