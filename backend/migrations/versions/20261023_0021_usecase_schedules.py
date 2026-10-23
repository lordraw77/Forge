"""E3.9 use case triggers: schedules (cron or interval) and inbound trigger tokens.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-23
"""

from __future__ import annotations

from alembic import op

from forge.core.rls import enable_project_rls
from forge.core.sqlscript import statements

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = """
        CREATE TABLE use_case_trigger (
            id               uuid PRIMARY KEY,
            project_id       uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            use_case_id      uuid NOT NULL REFERENCES use_case(id) ON DELETE CASCADE,
            name             text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
            kind             text NOT NULL CHECK (kind IN ('cron', 'interval', 'webhook')),
            cron             text,
            interval_minutes int CHECK (interval_minutes BETWEEN 1 AND 525600),
            timezone         text NOT NULL DEFAULT 'UTC',
            token_hash       bytea,
            inputs           jsonb NOT NULL DEFAULT '{}',
            webhook_url      text,
            enabled          boolean NOT NULL DEFAULT true,
            next_run_at      timestamptz,
            last_run_at      timestamptz,
            last_run_id      uuid,
            last_status      text,
            last_error       text,
            failures         int NOT NULL DEFAULT 0,
            created_at       timestamptz NOT NULL DEFAULT now(),
            created_by       uuid NOT NULL REFERENCES app_user(id),
            updated_at       timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX use_case_trigger_name_key
            ON use_case_trigger (project_id, use_case_id, lower(name));
        CREATE UNIQUE INDEX use_case_trigger_token_key
            ON use_case_trigger (token_hash) WHERE token_hash IS NOT NULL;
        CREATE INDEX use_case_trigger_due
            ON use_case_trigger (next_run_at) WHERE enabled AND next_run_at IS NOT NULL;
    """
    for stmt in statements(sql):
        op.execute(stmt)
    for stmt in statements(enable_project_rls("use_case_trigger")):
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS use_case_trigger CASCADE")
