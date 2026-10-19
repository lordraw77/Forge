"""M1.4 RAG on upload: data collections, documents, knowledge bases, chunks, ingestion,
pipelines with versions and tags, runs; member ACL groups (docs/architecture/m1.4-rag.md).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-19
"""

from __future__ import annotations

from alembic import op

from forge.core.rls import enable_project_rls
from forge.core.sqlscript import statements

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def run(sql: str) -> None:
    for stmt in statements(sql):
        op.execute(stmt)


def upgrade() -> None:
    run(
        """
        -- ACL groups of a member, matched against document ACLs at retrieval (US-1.16.2).
        ALTER TABLE membership ADD COLUMN acl_groups text[] NOT NULL DEFAULT '{}';

        CREATE TABLE data_collection (
            id           uuid PRIMARY KEY,
            project_id   uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            name         text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
            description  text NOT NULL DEFAULT '' CHECK (char_length(description) <= 2000),
            created_at   timestamptz NOT NULL DEFAULT now(),
            created_by   uuid REFERENCES app_user(id),
            updated_at   timestamptz NOT NULL DEFAULT now(),
            updated_by   uuid REFERENCES app_user(id)
        );
        CREATE UNIQUE INDEX data_collection_name_key ON data_collection (project_id, lower(name));

        CREATE TABLE rag_document (
            id             uuid PRIMARY KEY,
            project_id     uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            collection_id  uuid NOT NULL REFERENCES data_collection(id) ON DELETE CASCADE,
            name           text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 255),
            path           text NOT NULL DEFAULT '' CHECK (char_length(path) <= 1000),
            mime_type      text NOT NULL,
            kind           text NOT NULL,
            size_bytes     bigint NOT NULL CHECK (size_bytes >= 0),
            sha256         bytea NOT NULL,
            object_key     text NOT NULL,
            acl_groups     text[] NOT NULL DEFAULT '{}',
            status         text NOT NULL DEFAULT 'pending'
                           CHECK (status IN ('pending', 'processing', 'ready', 'failed')),
            error          text,
            failed_at      timestamptz,
            pages          int,
            parsed_key     text,
            created_at     timestamptz NOT NULL DEFAULT now(),
            created_by     uuid REFERENCES app_user(id),
            updated_at     timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX rag_document_path_key ON rag_document (collection_id, path, name);
        CREATE INDEX rag_document_collection ON rag_document (collection_id, created_at DESC);

        -- (collection, embedding model, chunking) -> one set of chunks; pipelines share it.
        CREATE TABLE knowledge_base (
            id               uuid PRIMARY KEY,
            project_id       uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            collection_id    uuid NOT NULL REFERENCES data_collection(id) ON DELETE CASCADE,
            embedding_model  text NOT NULL,
            dims             int NOT NULL CHECK (dims BETWEEN 1 AND 4000),
            chunk_size       int NOT NULL CHECK (chunk_size BETWEEN 50 AND 4000),
            chunk_overlap    int NOT NULL CHECK (chunk_overlap >= 0),
            node_parser      text NOT NULL CHECK (node_parser IN ('structure', 'recursive', 'fixed')),
            status           text NOT NULL DEFAULT 'building'
                             CHECK (status IN ('building', 'ready', 'failed')),
            created_at       timestamptz NOT NULL DEFAULT now(),
            created_by       uuid REFERENCES app_user(id),
            ready_at         timestamptz,
            CHECK (chunk_overlap < chunk_size)
        );
        CREATE UNIQUE INDEX knowledge_base_config_key ON knowledge_base
            (collection_id, lower(embedding_model), chunk_size, chunk_overlap, node_parser);

        -- One row per (document, knowledge base): chunk, embed, index (E1.15).
        CREATE TABLE rag_ingestion (
            document_id  uuid NOT NULL REFERENCES rag_document(id) ON DELETE CASCADE,
            kb_id        uuid NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
            project_id   uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            status       text NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'processing', 'ready', 'failed')),
            error        text,
            chunks       int NOT NULL DEFAULT 0,
            embedded     int NOT NULL DEFAULT 0,
            reused       int NOT NULL DEFAULT 0,
            started_at   timestamptz,
            finished_at  timestamptz,
            PRIMARY KEY (document_id, kb_id)
        );
        CREATE INDEX rag_ingestion_kb ON rag_ingestion (kb_id, status);

        CREATE TABLE rag_chunk (
            id            uuid PRIMARY KEY,
            project_id    uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            kb_id         uuid NOT NULL REFERENCES knowledge_base(id) ON DELETE CASCADE,
            document_id   uuid NOT NULL REFERENCES rag_document(id) ON DELETE CASCADE,
            ordinal       int NOT NULL,
            content       text NOT NULL,
            heading_path  text NOT NULL DEFAULT '',
            page          int,
            metadata      jsonb NOT NULL DEFAULT '{}',
            acl_groups    text[] NOT NULL,
            content_hash  bytea NOT NULL,
            token_count   int NOT NULL,
            embedding     vector NOT NULL
        );
        CREATE INDEX rag_chunk_document ON rag_chunk (kb_id, document_id, ordinal);
        CREATE INDEX rag_chunk_hash ON rag_chunk (kb_id, content_hash);

        CREATE TABLE rag_pipeline (
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
        CREATE UNIQUE INDEX rag_pipeline_name_key ON rag_pipeline (project_id, lower(name));

        CREATE TABLE rag_pipeline_version (
            id           uuid PRIMARY KEY,
            project_id   uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            pipeline_id  uuid NOT NULL REFERENCES rag_pipeline(id) ON DELETE CASCADE,
            version      int NOT NULL CHECK (version > 0),
            config       jsonb NOT NULL,
            kb_id        uuid NOT NULL REFERENCES knowledge_base(id),
            note         text NOT NULL DEFAULT '',
            created_at   timestamptz NOT NULL DEFAULT now(),
            created_by   uuid REFERENCES app_user(id),
            UNIQUE (pipeline_id, version)
        );
        CREATE INDEX rag_pipeline_version_kb ON rag_pipeline_version (kb_id);

        CREATE TABLE rag_pipeline_tag (
            pipeline_id  uuid NOT NULL REFERENCES rag_pipeline(id) ON DELETE CASCADE,
            project_id   uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            tag          text NOT NULL CHECK (tag IN ('draft', 'staging', 'prod')),
            version      int NOT NULL,
            updated_at   timestamptz NOT NULL DEFAULT now(),
            updated_by   uuid REFERENCES app_user(id),
            PRIMARY KEY (pipeline_id, tag),
            FOREIGN KEY (pipeline_id, version) REFERENCES rag_pipeline_version (pipeline_id, version)
        );

        CREATE TABLE rag_run (
            id            uuid PRIMARY KEY,
            project_id    uuid NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            pipeline_id   uuid REFERENCES rag_pipeline(id) ON DELETE CASCADE,
            version       int,
            kb_id         uuid,
            query         text NOT NULL,
            status        text NOT NULL CHECK (status IN ('ok', 'no_context', 'error')),
            error_code    text,
            error         text,
            answer        text,
            citations     jsonb NOT NULL DEFAULT '[]',
            retrieval     jsonb NOT NULL DEFAULT '{}',
            messages      jsonb NOT NULL DEFAULT '[]',
            model_name    text,
            tokens_in     int NOT NULL DEFAULT 0,
            tokens_out    int NOT NULL DEFAULT 0,
            cost          numeric(18, 8) NOT NULL DEFAULT 0,
            latency_ms    int NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            created_by    uuid REFERENCES app_user(id)
        );
        CREATE INDEX rag_run_pipeline_time ON rag_run (pipeline_id, created_at DESC);
        """
    )
    for table in (
        "data_collection",
        "rag_document",
        "knowledge_base",
        "rag_ingestion",
        "rag_chunk",
        "rag_pipeline",
        "rag_pipeline_version",
        "rag_pipeline_tag",
        "rag_run",
    ):
        run(enable_project_rls(table))
    op.execute(
        "REVOKE UPDATE, DELETE, TRUNCATE ON rag_pipeline_version FROM forge_app, forge_admin"
    )
    op.execute("REVOKE UPDATE, TRUNCATE ON rag_run FROM forge_app, forge_admin")

    # Partial HNSW index per knowledge base (ADR-008, US-1.15.2). The application role does not
    # own `rag_chunk`, so a reviewed SECURITY DEFINER function creates the index for a knowledge
    # base of the caller's project only. The expression matches the retriever's
    # `embedding::halfvec(<dims>)` cast so the planner can use it. Indexes of deleted knowledge
    # bases match no rows; dropping them is index maintenance (E2.13), never an app action.
    run(
        """
        CREATE FUNCTION rag_kb_index(p_kb uuid)
        RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS
        $$
        DECLARE
            v_dims int;
        BEGIN
            -- FORCE ROW LEVEL SECURITY applies to the owner too: only the caller's project.
            SELECT dims INTO v_dims FROM knowledge_base
             WHERE id = p_kb AND project_id = app_project_id();
            IF v_dims IS NULL THEN
                RAISE EXCEPTION 'knowledge base % not found in this project', p_kb;
            END IF;
            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS %I ON rag_chunk USING hnsw '
                '((embedding::halfvec(%s)) halfvec_cosine_ops) '
                'WITH (m = 16, ef_construction = 128) WHERE kb_id = %L',
                'rag_chunk_kb_' || replace(p_kb::text, '-', ''), v_dims, p_kb);
        END $$;
        REVOKE ALL ON FUNCTION rag_kb_index(uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION rag_kb_index(uuid) TO forge_app, forge_admin;
        """
    )


def downgrade() -> None:
    run(
        """
        DROP FUNCTION IF EXISTS rag_kb_index(uuid);
        DROP TABLE IF EXISTS rag_run, rag_pipeline_tag, rag_pipeline_version, rag_pipeline,
            rag_chunk, rag_ingestion, knowledge_base, rag_document, data_collection CASCADE;
        ALTER TABLE membership DROP COLUMN IF EXISTS acl_groups;
        """
    )
