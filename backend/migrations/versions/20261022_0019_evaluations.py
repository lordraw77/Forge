"""E3.2 evaluations: datasets, cases, runs and per-case results
(docs/architecture/m3.2-evaluations.md).

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-22
"""

from __future__ import annotations

from alembic import op

from forge.core.rls import enable_project_rls
from forge.core.sqlscript import statements

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def run(sql: str) -> None:
    for stmt in statements(sql):
        op.execute(stmt)


def upgrade() -> None:
    run(
        """
        CREATE TABLE eval_dataset (
            id          uuid PRIMARY KEY,
            project_id  uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            name        text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
            kind        text NOT NULL CHECK (kind IN ('prompt', 'rag')),
            description text NOT NULL DEFAULT '' CHECK (char_length(description) <= 2000),
            created_at  timestamptz NOT NULL DEFAULT now(),
            created_by  uuid REFERENCES app_user(id),
            updated_at  timestamptz NOT NULL DEFAULT now(),
            updated_by  uuid REFERENCES app_user(id)
        );
        CREATE UNIQUE INDEX eval_dataset_name_key ON eval_dataset (project_id, lower(name));

        CREATE TABLE eval_case (
            id               uuid PRIMARY KEY,
            project_id       uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            dataset_id       uuid NOT NULL REFERENCES eval_dataset(id) ON DELETE CASCADE,
            position         int NOT NULL,
            input            jsonb NOT NULL,
            expected         text,
            expected_sources jsonb NOT NULL DEFAULT '[]',
            UNIQUE (dataset_id, position)
        );

        CREATE TABLE eval_run (
            id             uuid PRIMARY KEY,
            project_id     uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            dataset_id     uuid REFERENCES eval_dataset(id) ON DELETE SET NULL,
            dataset_name   text NOT NULL,
            kind           text NOT NULL CHECK (kind IN ('prompt', 'rag')),
            name           text NOT NULL DEFAULT '',
            target_id      uuid NOT NULL,
            target_name    text NOT NULL,
            target_version int,
            model          text,
            evaluators     jsonb NOT NULL,
            status         text NOT NULL CHECK (status IN
                             ('queued', 'running', 'done', 'cancelled', 'interrupted')),
            total          int NOT NULL DEFAULT 0,
            completed      int NOT NULL DEFAULT 0,
            summary        jsonb NOT NULL DEFAULT '{}',
            created_at     timestamptz NOT NULL DEFAULT now(),
            created_by     uuid REFERENCES app_user(id),
            heartbeat_at   timestamptz NOT NULL DEFAULT now(),
            finished_at    timestamptz
        );
        CREATE INDEX eval_run_time ON eval_run (project_id, created_at DESC);

        CREATE TABLE eval_result (
            id          uuid PRIMARY KEY,
            project_id  uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            run_id      uuid NOT NULL REFERENCES eval_run(id) ON DELETE CASCADE,
            case_id     uuid,
            position    int NOT NULL,
            input       jsonb NOT NULL,
            expected    text,
            output      text,
            scores      jsonb NOT NULL DEFAULT '{}',
            passed      boolean NOT NULL DEFAULT false,
            error       text,
            tokens      int NOT NULL DEFAULT 0,
            cost        numeric(18, 8) NOT NULL DEFAULT 0,
            latency_ms  int NOT NULL DEFAULT 0,
            source_run  uuid,
            UNIQUE (run_id, position)
        );
        """
    )
    for table in ("eval_dataset", "eval_case", "eval_run", "eval_result"):
        run(enable_project_rls(table))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS eval_result, eval_run, eval_case, eval_dataset CASCADE")
