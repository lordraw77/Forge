"""M1.3 Prompt Studio: prompts, immutable versions, tags, test/playground/assist runs
(docs/architecture/m1.3-prompt-studio.md).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-18
"""

from __future__ import annotations

from alembic import op

from forge.core.rls import enable_project_rls
from forge.core.sqlscript import statements

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def run(sql: str) -> None:
    for stmt in statements(sql):
        op.execute(stmt)


def upgrade() -> None:
    run(
        """
        CREATE TABLE prompt (
            id              uuid PRIMARY KEY,
            project_id      uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            name            text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
            description     text NOT NULL DEFAULT '' CHECK (char_length(description) <= 2000),
            latest_version  int NOT NULL DEFAULT 0,
            created_at      timestamptz NOT NULL DEFAULT now(),
            created_by      uuid REFERENCES app_user(id),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            updated_by      uuid REFERENCES app_user(id)
        );
        CREATE UNIQUE INDEX prompt_name_key ON prompt (project_id, lower(name));
        CREATE INDEX prompt_project_updated ON prompt (project_id, updated_at DESC);

        -- Every save is a new row; the application roles may only insert (US-1.10.2).
        CREATE TABLE prompt_version (
            id               uuid PRIMARY KEY,
            project_id       uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            prompt_id        uuid NOT NULL REFERENCES prompt(id) ON DELETE CASCADE,
            version          int NOT NULL CHECK (version > 0),
            model            text NOT NULL,
            system_template  text NOT NULL DEFAULT '',
            user_template    text NOT NULL DEFAULT '',
            variables        jsonb NOT NULL DEFAULT '[]',
            examples         jsonb NOT NULL DEFAULT '[]',
            model_params     jsonb NOT NULL DEFAULT '{}',
            note             text NOT NULL DEFAULT '',
            created_at       timestamptz NOT NULL DEFAULT now(),
            created_by       uuid REFERENCES app_user(id),
            UNIQUE (prompt_id, version)
        );

        -- A tag points at one version of its prompt; `prod` set = Published.
        CREATE TABLE prompt_tag (
            prompt_id   uuid NOT NULL REFERENCES prompt(id) ON DELETE CASCADE,
            project_id  uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            tag         text NOT NULL CHECK (tag IN ('draft', 'staging', 'prod')),
            version     int NOT NULL,
            updated_at  timestamptz NOT NULL DEFAULT now(),
            updated_by  uuid REFERENCES app_user(id),
            PRIMARY KEY (prompt_id, tag),
            FOREIGN KEY (prompt_id, version) REFERENCES prompt_version (prompt_id, version)
        );

        -- Test, playground and Help Me Write executions with their trace (US-1.11.1 #2).
        CREATE TABLE prompt_run (
            id           uuid PRIMARY KEY,
            project_id   uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            prompt_id    uuid REFERENCES prompt(id) ON DELETE CASCADE,
            version      int,
            kind         text NOT NULL CHECK (kind IN ('test', 'playground', 'assist')),
            group_id     uuid,
            model        text NOT NULL,
            model_name   text,
            status       text NOT NULL CHECK (status IN ('ok', 'error')),
            error_code   text,
            error        text,
            variables    jsonb NOT NULL DEFAULT '{}',
            messages     jsonb NOT NULL DEFAULT '[]',
            params       jsonb NOT NULL DEFAULT '{}',
            output       text,
            finish_reason text,
            tokens_in    int NOT NULL DEFAULT 0,
            tokens_out   int NOT NULL DEFAULT 0,
            cost         numeric(18, 8) NOT NULL DEFAULT 0,
            latency_ms   int NOT NULL,
            cache_hit    boolean NOT NULL DEFAULT false,
            llm_call_id  uuid,
            created_at   timestamptz NOT NULL DEFAULT now(),
            created_by   uuid REFERENCES app_user(id)
        );
        CREATE INDEX prompt_run_prompt_time ON prompt_run (prompt_id, created_at DESC);
        CREATE INDEX prompt_run_group ON prompt_run (group_id) WHERE group_id IS NOT NULL;
        """
    )
    for table in ("prompt", "prompt_version", "prompt_tag", "prompt_run"):
        run(enable_project_rls(table))
    # Immutable versions: deletes happen only through the cascade from `prompt`
    # (referential actions run as the table owner).
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON prompt_version FROM forge_app, forge_admin")
    op.execute("REVOKE UPDATE, TRUNCATE ON prompt_run FROM forge_app, forge_admin")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS prompt_run, prompt_tag, prompt_version, prompt CASCADE")
