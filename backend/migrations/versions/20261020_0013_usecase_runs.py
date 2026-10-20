"""M2.3b Use case runs: one row per run with the graph it executed, one row per executed step
(docs/requirements/phase-2.md, E2.6).

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-20
"""

from __future__ import annotations

from alembic import op

from forge.core.rls import enable_project_rls
from forge.core.sqlscript import statements

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def run(sql: str) -> None:
    for stmt in statements(sql):
        op.execute(stmt)


def upgrade() -> None:
    run(
        """
        CREATE TABLE use_case_run (
            id               uuid PRIMARY KEY,
            project_id       uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            use_case_id      uuid NOT NULL REFERENCES use_case(id),
            use_case_name    text NOT NULL,
            version          int,
            mode             text NOT NULL CHECK (mode IN ('test', 'published')),
            graph            jsonb NOT NULL,
            inputs           jsonb NOT NULL DEFAULT '{}',
            outputs          jsonb,
            status           text NOT NULL DEFAULT 'queued'
                             CHECK (status IN ('queued', 'running', 'waiting_approval',
                                               'succeeded', 'failed', 'cancelled')),
            error_code       text,
            error            text,
            error_node       text,
            limits           jsonb NOT NULL DEFAULT '{}',
            steps            int NOT NULL DEFAULT 0,
            tokens_in        bigint NOT NULL DEFAULT 0,
            tokens_out       bigint NOT NULL DEFAULT 0,
            cost             numeric(18, 8) NOT NULL DEFAULT 0,
            trace_id         text,
            workflow_id      text,
            cancel_requested boolean NOT NULL DEFAULT false,
            created_at       timestamp with time zone NOT NULL DEFAULT now(),
            created_by       uuid REFERENCES app_user(id),
            started_at       timestamp with time zone,
            finished_at      timestamp with time zone
        );
        CREATE INDEX use_case_run_uc ON use_case_run (use_case_id, created_at DESC);
        CREATE INDEX use_case_run_project ON use_case_run (project_id, created_at DESC);

        -- One row per node execution; `step_key` carries the loop iterations
        -- (`each[2]/story`), so it is stable across retries and replays.
        CREATE TABLE use_case_run_step (
            run_id        uuid NOT NULL REFERENCES use_case_run(id) ON DELETE CASCADE,
            step_key      text NOT NULL,
            project_id    uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            ordinal       bigserial,
            node_id       text NOT NULL,
            node_type     text NOT NULL,
            ref           text,
            status        text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
            input         jsonb,
            output        jsonb,
            error_code    text,
            error         text,
            agent_run_id  uuid,
            tokens_in     int NOT NULL DEFAULT 0,
            tokens_out    int NOT NULL DEFAULT 0,
            cost          numeric(18, 8) NOT NULL DEFAULT 0,
            latency_ms    int NOT NULL DEFAULT 0,
            started_at    timestamp with time zone NOT NULL DEFAULT now(),
            finished_at   timestamp with time zone,
            PRIMARY KEY (run_id, step_key)
        );
        """
    )
    for table in ("use_case_run", "use_case_run_step"):
        run(enable_project_rls(table))


def downgrade() -> None:
    run("DROP TABLE IF EXISTS use_case_run_step, use_case_run;")
