"""E3.6 long-term memory of agents: facts remembered per agent and user, on pgvector.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-23
"""

from __future__ import annotations

from alembic import op

from forge.core.rls import enable_project_rls
from forge.core.sqlscript import statements

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = """
        CREATE TABLE agent_memory (
            id             uuid PRIMARY KEY,
            project_id     uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            agent_id       uuid NOT NULL REFERENCES agent(id) ON DELETE CASCADE,
            user_id        uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
            content        text NOT NULL CHECK (char_length(content) BETWEEN 1 AND 600),
            embedding      vector NOT NULL,
            dims           int NOT NULL,
            source_run_id  uuid,
            created_at     timestamptz NOT NULL DEFAULT now(),
            last_used_at   timestamptz NOT NULL DEFAULT now(),
            use_count      int NOT NULL DEFAULT 0
        );
        CREATE INDEX agent_memory_owner ON agent_memory (agent_id, user_id, last_used_at DESC);
    """
    for stmt in statements(sql):
        op.execute(stmt)
    for stmt in statements(enable_project_rls("agent_memory")):
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_memory CASCADE")
