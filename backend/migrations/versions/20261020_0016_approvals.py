"""M2.4 Human in the loop: approvals of use case runs and the one-time tokens of email links
(docs/requirements/phase-2.md, E2.10).

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-20
"""

from __future__ import annotations

from alembic import op

from forge.core.rls import enable_project_rls
from forge.core.sqlscript import statements

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def run(sql: str) -> None:
    for stmt in statements(sql):
        op.execute(stmt)


def upgrade() -> None:
    run(
        """
        CREATE TABLE approval (
            id            uuid PRIMARY KEY,
            project_id    uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            run_id        uuid NOT NULL REFERENCES use_case_run(id) ON DELETE CASCADE,
            step_key      text NOT NULL,
            node_id       text NOT NULL,
            kind          text NOT NULL CHECK (kind IN ('review', 'tool_call')),
            instructions  text NOT NULL DEFAULT '',
            content       jsonb NOT NULL,
            assignees     text[] NOT NULL DEFAULT '{}',
            status        text NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'approved', 'rejected', 'expired',
                                            'cancelled')),
            requested_by  uuid REFERENCES app_user(id),
            requested_at  timestamp with time zone NOT NULL DEFAULT now(),
            expires_at    timestamp with time zone NOT NULL,
            notified_at   timestamp with time zone,
            decided_by    uuid REFERENCES app_user(id),
            decided_at    timestamp with time zone,
            decided_via   text CHECK (decided_via IN ('ui', 'api', 'link')),
            edited        jsonb,
            comment       text,
            UNIQUE (run_id, step_key)
        );
        CREATE INDEX approval_pending ON approval (project_id, requested_at DESC)
            WHERE status = 'pending';
        CREATE INDEX approval_run ON approval (run_id);

        -- The token itself is never stored, only its SHA-256.
        CREATE TABLE approval_token (
            token_hash  text PRIMARY KEY,
            project_id  uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            approval_id uuid NOT NULL REFERENCES approval(id) ON DELETE CASCADE,
            user_id     uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
            expires_at  timestamp with time zone NOT NULL,
            used_at     timestamp with time zone
        );
        CREATE INDEX approval_token_approval ON approval_token (approval_id);
        """
    )
    for table in ("approval", "approval_token"):
        run(enable_project_rls(table))


def downgrade() -> None:
    run("DROP TABLE IF EXISTS approval_token, approval;")
