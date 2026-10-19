# ADR-008 – Vector and full-text search on PostgreSQL

- Status: Accepted; **BM25 part superseded by [ADR-016](016-external-bm25-opensearch.md) (2026-10-18)**
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
  Default to `halfvec(<dims>)` with `halfvec_cosine_ops` for every dimension size: it halves index and table memory, and spike E0.5b measured recall@10 0.997 with it.
- Per query: `SET LOCAL hnsw.ef_search = 80; SET LOCAL hnsw.iterative_scan = relaxed_order;` so ACL/RLS filters do not starve results.
- BM25: one pg_search index on `(id, content, heading_path, kb_id, project_id, (acl_groups::pdb.literal))` for the whole table, so tenant, KB and ACL filters are pushed into the index and its top-k scan is kept. Query syntax (pg_search 0.26): `content ||| :query AND kb_id = :kb AND acl_groups === :groups::text[] ORDER BY pdb.score(id) DESC LIMIT 40`; no `key_field` (deprecated); `=== ANY(...)` is broken in 0.26.2.
- Retriever interface `Retriever.search(kb, query, filters, k)` with implementations `PgVectorRetriever`, `PgSearchBm25Retriever`, `PgNativeFtsRetriever` (fallback), and `HybridRetriever` (RRF k = 60).
- ACL: project-wide chunks store the sentinel `{__public__}` (an empty array indexes no BM25 term); the user's groups always include `__public__`. Vector path: `acl_groups && :groups`; BM25 path: `acl_groups === :groups`.
- Parallel index builds need shared memory: the database container gets `shm_size: 2gb` (Compose) or a memory-backed `/dev/shm` volume (Helm); builds use `maintenance_work_mem` ≈ 1.5 GB and 3 workers.
- Changing a KB's embedding model creates a new index generation and re-embeds; the switch is atomic after completion.
- Scale-out path: `PARTITION BY LIST (project_id)` for large projects, or pgvectorscale.
- **Resolved (2026-10-18):** the PO rejected AGPL; BM25 moves to OpenSearch ([ADR-016](016-external-bm25-opensearch.md)). The pg_search notes below are kept for history; vector search is unchanged.

## Implementation notes (M1.4, 2026-10-19)

- A **knowledge base** is the set of chunks of one data collection for one (embedding model, chunk size, overlap, node parser). The model is pinned to a concrete model name at creation; pipelines with the same parameters share it ([M1.4 design](../architecture/m1.4-rag.md#2-data-model-migration-0006_rag)).
- The partial HNSW index is created by the SECURITY DEFINER function `rag_kb_index(kb)`, because the application role does not own `rag_chunk`; indexes of deleted knowledge bases are left for index maintenance (E2.13).
- Without a reranker the similarity threshold applies to cosine similarity.

## Consequences

- Single database, transactional consistency between documents and chunks.
- Validated by spike E0.5b on 2026-10-16: hybrid p95 39 ms at 1M chunks (384 dims) and 22 ms at 250k chunks (1024 dims), ANN recall@10 0.997 under filters ([report](../spikes/e05b-hybrid-retrieval.md)). Sizing: about 8 GB of RAM cache per million 1024-dim chunks to keep the HNSW index resident.
