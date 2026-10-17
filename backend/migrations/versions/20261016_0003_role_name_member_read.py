"""Let a user read the role names of their own memberships across projects.

Without it, "my projects" (membership rows visible via user_id) cannot join role_name, whose
tenant policy hides rows when no project is set. Idempotent and online-safe (policy swap only).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-16
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS role_name_tenant_read ON role_name")
    op.execute(
        """
        CREATE POLICY role_name_tenant_read ON role_name FOR SELECT USING (
            project_id = app_project_id()
            OR EXISTS (
                SELECT 1 FROM membership m
                WHERE m.role_name_id = role_name.id AND m.user_id = app_user_id()
            )
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS role_name_tenant_read ON role_name")
    op.execute(
        "CREATE POLICY role_name_tenant_read ON role_name FOR SELECT "
        "USING (project_id = app_project_id())"
    )
