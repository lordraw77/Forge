"""M2.1 External identity: LDAP/OIDC providers, group-to-Role-Name mappings, external users and
mapped memberships (docs/requirements/phase-2.md, ADR-003).

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-19
"""

from __future__ import annotations

from alembic import op

from forge.core.rls import enable_project_rls
from forge.core.sqlscript import statements

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def run(sql: str) -> None:
    for stmt in statements(sql):
        op.execute(stmt)


def upgrade() -> None:
    run(
        """
        CREATE TABLE identity_provider (
            id          uuid PRIMARY KEY,
            kind        text NOT NULL CHECK (kind IN ('ldap', 'oidc')),
            name        text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
            enabled     boolean NOT NULL DEFAULT false,
            position    int NOT NULL DEFAULT 0,
            config      jsonb NOT NULL DEFAULT '{}',
            secret_id   uuid REFERENCES secret(id),
            last_sync_at  timestamptz,
            last_sync     jsonb,
            created_at  timestamptz NOT NULL DEFAULT now(),
            created_by  uuid REFERENCES app_user(id),
            updated_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX identity_provider_name_key ON identity_provider (lower(name));

        ALTER TABLE app_user
            ADD COLUMN provider_id uuid REFERENCES identity_provider(id) ON DELETE RESTRICT,
            ADD COLUMN external_id text,
            ADD COLUMN external_groups text[] NOT NULL DEFAULT '{}';
        CREATE UNIQUE INDEX app_user_external_key ON app_user (provider_id, external_id)
            WHERE provider_id IS NOT NULL;

        ALTER TABLE membership
            ADD COLUMN source text NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'mapped'));

        CREATE TABLE group_mapping (
            id             uuid PRIMARY KEY,
            provider_id    uuid NOT NULL REFERENCES identity_provider(id) ON DELETE CASCADE,
            project_id     uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            role_name_id   uuid NOT NULL REFERENCES role_name(id) ON DELETE CASCADE,
            external_group text NOT NULL CHECK (char_length(external_group) BETWEEN 1 AND 500),
            created_at     timestamptz NOT NULL DEFAULT now(),
            created_by     uuid REFERENCES app_user(id),
            UNIQUE (provider_id, external_group, project_id, role_name_id)
        );
        CREATE INDEX group_mapping_provider ON group_mapping (provider_id);
        """
    )
    # Mappings are managed by the Super Admin through forge_admin; RLS keeps project code out.
    run(enable_project_rls("group_mapping"))


def downgrade() -> None:
    run(
        """
        DROP TABLE IF EXISTS group_mapping;
        ALTER TABLE membership DROP COLUMN IF EXISTS source;
        DROP INDEX IF EXISTS app_user_external_key;
        ALTER TABLE app_user DROP COLUMN IF EXISTS external_groups,
            DROP COLUMN IF EXISTS external_id, DROP COLUMN IF EXISTS provider_id;
        DROP TABLE IF EXISTS identity_provider;
        """
    )
