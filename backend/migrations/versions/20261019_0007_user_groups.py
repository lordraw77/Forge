"""Project user groups for document access (PO decision 2026-10-19: groups and roles).

Document ACLs reference groups (`group:<id>`) and project roles (`role:<role_name_id>`); the
free-text `membership.acl_groups` of 0006 is replaced by group membership.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-19
"""

from __future__ import annotations

from alembic import op

from forge.core.rls import enable_project_rls
from forge.core.sqlscript import statements

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def run(sql: str) -> None:
    for stmt in statements(sql):
        op.execute(stmt)


def upgrade() -> None:
    run(
        """
        CREATE TABLE user_group (
            id           uuid PRIMARY KEY,
            project_id   uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            name         text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
            description  text NOT NULL DEFAULT '' CHECK (char_length(description) <= 500),
            created_at   timestamptz NOT NULL DEFAULT now(),
            created_by   uuid REFERENCES app_user(id),
            updated_at   timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX user_group_name_key ON user_group (project_id, lower(name));

        CREATE TABLE user_group_member (
            group_id    uuid NOT NULL REFERENCES user_group(id) ON DELETE CASCADE,
            user_id     uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
            project_id  uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            created_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (group_id, user_id)
        );
        CREATE INDEX user_group_member_user ON user_group_member (project_id, user_id);

        -- Free-text ACL tags of 0006 (never released) give way to groups and roles.
        ALTER TABLE membership DROP COLUMN acl_groups;
        UPDATE rag_document SET acl_groups = '{}' WHERE acl_groups <> '{}';
        UPDATE rag_chunk SET acl_groups = '{__public__}' WHERE acl_groups <> '{__public__}';
        """
    )
    for table in ("user_group", "user_group_member"):
        run(enable_project_rls(table))
    # A member leaving the project leaves its groups too.
    run(
        """
        CREATE FUNCTION user_group_member_cleanup() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            DELETE FROM user_group_member
             WHERE project_id = OLD.project_id AND user_id = OLD.user_id;
            RETURN OLD;
        END $$;
        CREATE TRIGGER membership_group_cleanup AFTER DELETE ON membership
            FOR EACH ROW EXECUTE FUNCTION user_group_member_cleanup();
        """
    )


def downgrade() -> None:
    run(
        """
        DROP TRIGGER IF EXISTS membership_group_cleanup ON membership;
        DROP FUNCTION IF EXISTS user_group_member_cleanup();
        DROP TABLE IF EXISTS user_group_member, user_group CASCADE;
        ALTER TABLE membership ADD COLUMN acl_groups text[] NOT NULL DEFAULT '{}';
        """
    )
