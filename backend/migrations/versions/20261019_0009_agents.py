"""M1.5 Single agent and tracing: agents with immutable versions, conversations, runs, steps,
tool-call log, feedback, and the LangGraph checkpoint schema (docs/architecture/m1.5-agent.md,
ADR-004).

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-19
"""

from __future__ import annotations

from alembic import op

from forge.core.rls import enable_project_rls
from forge.core.sqlscript import statements

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def run(sql: str) -> None:
    for stmt in statements(sql):
        op.execute(stmt)


def upgrade() -> None:
    run(
        """
        CREATE TABLE agent (
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
        CREATE UNIQUE INDEX agent_name_key ON agent (project_id, lower(name));
        CREATE INDEX agent_project_updated ON agent (project_id, updated_at DESC);

        -- Insert-only like prompt and pipeline versions (US-1.18.1 #1).
        CREATE TABLE agent_version (
            id              uuid PRIMARY KEY,
            project_id      uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            agent_id        uuid NOT NULL REFERENCES agent(id) ON DELETE CASCADE,
            version         int NOT NULL CHECK (version > 0),
            config          jsonb NOT NULL,
            prompt_id       uuid,
            prompt_version  int,
            note            text NOT NULL DEFAULT '',
            created_at      timestamptz NOT NULL DEFAULT now(),
            created_by      uuid REFERENCES app_user(id),
            UNIQUE (agent_id, version)
        );
        CREATE INDEX agent_version_prompt ON agent_version (project_id, prompt_id);

        CREATE TABLE agent_tag (
            agent_id    uuid NOT NULL REFERENCES agent(id) ON DELETE CASCADE,
            project_id  uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            tag         text NOT NULL CHECK (tag IN ('draft', 'staging', 'prod')),
            version     int NOT NULL,
            updated_at  timestamptz NOT NULL DEFAULT now(),
            updated_by  uuid REFERENCES app_user(id),
            PRIMARY KEY (agent_id, tag),
            FOREIGN KEY (agent_id, version) REFERENCES agent_version (agent_id, version)
        );

        CREATE TABLE conversation (
            id             uuid PRIMARY KEY,
            project_id     uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            agent_id       uuid NOT NULL REFERENCES agent(id) ON DELETE CASCADE,
            agent_version  int NOT NULL,
            title          text NOT NULL DEFAULT '' CHECK (char_length(title) <= 200),
            created_at     timestamptz NOT NULL DEFAULT now(),
            created_by     uuid REFERENCES app_user(id),
            updated_at     timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX conversation_user ON conversation (project_id, created_by, updated_at DESC);

        CREATE TABLE agent_run (
            id                uuid PRIMARY KEY,
            project_id        uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            conversation_id   uuid NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
            agent_id          uuid NOT NULL REFERENCES agent(id) ON DELETE CASCADE,
            agent_version     int NOT NULL,
            input             text NOT NULL,
            output            text,
            status            text NOT NULL DEFAULT 'queued'
                              CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
            error_code        text,
            error             text,
            citations         jsonb NOT NULL DEFAULT '[]',
            limits            jsonb NOT NULL DEFAULT '{}',
            tokens_in         int NOT NULL DEFAULT 0,
            tokens_out        int NOT NULL DEFAULT 0,
            cost              numeric(18, 8) NOT NULL DEFAULT 0,
            steps             int NOT NULL DEFAULT 0,
            latency_ms        int,
            trace_id          text,
            workflow_id       text,
            cancel_requested  boolean NOT NULL DEFAULT false,
            created_at        timestamptz NOT NULL DEFAULT now(),
            created_by        uuid REFERENCES app_user(id),
            started_at        timestamptz,
            finished_at       timestamptz
        );
        CREATE INDEX agent_run_project_time ON agent_run (project_id, created_at DESC);
        CREATE INDEX agent_run_conversation ON agent_run (conversation_id, created_at);
        CREATE INDEX agent_run_agent_time ON agent_run (agent_id, created_at DESC);

        CREATE TABLE run_step (
            run_id        uuid NOT NULL REFERENCES agent_run(id) ON DELETE CASCADE,
            ordinal       int NOT NULL,
            project_id    uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            kind          text NOT NULL CHECK (kind IN ('llm', 'tool')),
            name          text NOT NULL,
            status        text NOT NULL CHECK (status IN ('ok', 'error')),
            error_code    text,
            input         jsonb,
            output        jsonb,
            model_name    text,
            tokens_in     int NOT NULL DEFAULT 0,
            tokens_out    int NOT NULL DEFAULT 0,
            cost          numeric(18, 8) NOT NULL DEFAULT 0,
            latency_ms    int NOT NULL DEFAULT 0,
            ttft_ms       int,
            llm_call_id   uuid,
            tool_call_id  text,
            span_id       text,
            created_at    timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (run_id, ordinal)
        );

        -- ADR-004 rule 2: the key is "{thread_id}:{tool_call_id}"; a repeat returns the result.
        CREATE TABLE tool_call_log (
            idempotency_key  text PRIMARY KEY,
            project_id       uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            run_id           uuid NOT NULL REFERENCES agent_run(id) ON DELETE CASCADE,
            tool             text NOT NULL,
            result           jsonb NOT NULL,
            created_at       timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX tool_call_log_run ON tool_call_log (run_id);

        CREATE TABLE run_feedback (
            run_id      uuid NOT NULL REFERENCES agent_run(id) ON DELETE CASCADE,
            user_id     uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
            project_id  uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            rating      text NOT NULL CHECK (rating IN ('up', 'down')),
            comment     text NOT NULL DEFAULT '' CHECK (char_length(comment) <= 2000),
            created_at  timestamptz NOT NULL DEFAULT now(),
            updated_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (run_id, user_id)
        );
        """
    )
    for table in (
        "agent",
        "agent_version",
        "agent_tag",
        "conversation",
        "agent_run",
        "run_step",
        "tool_call_log",
        "run_feedback",
    ):
        run(enable_project_rls(table))
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON agent_version FROM forge_app, forge_admin")
    op.execute("REVOKE DELETE, TRUNCATE ON run_step, tool_call_log FROM forge_app, forge_admin")

    # LangGraph checkpoints (ADR-004): their own schema, no project_id; access is by thread id
    # ("run:<run_id>") derived from a run already authorised under RLS. The DDL is the
    # checkpointer's own, with plain (not CONCURRENTLY) indexes, which cannot run in a transaction.
    from langgraph.checkpoint.postgres.base import BasePostgresSaver

    op.execute("CREATE SCHEMA IF NOT EXISTS langgraph")
    op.execute("SET LOCAL search_path = langgraph")
    for ddl in BasePostgresSaver.MIGRATIONS:
        op.execute(ddl.replace("CONCURRENTLY ", ""))
    op.execute("SET LOCAL search_path = public")
    # The checkpointer records its own schema version row-by-row; mark them all applied.
    op.execute(
        "INSERT INTO langgraph.checkpoint_migrations (v) "
        f"SELECT g FROM generate_series(0, {len(BasePostgresSaver.MIGRATIONS) - 1}) g "
        "ON CONFLICT DO NOTHING"
    )
    op.execute("GRANT USAGE ON SCHEMA langgraph TO forge_app, forge_admin")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA langgraph "
        "TO forge_app, forge_admin"
    )


def downgrade() -> None:
    run(
        """
        DROP SCHEMA IF EXISTS langgraph CASCADE;
        DROP TABLE IF EXISTS run_feedback, tool_call_log, run_step, agent_run, conversation,
            agent_tag, agent_version, agent CASCADE;
        """
    )
