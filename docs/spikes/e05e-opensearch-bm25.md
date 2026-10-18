# Spike E0.5e – BM25 on OpenSearch + pgvector hybrid (ADR-016)

_Date: 2026-10-18. Code: [backend/spikes/e05e/bench.py](../../backend/spikes/e05e/bench.py). Raw results: [run](e05e-r1000000d384.json). Run: `make spike/e05e`._

## Question

Does the ADR-016 split still meet the NFR **"hybrid retrieval under 300 ms p95 on 1M chunks"**, and is tenant isolation without RLS sound? In this design, vectors stay in PostgreSQL (HNSW, RLS) and BM25 moves to one shared OpenSearch index routed by project. The retriever runs both searches concurrently and fuses them with RRF. As in E0.5b, time spent in external models (query embedding, reranker) is excluded.

**Result: yes.** Hybrid p95 was **159 ms at 1M chunks**, and isolation leaked **0** ids. The margin is smaller than in E0.5b (39 ms). The cause is not OpenSearch's BM25: its p95 is 48 ms, close to pg_search's 40 ms. The cause is memory: on this 7.5 GB host, OpenSearch takes RAM that PostgreSQL used to cache the HNSW index, so vector search got slower.

## Setup

- **Host:** the same as E0.5b: 4 vCPU and 7.5 GB RAM. The application services were stopped during the run, but the host was already 6 GB into swap.
  - PostgreSQL 17 with pgvector 0.8.7 (`pgvector/pgvector` image) ran with `shared_buffers` at 1.9 GB.
  - OpenSearch 3.9.0 ran single node with a 1 GB heap, the security plugin, TLS and the least-privilege user `forge`.
- **Data and queries:** the E0.5b corpus and query generator, so the numbers compare.
  - 1,000,000 chunks of 384 dimensions in the measured knowledge base, plus 100,000 rows of a second project.
  - 600 query templates per method came from the same seed.
- **OpenSearch index:** one shard and no replica. The mapping is strict, as in ADR-016, with `_routing` required.
  - Each query is a bool query: `match` on `content` (operator `or`); `project_id`, `kb_id` and `acl_groups` (`terms`, with the `__public__` sentinel) in the filter context; routing by project; `track_total_hits: false` and `_source: false`.
- **Hybrid query:** vector top 40 (PostgreSQL, RLS active, partial HNSW, `ef_search = 80`, iterative scan) and BM25 top 40 (OpenSearch), run concurrently from a two-thread pool and fused in Python with RRF (k = 60), top 40. This is what the retriever (E1.16) will do.
- **Measurement:** 100 warm-up queries, then 1,500 measured queries. The three methods rotate over **distinct** queries, giving 500 each, so every method sees the same cache state and no method re-runs a query that another one has just warmed.

### Why the first run was discarded

The first run measured the methods one after the other, as in E0.5b: all vector queries, then all BM25 queries, then all hybrid queries. On this memory-tight host, the order dominated the result:

| Method (sequential run) | p95 |
| --- | --- |
| Vector top 40 | 276 ms |
| BM25 top 40 (client) | 204 ms, with server `took` p95 at 32 ms |
| Hybrid | 84 ms |

The hybrid query, measured last, was faster than both of its parts, which is impossible with equal caches. The harness now rotates the methods per query, and the numbers below come from the rotated run.

## Results

| Metric | E0.5e (OpenSearch BM25) | E0.5b (pg_search, same host and data) | Target |
| --- | --- | --- | --- |
| **Hybrid RRF p95** | **159.4 ms** | 39.4 ms | < 300 ms |
| Hybrid p50 / p99 | 42.4 / 211.2 ms | 9.9 / 81.6 ms | – |
| Vector top 40 p50 / p95 | 36.3 / 169.5 ms | 9.6 / 13.2 ms | – |
| BM25 top 40 p50 / p95 (client) | 32.2 / 47.7 ms | 14.5 / 39.5 ms | – |
| BM25 server `took` p95 | 44 ms | – | – |
| Results always complete (40 rows) | yes | yes | – |
| Cross-project isolation (ids of project A returned for project B, 50 queries) | **0** | RLS | 0 |
| Bulk indexing of 1.1M documents | 204 s (5,400 docs/s, 5,000 per bulk request) | BM25 build 34 s | – |
| Index size | 699 MB, 6 segments | 0.3 GB | – |
| HNSW build (m = 16, ef_construction = 128, 3 workers) | 438 s | 230 s | – |

Notes:

- **Hybrid ≈ the slower of its two parts.** The searches run concurrently, so the hybrid p95 (159 ms) follows the vector p95 (170 ms), not the sum.
- **Vector search is 13 times slower than in E0.5b.** That run already noted that the 2.8 GB HNSW index and heap did not fit in the 1.9 GB `shared_buffers`. With OpenSearch's heap plus its own page cache on the same 7.5 GB host, even less of the index stays in the OS cache, and the host is swapping. This is a sizing effect, not a design effect.
- **The HNSW build was slower** (438 s against 230 s) for the same reason, and because other work ran on the host during the build. Build time is not an NFR.
- **The BM25 client time is close to the server `took` time**, so HTTPS and JSON overhead is small: a few milliseconds per query at p50.

## Findings

### 1. The NFR holds, with less margin on a small host

159 ms against 300 ms. On a production-sized server the vector side should return to E0.5b's level, because PostgreSQL would have enough cache for the HNSW index. The BM25 side was already 48 ms. Sizing guidance for E3.7 follows.

### 2. Tenant isolation without RLS works as designed

- Every query is built by one function: `bm25_body` in the harness, `forge.rag` in the product. It always adds the project term, the KB term, the ACL terms and project routing.
- No query DSL comes from callers.
- The isolation check ran 50 queries for another project and found no id of the first project. This test moves to CI with the retriever (threat model TB2, M1.4).

### 3. Filter context and the ACL sentinel carry over from E0.5b

- `terms` on `acl_groups`, with `__public__` always in the user's groups, gives complete result sets: always 40 rows.
- With `track_total_hits: false`, OpenSearch can stop scoring early.
- No ACL-specific tuning was needed, unlike pg_search, which needed the literal tokenizer and `===`.

### 4. Indexing throughput is adequate for ingestion

- 5,400 documents per second, single client, refresh disabled during the load.
- The ingestion workflow (E1.15) indexes per document after the PostgreSQL commit, so the steady-state load is far below this rate.
- A full re-index of 1M chunks (alias swap, ADR-016) takes about 3–4 minutes from one worker.

## Sizing guidance (update to E0.5b)

| Component | Per 1M chunks | Notes |
| --- | --- | --- |
| PostgreSQL | as in E0.5b: about 8 GB of cache per 1M 1024-dim chunks | Keep the HNSW index of active KBs in RAM. Do not count memory that OpenSearch uses. |
| OpenSearch | about 0.7 GB of index per 1M chunks of 60 words; a 1–2 GB heap; page cache about the index size | Separate node or container memory limit in Helm. Do not co-locate it with PostgreSQL on a small host. |

For a 1M-chunk installation, plan 16–32 GB for PostgreSQL, as in E0.5b, plus 4 GB for OpenSearch. E3.7 validates this on a production-like environment.

## Not covered (follow-ups)

| Gap | Where it gets covered |
| --- | --- |
| Concurrency: many simultaneous queries | E3.7 load tests |
| Production-sized host with separate memory budgets for PostgreSQL and OpenSearch | E3.7 |
| Italian and English analyzers per knowledge base, relevance quality | E1.16, RAG evaluation (E3.2) |
| Consistency under continuous ingestion, deletes and ACL changes; reconcile job | E1.15, E2.13 |
| CI isolation test against the real `forge.rag` query builder | M1.4 |

## Decision

ADR-016 is **confirmed**. The hybrid NFR is met at 1M chunks. Isolation relies on a single query builder and was verified. The latency margin depends on giving PostgreSQL and OpenSearch separate memory budgets, which goes into the Helm sizing (Phase 3) and E3.7.
