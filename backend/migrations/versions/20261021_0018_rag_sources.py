"""M2.6b Scheduled RAG sources: connector definitions, the items they brought in, and sync runs
(docs/requirements/phase-2.md, E2.13).

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-21
"""

from __future__ import annotations

from alembic import op

from forge.core.rls import enable_project_rls
from forge.core.sqlscript import statements

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def run(sql: str) -> None:
    for stmt in statements(sql):
        op.execute(stmt)


def upgrade() -> None:
    run(
        """
        CREATE TABLE rag_source (
            id               uuid PRIMARY KEY,
            project_id       uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            collection_id    uuid NOT NULL REFERENCES data_collection(id) ON DELETE CASCADE,
            name             text NOT NULL,
            type             text NOT NULL CHECK (type IN
                                 ('s3', 'url', 'rest', 'sql', 'git', 'filesystem')),
            config           jsonb NOT NULL DEFAULT '{}',
            secret_id        uuid,
            interval_minutes integer CHECK (interval_minutes IS NULL OR interval_minutes >= 5),
            enabled          boolean NOT NULL DEFAULT true,
            acl_groups       text[] NOT NULL DEFAULT '{}',
            delete_missing   boolean NOT NULL DEFAULT true,
            status           text NOT NULL DEFAULT 'idle' CHECK (status IN ('idle', 'running')),
            next_run_at      timestamp with time zone,
            last_run_at      timestamp with time zone,
            last_status      text,
            last_error       text,
            created_at       timestamp with time zone NOT NULL DEFAULT now(),
            created_by       uuid REFERENCES app_user(id),
            updated_at       timestamp with time zone NOT NULL DEFAULT now(),
            UNIQUE (project_id, name)
        );
        CREATE INDEX rag_source_due ON rag_source (next_run_at) WHERE enabled;
        CREATE INDEX rag_source_collection ON rag_source (collection_id);

        -- What a source brought in: external id -> document, and the version seen last time.
        CREATE TABLE rag_source_item (
            source_id    uuid NOT NULL REFERENCES rag_source(id) ON DELETE CASCADE,
            external_id  text NOT NULL,
            project_id   uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            document_id  uuid NOT NULL REFERENCES rag_document(id) ON DELETE CASCADE,
            version      text NOT NULL,
            seen_run     uuid,
            PRIMARY KEY (source_id, external_id)
        );
        CREATE INDEX rag_source_item_document ON rag_source_item (document_id);

        CREATE TABLE rag_source_run (
            id           uuid PRIMARY KEY,
            project_id   uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            source_id    uuid NOT NULL REFERENCES rag_source(id) ON DELETE CASCADE,
            trigger      text NOT NULL,
            status       text NOT NULL DEFAULT 'running'
                             CHECK (status IN ('running', 'success', 'partial', 'failed')),
            started_at   timestamp with time zone NOT NULL DEFAULT now(),
            finished_at  timestamp with time zone,
            added        integer NOT NULL DEFAULT 0,
            updated      integer NOT NULL DEFAULT 0,
            unchanged    integer NOT NULL DEFAULT 0,
            deleted      integer NOT NULL DEFAULT 0,
            skipped      integer NOT NULL DEFAULT 0,
            failed       integer NOT NULL DEFAULT 0,
            message      text,
            errors       jsonb NOT NULL DEFAULT '[]'
        );
        CREATE INDEX rag_source_run_recent ON rag_source_run (source_id, started_at DESC);
        """
    )
    for table in ("rag_source", "rag_source_item", "rag_source_run"):
        run(enable_project_rls(table))


def downgrade() -> None:
    run("DROP TABLE IF EXISTS rag_source_run, rag_source_item, rag_source;")
