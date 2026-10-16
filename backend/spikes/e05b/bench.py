"""E0.5b benchmark: hybrid retrieval (pgvector HNSW + pg_search BM25 + RRF) with RLS and ACL.

    FORGE_SPIKE_DSN=postgresql://owner@... python -m spikes.e05b.bench --rows 1000000 --dims 384

Data is synthetic but structured: vectors are clustered (centroid + noise, like real
embeddings), text mixes Zipf-distributed common words with cluster-specific topic words, and
20% of chunks carry ACL groups. The schema follows ADR-008: one table, untyped `halfvec`
column, partial HNSW index per knowledge base with a typed cast, BM25 index, forced RLS.
Queries run as `forge_app` with `app.project_id` set per transaction, as the product will.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import statistics
import time
from pathlib import Path
from typing import Any

import psycopg

SCHEMA = "spike_e05b"
BIG_KB = "00000000-0000-0000-0000-00000000000b"
OTHER_KB = "00000000-0000-0000-0000-00000000000c"
PROJECT = "00000000-0000-0000-0000-0000000000a1"
OTHER_PROJECT = "00000000-0000-0000-0000-0000000000a2"
CLUSTERS = 1000
GLOBAL_VOCAB = 20000
TOPIC_WORDS = 60
ACL_GROUPS = 50
BATCH = 50_000


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def connect() -> psycopg.Connection[Any]:
    return psycopg.connect(os.environ["FORGE_SPIKE_DSN"], autocommit=True)


def word(n: int) -> str:
    """Synthetic vocabulary, identical to the SQL generator: substr(md5(n::text), 1, 5 + n % 4)."""
    return hashlib.md5(str(n).encode()).hexdigest()[: 5 + n % 4]  # noqa: S324 - not security


def table(label: str) -> str:
    return f"{SCHEMA}.chunk_{label}"


def load(conn: psycopg.Connection[Any], label: str, rows: int, other_rows: int, dims: int) -> None:
    t = table(label)
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    conn.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO forge_app")
    exists = conn.execute("SELECT to_regclass(%s) IS NOT NULL", (t,)).fetchone()
    if exists and exists[0]:
        count = conn.execute(f"SELECT count(*) FROM {t}").fetchone()
        if count and count[0] == rows + other_rows:
            log(f"{t} already loaded ({count[0]} rows), skipping load")
            return
        conn.execute(f"DROP TABLE {t}")

    log("generating centroids")
    conn.execute(f"DROP TABLE IF EXISTS {SCHEMA}.centroid_{label}")
    conn.execute(
        f"""CREATE TABLE {SCHEMA}.centroid_{label} AS
            SELECT c AS id, array_agg(random() * 2 - 1 ORDER BY j)::real[] AS v
            FROM generate_series(0, {CLUSTERS - 1}) c, generate_series(1, {dims}) j
            GROUP BY c"""
    )
    conn.execute(
        f"""CREATE TABLE {t} (
                id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                project_id    uuid NOT NULL,
                kb_id         uuid NOT NULL,
                document_id   uuid NOT NULL,
                ordinal       int  NOT NULL,
                cluster       int  NOT NULL,
                content       text NOT NULL,
                heading_path  text,
                page          int,
                metadata      jsonb NOT NULL DEFAULT '{{}}',
                acl_groups    text[] NOT NULL DEFAULT '{{__public__}}',
                content_hash  bytea NOT NULL,
                embedding     halfvec NOT NULL)"""
    )

    def insert(n: int, project: str, kb: str) -> None:
        done = 0
        while done < n:
            size = min(BATCH, n - done)
            conn.execute(
                f"""INSERT INTO {t} (project_id, kb_id, document_id, ordinal, cluster, content,
                                     heading_path, page, acl_groups, content_hash, embedding)
                    SELECT %(p)s, %(kb)s, md5((g / 20)::text)::uuid, g %% 20, c.id,
                           (SELECT string_agg(substr(md5(w::text), 1, 5 + w %% 4), ' ')
                            FROM (SELECT CASE WHEN k <= 45
                                         THEN 1 + floor(power(random(), 3) * {GLOBAL_VOCAB})::int
                                         ELSE {GLOBAL_VOCAB} + c.id * {TOPIC_WORDS} + 1
                                              + floor(random() * {TOPIC_WORDS})::int
                                         END AS w
                                  FROM generate_series(1, 60) k) ids),
                           'Doc ' || (g / 20) || ' > Section ' || (g %% 5), 1 + g %% 40,
                           CASE WHEN random() < 0.8 THEN '{{__public__}}'::text[]
                                ELSE ARRAY['g' || (1 + floor(random() * {ACL_GROUPS})::int)] END,
                           sha256(g::text::bytea),
                           (SELECT array_agg(x + (random() * 2 - 1) * 0.35 ORDER BY o)
                            FROM unnest(c.v) WITH ORDINALITY AS u(x, o))::halfvec
                    FROM generate_series(%(lo)s::int, %(hi)s::int) g
                    JOIN {SCHEMA}.centroid_{label} c ON c.id = g %% {CLUSTERS}""",
                {"p": project, "kb": kb, "lo": done, "hi": done + size - 1},
            )
            done += size
            log(f"  {kb[-2:]}: {done}/{n} rows")

    t0 = time.monotonic()
    insert(rows, PROJECT, BIG_KB)
    insert(other_rows, OTHER_PROJECT, OTHER_KB)
    log(f"load done in {time.monotonic() - t0:.0f}s")


def build_indexes(conn: psycopg.Connection[Any], label: str, dims: int) -> dict[str, float]:
    t = table(label)
    timings: dict[str, float] = {}
    conn.execute("SET maintenance_work_mem = '1500MB'")
    conn.execute("SET max_parallel_maintenance_workers = 3")
    steps = {
        "hnsw": f"""CREATE INDEX IF NOT EXISTS chunk_{label}_hnsw ON {t}
                    USING hnsw ((embedding::halfvec({dims})) halfvec_cosine_ops)
                    WITH (m = 16, ef_construction = 128) WHERE kb_id = '{BIG_KB}'""",
        "bm25": f"""CREATE INDEX IF NOT EXISTS chunk_{label}_bm25 ON {t}
                    USING bm25 (id, content, heading_path, kb_id, project_id,
                                (acl_groups::pdb.literal))""",
        "kb_btree": f"CREATE INDEX IF NOT EXISTS chunk_{label}_kb ON {t} (kb_id)",
    }
    for name, sql in steps.items():
        log(f"building {name} index")
        t0 = time.monotonic()
        conn.execute(sql)
        timings[f"{name}_build_s"] = round(time.monotonic() - t0, 1)
        log(f"  {name} done in {timings[f'{name}_build_s']}s")
    conn.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
    conn.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
    conn.execute(f"DROP POLICY IF EXISTS tenant ON {t}")
    conn.execute(f"CREATE POLICY tenant ON {t} USING (project_id = app_project_id())")
    conn.execute(f"GRANT SELECT ON {t} TO forge_app")
    conn.execute(f"ANALYZE {t}")
    return timings


def sizes(conn: psycopg.Connection[Any], label: str) -> dict[str, str]:
    t = table(label)
    row = conn.execute(
        f"""SELECT pg_size_pretty(pg_table_size('{t}')),
                   pg_size_pretty(pg_relation_size('{SCHEMA}.chunk_{label}_hnsw')),
                   pg_size_pretty(pg_relation_size('{SCHEMA}.chunk_{label}_bm25'))"""
    ).fetchone()
    assert row
    return {"table": row[0], "hnsw_index": row[1], "bm25_index": row[2]}


def make_queries(
    conn: psycopg.Connection[Any], label: str, n: int, dims: int
) -> list[dict[str, Any]]:
    """Query vector = a random chunk's centroid + fresh noise; text = 2 topic words + 1 common."""
    rng = random.Random(42)
    queries = []
    for _ in range(n):
        c = rng.randrange(CLUSTERS)
        cv = conn.execute(f"SELECT v FROM {SCHEMA}.centroid_{label} WHERE id = %s", (c,)).fetchone()
        assert cv
        vec = [x + rng.uniform(-0.35, 0.35) for x in cv[0]]
        topic = [
            word(GLOBAL_VOCAB + c * TOPIC_WORDS + 1 + rng.randrange(TOPIC_WORDS)) for _ in range(2)
        ]
        common = word(1 + int(rng.random() ** 3 * GLOBAL_VOCAB))
        groups = ["__public__", *(f"g{rng.randrange(1, ACL_GROUPS + 1)}" for _ in range(3))]
        queries.append(
            {
                "vec": "[" + ",".join(f"{x:.4f}" for x in vec) + "]",
                "text": " ".join([*topic, common]),
                "groups": groups,
            }
        )
    return queries


# Project-wide chunks carry the '__public__' sentinel (an empty array indexes no term).
# Vector path: array overlap. BM25 path: `===` on the literal-tokenized field, pushed into the
# index. No literal project_id predicate: RLS alone keeps the TopK scan (spike finding).
ACL_VECTOR = "acl_groups && %(groups)s::text[]"
ACL_BM25 = "acl_groups === %(groups)s::text[]"


def sql_vector(label: str, dims: int, k: int = 40) -> str:
    return f"""SELECT id FROM {table(label)}
               WHERE kb_id = '{BIG_KB}' AND {ACL_VECTOR}
               ORDER BY embedding::halfvec({dims}) <=> %(vec)s::halfvec({dims}) LIMIT {k}"""


def sql_bm25(label: str, k: int = 40) -> str:
    return f"""SELECT id FROM {table(label)}
               WHERE content ||| %(text)s AND kb_id = '{BIG_KB}' AND {ACL_BM25}
               ORDER BY pdb.score(id) DESC LIMIT {k}"""


def sql_hybrid(label: str, dims: int) -> str:
    return f"""WITH v AS (SELECT id, row_number() OVER () AS r FROM ({sql_vector(label, dims)}) s),
                    b AS (SELECT id, row_number() OVER () AS r FROM ({sql_bm25(label)}) s)
               SELECT id, sum(1.0 / (60 + r)) AS rrf
               FROM (SELECT * FROM v UNION ALL SELECT * FROM b) u
               GROUP BY id ORDER BY rrf DESC LIMIT 40"""


def run_query(
    conn: psycopg.Connection[Any], sql: str, q: dict[str, Any], exact: bool = False
) -> tuple[float, list[Any]]:
    with conn.transaction():
        conn.execute("SET LOCAL ROLE forge_app")
        conn.execute("SELECT set_config('app.project_id', %s, true)", (PROJECT,))
        conn.execute("SET LOCAL hnsw.ef_search = 80")
        conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        if exact:
            conn.execute("SET LOCAL enable_indexscan = off")
            conn.execute("SET LOCAL enable_bitmapscan = off")
        t0 = time.perf_counter()
        rows = conn.execute(sql, q).fetchall()
        return (time.perf_counter() - t0) * 1000, [r[0] for r in rows]


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return round(s[min(len(s) - 1, int(len(s) * p))], 1)


def bench(
    conn: psycopg.Connection[Any], label: str, dims: int, queries: list[dict[str, Any]], warmup: int
) -> dict[str, Any]:
    plans = {
        "vector_top40": sql_vector(label, dims),
        "bm25_top40": sql_bm25(label),
        "hybrid_rrf": sql_hybrid(label, dims),
    }
    out: dict[str, Any] = {}
    for name, sql in plans.items():
        for q in queries[:warmup]:
            run_query(conn, sql, q)
        lat, counts = [], []
        for q in queries[warmup:]:
            ms, rows = run_query(conn, sql, q)
            lat.append(ms)
            counts.append(len(rows))
        out[name] = {
            "p50_ms": pct(lat, 0.50),
            "p95_ms": pct(lat, 0.95),
            "p99_ms": pct(lat, 0.99),
            "mean_ms": round(statistics.mean(lat), 1),
            "min_rows": min(counts),
            "queries": len(lat),
        }
        log(f"  {name}: {out[name]}")

    # ANN quality under filters: HNSW top-10 vs exact top-10 (sequential scan).
    recalls = []
    for q in queries[warmup : warmup + 30]:
        _, approx = run_query(conn, sql_vector(label, dims, 10), q)
        _, exact = run_query(conn, sql_vector(label, dims, 10), q, exact=True)
        recalls.append(len(set(approx) & set(exact)) / 10)
    out["vector_recall_at_10"] = round(statistics.mean(recalls), 3)
    log(f"  recall@10 (HNSW vs exact, with filters): {out['vector_recall_at_10']}")

    for name, sql in (("vector", sql_vector(label, dims)), ("bm25", sql_bm25(label))):
        with conn.transaction():
            conn.execute("SET LOCAL ROLE forge_app")
            conn.execute("SELECT set_config('app.project_id', %s, true)", (PROJECT,))
            conn.execute("SET LOCAL hnsw.ef_search = 80")
            conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            plan = conn.execute("EXPLAIN (COSTS OFF) " + sql, queries[0]).fetchall()
        lines = [r[0] for r in plan]
        out[f"{name}_plan"] = [ln[:160] for ln in lines if "<=>" not in ln or "Order By" in ln]
        out[f"{name}_plan_summary"] = {
            "uses_hnsw": any("_hnsw" in ln for ln in lines),
            "topk_scan": any("TopKScanExecState" in ln for ln in lines),
            "heap_filter": any("heap_filter" in ln for ln in lines),
        }
        log(f"  {name} plan: {out[f'{name}_plan_summary']}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument(
        "--rows", type=int, required=True, help="chunks in the benchmarked knowledge base"
    )
    ap.add_argument("--other-rows", type=int, default=100_000, help="chunks of another project")
    ap.add_argument("--dims", type=int, required=True)
    ap.add_argument("--queries", type=int, default=600)
    ap.add_argument("--warmup", type=int, default=100)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    conn = connect()
    load(conn, args.label, args.rows, args.other_rows, args.dims)
    index_timings = build_indexes(conn, args.label, args.dims)
    result: dict[str, Any] = {
        "label": args.label,
        "rows_in_kb": args.rows,
        "rows_total": args.rows + args.other_rows,
        "dims": args.dims,
        "index_timings": index_timings,
        "sizes": sizes(conn, args.label),
    }
    log(f"sizes: {result['sizes']}")
    queries = make_queries(conn, args.label, args.queries, args.dims)
    log("benchmarking")
    result["latency"] = bench(conn, args.label, args.dims, queries, args.warmup)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2))
    log(f"results written to {args.out}")


if __name__ == "__main__":
    main()
