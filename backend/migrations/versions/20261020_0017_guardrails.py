"""M2.5 Guardrails: the per-project policy and the intervention log
(docs/requirements/phase-2.md, E2.11).

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-20
"""

from __future__ import annotations

from alembic import op

from forge.core.rls import enable_project_rls
from forge.core.sqlscript import statements

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def run(sql: str) -> None:
    for stmt in statements(sql):
        op.execute(stmt)


def upgrade() -> None:
    run(
        """
        -- One policy per project: an ordered list of rules (type, stage, action, params).
        CREATE TABLE guardrail_policy (
            project_id  uuid PRIMARY KEY REFERENCES project(id) ON DELETE CASCADE,
            enabled     boolean NOT NULL DEFAULT true,
            rules       jsonb NOT NULL DEFAULT '[]',
            updated_at  timestamp with time zone NOT NULL DEFAULT now(),
            updated_by  uuid REFERENCES app_user(id)
        );

        -- What a rule did. The text itself is never stored, only the labels and positions.
        CREATE TABLE guardrail_event (
            id          uuid PRIMARY KEY,
            project_id  uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            created_at  timestamp with time zone NOT NULL DEFAULT now(),
            stage       text NOT NULL CHECK (stage IN ('input', 'output', 'tool_args')),
            source      text NOT NULL,
            rule_index  integer NOT NULL,
            check_type  text NOT NULL,
            action      text NOT NULL,
            label       text NOT NULL DEFAULT '',
            detail      text NOT NULL DEFAULT '',
            matches     integer NOT NULL DEFAULT 0,
            user_id     uuid REFERENCES app_user(id),
            api_key_id  uuid,
            trace_id    text
        );
        CREATE INDEX guardrail_event_recent ON guardrail_event (project_id, created_at DESC);
        """
    )
    for table in ("guardrail_policy", "guardrail_event"):
        run(enable_project_rls(table))


def downgrade() -> None:
    run("DROP TABLE IF EXISTS guardrail_event, guardrail_policy;")
