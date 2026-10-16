# Spike E0.5b – Hybrid retrieval on PostgreSQL (ADR-008)

_Date: 2026-10-16. Code: [backend/spikes/e05b/bench.py](../../backend/spikes/e05b/bench.py). Raw results: [run A](e05b-run-a-1m-384d.json), [run B](e05b-run-b-250k-1024d.json). Run: `make spike/e05b`._

## Question

Does the ADR-008 design meet the NFR **"hybrid retrieval under 300 ms p95 on 1M chunks"**? The design is a single `rag_chunk` table with a partial HNSW index per knowledge base, a pg_search BM25 index, RRF fusion, forced RLS and ACL filtering. As in the NFR, the time spent in external models (query embedding, reranker) is excluded.

**Result: yes.** Hybrid p95 was **39 ms at 1M chunks** and 22 ms at 250k chunks of 1024 dimensions, with ANN recall@10 of 0.997 under filters. Getting there required three design changes to ADR-008, described below.

## Setup

- **Host:** 4 vCPU and 7.5 GB RAM, shared with the rest of the stack, which was stopped during the runs. PostgreSQL 17 (ParadeDB 0.26.2, pgvector 0.8.6) ran with `shared_buffers` at 1.9 GB.
- **Data:** synthetic but structured.
  - Vectors are clustered (1,000 centroids plus noise), like real embeddings, rather than uniform random vectors, which would be HNSW's worst case.
  - Text has 60 words per chunk: Zipf-distributed common words plus cluster-specific topic words.
  - 20% of chunks carry one ACL group.
  - A second project adds 10% extra rows, so RLS has rows to filter.
- **Queries:** run as `forge_app` with `app.project_id` set per transaction (RLS active), `hnsw.ef_search = 80` and `hnsw.iterative_scan = relaxed_order`.
  - Each query has a vector near a random cluster, 2 topic words plus 1 common word, and 3 user groups.
  - 600 queries per method: 100 for warm-up and 500 measured, sequentially from one client.
- **Hybrid query:** vector top 40 and BM25 top 40, fused with RRF (k = 60) and returning the top 40, in a single SQL statement.

The machine cannot hold 1M × 1024-dim vectors in memory (about 4.5 GB of data plus index), so the benchmark ran twice:

| Run | Chunks in the KB | Total rows | Dims | Purpose |
| --- | --- | --- | --- | --- |
| A | 1,000,000 | 1,100,000 | 384 | Scale: row count, BM25, RLS, filters |
| B | 250,000 | 275,000 | 1024 (bge-m3) | Realistic vector size |

## Results

| Metric | Run A (1M × 384) | Run B (250k × 1024) | Target |
| --- | --- | --- | --- |
| **Hybrid RRF p95** | **39.4 ms** | **21.6 ms** | < 300 ms |
| Hybrid RRF p50 / p99 | 9.9 / 81.6 ms | 9.0 / 48.5 ms | – |
| Vector top 40 p50 / p95 | 9.6 / 13.2 ms | 2.0 / 2.8 ms | – |
| BM25 top 40 p50 / p95 | 14.5 / 39.5 ms | 5.1 / 11.9 ms | – |
| ANN recall@10 with filters (HNSW vs exact) | 0.997 | 0.997 | – |
| Results always complete (40 rows) | yes | yes | – |
| HNSW build (m = 16, ef_construction = 128, 3 workers) | 230 s | 68 s | – |
| BM25 build | 34 s | 6.6 s | – |
| Table / HNSW / BM25 size | 1.7 GB / 1.1 GB / 0.3 GB | 0.9 GB / 0.65 GB / 0.08 GB | – |

Notes:

- **Vector search is slower in run A than in run B** despite the smaller vectors. Run A's index and heap (2.8 GB) exceed `shared_buffers`, so part of each query hits disk. Run B fits in cache. A production server sized to keep the index in RAM should do better than run A.
- **The hybrid p50 in run A is lower than BM25 alone** because the hybrid query was measured last, with warmer caches. The p95 values are comparable.
- **Tail latency (p99 of 80–90 ms) comes from BM25 queries containing very common words.** It is still well under target. Stop-word handling and capping match-any terms are tuning options.
- Ingestion throughput was not measured here; it is covered by E1.15.

## Findings and design changes

### 1. ACL must be pushed into the BM25 index

- **Problem:** with `(acl_groups = '{}' OR acl_groups && :groups)`, pg_search applies the ACL after the index. That disables its top-k scan, so every match gets scored and then filtered. In the 25k-row smoke run this put BM25 p95 at 29 ms, and the cost grows with match count.
- **Fix:**
  - Index `acl_groups` with the literal tokenizer: `(acl_groups::pdb.literal)`.
  - Filter with `acl_groups === :groups::text[]`. The result set was identical to the `&&` reference.
  - Represent project-wide visibility with the sentinel **`__public__`** instead of `'{}'`, because an empty array indexes no term.
  - Always include `__public__` in the user's groups.
- **Result:** BM25 p95 at 25k rows dropped from 29 ms to 5 ms, with a top-k scan and no heap filter.
- **Also note:** `=== ANY(ARRAY[…])` fails in pg_search 0.26.2 with `failed to decode CBOR`. Use `=== ARRAY[…]`.

### 2. No literal `project_id` predicate on BM25 queries; index `project_id` instead

| Variant (RLS active) | Plan |
| --- | --- |
| `project_id` not in the BM25 index | RLS check becomes a per-row `heap_filter` |
| `project_id` in the index, RLS only | Top-k inside the index, no heap filter ✅ |
| Plus `AND project_id = '<literal uuid>'` | **Planner abandons the BM25 scan**: btree plus a per-row search function ❌ |
| Plus `AND project_id = app_project_id()` | Top-k inside the index ✅ |

This changes ADR-002's rule that "queries add explicit `project_id` filters". For BM25 queries, either rely on RLS or use `app_project_id()`, never a literal value. A literal predicate remains fine for btree and HNSW queries.

### 3. pg_search 0.26 syntax

- `key_field` is deprecated and ignored.
- `USING bm25` and `USING paradedb` both work.
- Current operators are `|||` (match any) and `===` (term), with scoring by `pdb.score(id)`.
- The SQL in the spec (`@@@`, `paradedb.score`, `key_field`) still runs, but new code should use the current syntax.

### 4. HNSW index builds need shared memory

- **Problem:** parallel HNSW builds allocate `maintenance_work_mem` in `/dev/shm`. Docker's default of 64 MB made the build fail with `could not resize shared memory segment … No space left on device`.
- **Fix:** Compose now sets `shm_size: 2gb` on `db`. Helm (Phase 3) needs an equivalent `emptyDir` with `medium: Memory`.
- **Build settings:** use `maintenance_work_mem` of about 1.5 GB per build and 3 parallel workers. 1M × 384 then builds in about 4 minutes.

### 5. The partial HNSW index per KB is used

With the typed cast `embedding::halfvec(<dims>)` matching the index expression, the planner chose the per-KB HNSW index at both scales. Iterative scan kept results complete (always 40) despite ACL and RLS filters.

- **Caveat:** in a 25k-row smoke run *without* the iterative-scan settings, the planner preferred a btree on `kb_id` plus an exact sort. That plan is fine for small KBs; it is only slow if chosen at scale, which did not happen.

## Sizing guidance (first estimate)

These figures are measured, then extrapolated to 1M chunks of 1024 dims with `halfvec`:

- about 2.6 GB of HNSW index;
- about 3.5 GB of table;
- about 0.3 GB of BM25 index.

To keep p95 near run B's level, the database server needs RAM for the HNSW indexes of active KBs plus their hot heap pages: about 8 GB of `shared_buffers` or OS cache per million 1024-dim chunks, and 16–32 GB of RAM in total for a 1M-chunk installation. This must be validated on the production-like environment in E3.7.

## Not covered (follow-ups)

| Gap | Where it gets covered |
| --- | --- |
| Concurrency: many simultaneous queries; this run was sequential, one client | E3.7 load tests |
| 1M × 1024 dims on a correctly sized server | E3.7 |
| Real embeddings and real text: relevance quality, not just latency | E1.16, RAG evaluation (E3.2) |
| Reranker latency (external model, excluded by the NFR) | E1.16 with a TEI/vLLM reranker |
| Index maintenance under continuous ingestion and deletes | E2.13 |

## Decision

ADR-008 is **confirmed with amendments**: the ACL sentinel and literal-tokenized ACL field, `project_id` in the BM25 index, the BM25 query rules, current pg_search syntax and `shm_size`. ADR-002 is amended for BM25 queries.
