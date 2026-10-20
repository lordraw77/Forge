"""M2.3d End-user runs: sessions, favorites, files (inputs, requirements, outputs) and the
webhook of asynchronous runs (docs/requirements/phase-2.md, E2.9).

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-20
"""

from __future__ import annotations

from alembic import op

from forge.core.rls import enable_project_rls
from forge.core.sqlscript import statements

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def run(sql: str) -> None:
    for stmt in statements(sql):
        op.execute(stmt)


def upgrade() -> None:
    run(
        """
        CREATE TABLE use_case_session (
            id          uuid PRIMARY KEY,
            project_id  uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            use_case_id uuid NOT NULL REFERENCES use_case(id),
            title       text NOT NULL DEFAULT '',
            created_by  uuid REFERENCES app_user(id),
            created_at  timestamp with time zone NOT NULL DEFAULT now(),
            updated_at  timestamp with time zone NOT NULL DEFAULT now()
        );
        CREATE INDEX use_case_session_user
            ON use_case_session (project_id, created_by, updated_at DESC);

        CREATE TABLE use_case_favorite (
            project_id  uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            user_id     uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
            use_case_id uuid NOT NULL REFERENCES use_case(id) ON DELETE CASCADE,
            created_at  timestamp with time zone NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, use_case_id)
        );

        -- `input` files are uploaded before a run and referenced by the form; `requirements` is
        -- the optional attachment of a use case; `output` files are generated when a run ends.
        CREATE TABLE use_case_file (
            id           uuid PRIMARY KEY,
            project_id   uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            kind         text NOT NULL CHECK (kind IN ('input', 'requirements', 'output')),
            use_case_id  uuid REFERENCES use_case(id),
            run_id       uuid REFERENCES use_case_run(id) ON DELETE CASCADE,
            name         text NOT NULL,
            content_type text NOT NULL,
            size         bigint NOT NULL,
            sha256       text NOT NULL,
            storage_key  text NOT NULL,
            created_by   uuid REFERENCES app_user(id),
            created_at   timestamp with time zone NOT NULL DEFAULT now()
        );
        CREATE INDEX use_case_file_run ON use_case_file (run_id) WHERE run_id IS NOT NULL;
        CREATE INDEX use_case_file_uc ON use_case_file (use_case_id) WHERE use_case_id IS NOT NULL;

        ALTER TABLE use_case_run
            ADD COLUMN session_id uuid REFERENCES use_case_session(id),
            ADD COLUMN webhook_url text,
            ADD COLUMN webhook_status text
                CHECK (webhook_status IN ('pending', 'delivered', 'failed')),
            ADD COLUMN webhook_error text;
        CREATE INDEX use_case_run_session ON use_case_run (session_id) WHERE session_id IS NOT NULL;
        """
    )
    for table in ("use_case_session", "use_case_favorite", "use_case_file"):
        run(enable_project_rls(table))


def downgrade() -> None:
    run(
        """
        ALTER TABLE use_case_run
            DROP COLUMN session_id, DROP COLUMN webhook_url,
            DROP COLUMN webhook_status, DROP COLUMN webhook_error;
        DROP TABLE IF EXISTS use_case_file, use_case_favorite, use_case_session;
        """
    )
