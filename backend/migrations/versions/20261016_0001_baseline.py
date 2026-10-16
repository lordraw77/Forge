"""Baseline: tenant context functions and grants for the RLS-bound app role (ADR-002).

Roles and extensions are created by the database init script (they need superuser):
`forge_app` (NOLOGIN, no BYPASSRLS) and `forge_admin` (NOLOGIN, BYPASSRLS), both granted to
the owner role used in FORGE_DATABASE_URL.

Revision ID: 0001
Revises:
Create Date: 2026-10-16
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # NULL when unset or empty, so policies deny by default instead of raising.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_project_id() RETURNS uuid
        LANGUAGE sql STABLE PARALLEL SAFE AS
        $$ SELECT NULLIF(current_setting('app.project_id', true), '')::uuid $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_user_id() RETURNS uuid
        LANGUAGE sql STABLE PARALLEL SAFE AS
        $$ SELECT NULLIF(current_setting('app.user_id', true), '')::uuid $$
        """
    )

    op.execute("GRANT USAGE ON SCHEMA public TO forge_app, forge_admin")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public"
        " GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO forge_app, forge_admin"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public"
        " GRANT USAGE, SELECT ON SEQUENCES TO forge_app, forge_admin"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION app_project_id(), app_user_id() TO forge_app, forge_admin"
    )


def downgrade() -> None:
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public"
        " REVOKE USAGE, SELECT ON SEQUENCES FROM forge_app, forge_admin"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public"
        " REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM forge_app, forge_admin"
    )
    op.execute("DROP FUNCTION IF EXISTS app_user_id()")
    op.execute("DROP FUNCTION IF EXISTS app_project_id()")
