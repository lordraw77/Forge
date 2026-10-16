# ADR-008 – Vector and full-text search on PostgreSQL

- Status: Accepted
- Date: 2026-10-16

## Context

The spec's RAG design uses one `rag_chunk` table with pgvector HNSW and ParadeDB pg_search BM25, RLS on `project_id`, ACL filtering, and a fixed dimension per knowledge base. Target: hybrid retrieval < 300 ms p95 on 1M chunks.

## Decision

- PostgreSQL 16+ image with pgvector ≥ 0.8 and pg_search (ParadeDB image in Compose/Helm).
- `rag_chunk.embedding` is untyped `vector` (or `halfvec` for models with > 2000 dims, up to 4000). Dimension is validated at insert against the KB.
- One partial HNSW index per knowledge base, created by the ingestion workflow when the KB is created:

  ```sql
  CREATE INDEX CONCURRENTLY rag_chunk_hnsw_<kb> ON rag_chunk
    USING hnsw ((embedding::vector(1024)) vector_cosine_ops)
    WITH (m = 16, ef_construction = 128)
    WHERE kb_id = '<kb>';
  ```

  Queries use the same cast expression and `kb_id` predicate so the planner picks the partial index.
- Per query: `SET LOCAL hnsw.ef_search = 80; SET LOCAL hnsw.iterative_scan = relaxed_order;` so ACL/RLS filters do not starve results.
- BM25: one pg_search index on `(id, content, heading_path)` for the whole table; filtered by `kb_id` and ACL.
- Retriever interface `Retriever.search(kb, query, filters, k)` with implementations `PgVectorRetriever`, `PgSearchBm25Retriever`, `PgNativeFtsRetriever` (fallback), and `HybridRetriever` (RRF k = 60).
- ACL: `acl_groups && :user_groups OR acl_groups = '{}'`.
- Changing a KB's embedding model creates a new index generation and re-embeds; the switch is atomic after completion.
- Scale-out path: `PARTITION BY LIST (project_id)` for large projects, or pgvectorscale.
- **Open**: pg_search is AGPL-3.0; acceptability for Forge's distribution model awaits the PO (analysis question 8). Native FTS remains available behind the same interface if it is rejected.

## Consequences

- Single database, transactional consistency between documents and chunks.
- Phase 0 spike E0.5b measures p95 at 1M chunks with RLS and ACL filters before the design is frozen.
