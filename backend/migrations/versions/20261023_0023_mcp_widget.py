"""E3.5 exposed MCP server (what a project offers to MCP clients) and the embeddable chat widget.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-23
"""

from __future__ import annotations

from alembic import op

from forge.core.rls import enable_project_rls
from forge.core.sqlscript import statements

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = """
        CREATE TABLE mcp_exposure (
            id            uuid PRIMARY KEY,
            project_id    uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            name          text NOT NULL CHECK (name ~ '^[A-Za-z0-9_-]{1,64}$'),
            description   text NOT NULL DEFAULT '' CHECK (char_length(description) <= 1000),
            agent_id      uuid REFERENCES agent(id) ON DELETE CASCADE,
            prompt_id     uuid REFERENCES prompt(id) ON DELETE CASCADE,
            collection_id uuid REFERENCES data_collection(id) ON DELETE CASCADE,
            enabled       boolean NOT NULL DEFAULT true,
            created_at    timestamptz NOT NULL DEFAULT now(),
            created_by    uuid REFERENCES app_user(id),
            CHECK (num_nonnulls(agent_id, prompt_id, collection_id) = 1)
        );
        CREATE UNIQUE INDEX mcp_exposure_name_key ON mcp_exposure (project_id, lower(name));
        CREATE UNIQUE INDEX mcp_exposure_agent_key ON mcp_exposure (agent_id)
            WHERE agent_id IS NOT NULL;
        CREATE UNIQUE INDEX mcp_exposure_prompt_key ON mcp_exposure (prompt_id)
            WHERE prompt_id IS NOT NULL;
        CREATE UNIQUE INDEX mcp_exposure_collection_key ON mcp_exposure (collection_id)
            WHERE collection_id IS NOT NULL;

        CREATE TABLE chat_widget (
            id                  uuid PRIMARY KEY,
            project_id          uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            agent_id            uuid NOT NULL REFERENCES agent(id) ON DELETE CASCADE,
            name                text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
            public_key          text NOT NULL UNIQUE,
            title               text NOT NULL DEFAULT '' CHECK (char_length(title) <= 80),
            welcome             text NOT NULL DEFAULT '' CHECK (char_length(welcome) <= 500),
            allowed_origins     text[] NOT NULL DEFAULT '{}',
            enabled             boolean NOT NULL DEFAULT true,
            messages_per_minute int NOT NULL DEFAULT 10 CHECK (messages_per_minute BETWEEN 1 AND 600),
            daily_limit         int NOT NULL DEFAULT 1000 CHECK (daily_limit BETWEEN 1 AND 1000000),
            created_at          timestamptz NOT NULL DEFAULT now(),
            created_by          uuid NOT NULL REFERENCES app_user(id),
            updated_at          timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX chat_widget_name_key ON chat_widget (project_id, lower(name));

        CREATE TABLE widget_session (
            token_hash      bytea PRIMARY KEY,
            project_id      uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            widget_id       uuid NOT NULL REFERENCES chat_widget(id) ON DELETE CASCADE,
            conversation_id uuid NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
            created_at      timestamptz NOT NULL DEFAULT now(),
            last_seen_at    timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX widget_session_widget ON widget_session (widget_id, last_seen_at);

        ALTER TABLE conversation ADD COLUMN source text NOT NULL DEFAULT 'app'
            CHECK (source IN ('app', 'mcp', 'widget'));
    """
    for stmt in statements(sql):
        op.execute(stmt)
    for table in ("mcp_exposure", "chat_widget", "widget_session"):
        for stmt in statements(enable_project_rls(table)):
            op.execute(stmt)


def downgrade() -> None:
    op.execute("ALTER TABLE conversation DROP COLUMN IF EXISTS source")
    op.execute("DROP TABLE IF EXISTS widget_session, chat_widget, mcp_exposure CASCADE")
