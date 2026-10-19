"""M2.2 Tools: tool library with immutable versions, credentials, egress policy, data sources,
MCP servers and the tool call log (docs/requirements/phase-2.md, M2.2).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-19
"""

from __future__ import annotations

from alembic import op

from forge.core.rls import enable_project_rls
from forge.core.sqlscript import statements

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def run(sql: str) -> None:
    for stmt in statements(sql):
        op.execute(stmt)


def upgrade() -> None:
    run(
        """
        CREATE TABLE tool_policy (
            project_id     uuid PRIMARY KEY REFERENCES project(id) ON DELETE CASCADE,
            allowed_hosts  text[] NOT NULL DEFAULT '{}',
            updated_at     timestamp with time zone NOT NULL DEFAULT now(),
            updated_by     uuid REFERENCES app_user(id)
        );

        CREATE TABLE credential (
            id          uuid PRIMARY KEY,
            project_id  uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            name        text NOT NULL CHECK (name ~ '^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$'),
            kind        text NOT NULL CHECK (kind IN ('bearer', 'basic', 'header', 'query')),
            param_name  text,
            username    text,
            secret_id   uuid NOT NULL REFERENCES secret(id),
            created_at  timestamp with time zone NOT NULL DEFAULT now(),
            created_by  uuid REFERENCES app_user(id)
        );
        CREATE UNIQUE INDEX credential_name_key ON credential (project_id, lower(name));

        CREATE TABLE datasource (
            id          uuid PRIMARY KEY,
            project_id  uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            name        text NOT NULL CHECK (name ~ '^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$'),
            kind        text NOT NULL DEFAULT 'postgres' CHECK (kind IN ('postgres')),
            host        text NOT NULL,
            port        int NOT NULL DEFAULT 5432 CHECK (port BETWEEN 1 AND 65535),
            database    text NOT NULL,
            username    text NOT NULL,
            ssl_mode    text NOT NULL DEFAULT 'require'
                        CHECK (ssl_mode IN ('disable', 'require', 'verify-full')),
            secret_id   uuid NOT NULL REFERENCES secret(id),
            created_at  timestamp with time zone NOT NULL DEFAULT now(),
            created_by  uuid REFERENCES app_user(id)
        );
        CREATE UNIQUE INDEX datasource_name_key ON datasource (project_id, lower(name));

        CREATE TABLE mcp_server (
            id             uuid PRIMARY KEY,
            project_id     uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            name           text NOT NULL CHECK (name ~ '^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$'),
            transport      text NOT NULL CHECK (transport IN ('http', 'sse', 'stdio')),
            url            text,
            command        text,
            args           text[] NOT NULL DEFAULT '{}',
            credential_id  uuid REFERENCES credential(id),
            enabled        boolean NOT NULL DEFAULT true,
            created_at     timestamp with time zone NOT NULL DEFAULT now(),
            created_by     uuid REFERENCES app_user(id)
        );
        CREATE UNIQUE INDEX mcp_server_name_key ON mcp_server (project_id, lower(name));

        CREATE TABLE tool (
            id                 uuid PRIMARY KEY,
            project_id         uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            name               text NOT NULL CHECK (name ~ '^[a-z][a-z0-9_]{0,47}$'),
            kind               text NOT NULL CHECK (kind IN ('http', 'sql', 'mcp')),
            description        text NOT NULL DEFAULT '',
            latest_version     int NOT NULL DEFAULT 0,
            published_version  int,
            created_at         timestamp with time zone NOT NULL DEFAULT now(),
            created_by         uuid REFERENCES app_user(id),
            updated_at         timestamp with time zone NOT NULL DEFAULT now(),
            updated_by         uuid REFERENCES app_user(id)
        );
        CREATE UNIQUE INDEX tool_name_key ON tool (project_id, name);

        -- Insert-only, like prompt, pipeline and agent versions.
        CREATE TABLE tool_version (
            id                 uuid PRIMARY KEY,
            project_id         uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            tool_id            uuid NOT NULL REFERENCES tool(id) ON DELETE CASCADE,
            version            int NOT NULL CHECK (version > 0),
            description        text NOT NULL,
            config             jsonb NOT NULL,
            schema_in          jsonb NOT NULL,
            timeout_s          int NOT NULL DEFAULT 15 CHECK (timeout_s BETWEEN 1 AND 60),
            requires_approval  boolean NOT NULL DEFAULT false,
            writes             boolean NOT NULL DEFAULT false,
            note               text NOT NULL DEFAULT '',
            created_at         timestamp with time zone NOT NULL DEFAULT now(),
            created_by         uuid REFERENCES app_user(id),
            UNIQUE (tool_id, version)
        );

        CREATE TABLE tool_call (
            id           uuid PRIMARY KEY,
            project_id   uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            tool_id      uuid REFERENCES tool(id) ON DELETE SET NULL,
            tool_name    text NOT NULL,
            version      int,
            source       text NOT NULL CHECK (source IN ('test', 'agent')),
            run_id       uuid,
            user_id      uuid,
            status       text NOT NULL CHECK (status IN ('ok', 'error')),
            error_code   text,
            input        jsonb,
            output       jsonb,
            latency_ms   int NOT NULL DEFAULT 0,
            created_at   timestamp with time zone NOT NULL DEFAULT now()
        );
        CREATE INDEX tool_call_project_time ON tool_call (project_id, created_at DESC);
        CREATE INDEX tool_call_tool ON tool_call (tool_id, created_at DESC);
        """
    )
    for table in (
        "tool_policy",
        "credential",
        "datasource",
        "mcp_server",
        "tool",
        "tool_version",
        "tool_call",
    ):
        run(enable_project_rls(table))


def downgrade() -> None:
    run(
        """
        DROP TABLE IF EXISTS tool_call, tool_version, tool, mcp_server, datasource, credential,
            tool_policy;
        """
    )
