"""E0.5e benchmark: BM25 on OpenSearch (ADR-016) + pgvector HNSW, fused with RRF in Forge.

    FORGE_SPIKE_DSN=postgresql://owner@... FORGE_SPIKE_SEARCH_URL=https://127.0.0.1:9200 \
    FORGE_SPIKE_SEARCH_PASSWORD=... FORGE_SPIKE_SEARCH_CA=.../root-ca.pem \
    python -m spikes.e05e.bench --rows 1000000 --dims 384

Same synthetic corpus and queries as E0.5b (spikes.e05b.bench), so the numbers compare: the
vectors stay in PostgreSQL (RLS, partial HNSW per KB); the text goes to one shared OpenSearch
index routed by project, and every query filters project, KB and ACL in the bool filter
context. Hybrid = both searches concurrently + RRF (k = 60) in Python, as the retriever will do.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import psycopg

from spikes.e05b.bench import (
    BIG_KB,
    OTHER_PROJECT,
    PROJECT,
    connect,
    load,
    log,
    make_queries,
    pct,
    run_query,
    sql_vector,
    table,
)

INDEX = "forge-spike-e05e"
BULK = 5000


def search_client() -> httpx.Client:
    ctx = ssl.create_default_context(cafile=os.environ["FORGE_SPIKE_SEARCH_CA"])
    return httpx.Client(
        base_url=os.environ["FORGE_SPIKE_SEARCH_URL"],
        auth=("forge", os.environ["FORGE_SPIKE_SEARCH_PASSWORD"]),
        verify=ctx,
        timeout=120,
    )


def build_vector_index(conn: psycopg.Connection[Any], label: str, dims: int) -> float:
    t = table(label)
    conn.execute("SET maintenance_work_mem = '1500MB'")
    conn.execute("SET max_parallel_maintenance_workers = 3")
    log("building hnsw index")
    t0 = time.monotonic()
    conn.execute(
        f"""CREATE INDEX IF NOT EXISTS chunk_{label}_hnsw ON {t}
            USING hnsw ((embedding::halfvec({dims})) halfvec_cosine_ops)
            WITH (m = 16, ef_construction = 128) WHERE kb_id = '{BIG_KB}'"""
    )
    elapsed = round(time.monotonic() - t0, 1)
    conn.execute(f"CREATE INDEX IF NOT EXISTS chunk_{label}_kb ON {t} (kb_id)")
    conn.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
    conn.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
    conn.execute(f"DROP POLICY IF EXISTS tenant ON {t}")
    conn.execute(f"CREATE POLICY tenant ON {t} USING (project_id = app_project_id())")
    conn.execute(f"GRANT SELECT ON {t} TO forge_app, forge_admin")
    conn.execute(f"GRANT USAGE ON SCHEMA {t.split('.')[0]} TO forge_admin")
    conn.execute(f"ANALYZE {t}")
    log(f"  hnsw done in {elapsed}s")
    return elapsed


MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "refresh_interval": "-1",
    },
    "mappings": {
        "dynamic": "strict",
        "_routing": {"required": True},
        "properties": {
            "project_id": {"type": "keyword"},
            "kb_id": {"type": "keyword"},
            "document_id": {"type": "keyword"},
            "acl_groups": {"type": "keyword"},
            "heading_path": {"type": "text"},
            "content": {"type": "text"},
            "page": {"type": "integer"},
        },
    },
}


def index_text(conn: psycopg.Connection[Any], es: httpx.Client, label: str) -> dict[str, Any]:
    t = table(label)
    # The table has forced RLS: count and export as forge_admin (BYPASSRLS), like a re-index job.
    with conn.transaction():
        conn.execute("SET LOCAL ROLE forge_admin")
        total = conn.execute(f"SELECT count(*) FROM {t}").fetchone()
    assert total
    count = es.get(f"/{INDEX}/_count")
    if count.status_code == 200 and count.json()["count"] == total[0]:
        log(f"{INDEX} already holds {total[0]} documents, skipping indexing")
        return {"skipped": True}
    es.delete(f"/{INDEX}")
    es.put(f"/{INDEX}", json=MAPPING).raise_for_status()
    log(f"indexing {total[0]} documents into {INDEX}")
    t0 = time.monotonic()
    done = 0
    with conn.transaction(), conn.cursor(name="export") as cur:
        conn.execute("SET LOCAL ROLE forge_admin")
        cur.itersize = BULK
        cur.execute(
            f"SELECT id, project_id, kb_id, document_id, acl_groups, heading_path, content, page "
            f"FROM {t}"
        )
        batch: list[str] = []
        for row in cur:
            rid, project, kb, doc, acl, heading, content, page = row
            batch.append(json.dumps({"index": {"_id": str(rid), "routing": str(project)}}))
            batch.append(
                json.dumps(
                    {
                        "project_id": str(project),
                        "kb_id": str(kb),
                        "document_id": str(doc),
                        "acl_groups": list(acl),
                        "heading_path": heading,
                        "content": content,
                        "page": page,
                    }
                )
            )
            if len(batch) >= 2 * BULK:
                done += _bulk(es, batch)
                batch = []
                if done % 100_000 == 0:
                    log(f"  {done}/{total[0]}")
        if batch:
            done += _bulk(es, batch)
    bulk_s = time.monotonic() - t0
    es.put(f"/{INDEX}/_settings", json={"index": {"refresh_interval": "1s"}}).raise_for_status()
    es.post(f"/{INDEX}/_refresh").raise_for_status()
    stats = es.get(f"/{INDEX}/_stats/store,segments").json()["indices"][INDEX]["primaries"]
    out = {
        "documents": done,
        "index_s": round(bulk_s, 1),
        "docs_per_s": round(done / bulk_s),
        "store_mb": round(stats["store"]["size_in_bytes"] / 2**20),
        "segments": stats["segments"]["count"],
    }
    log(f"  indexed: {out}")
    return out


def _bulk(es: httpx.Client, lines: list[str]) -> int:
    resp = es.post(
        "/_bulk",
        content="\n".join(lines) + "\n",
        headers={"Content-Type": "application/x-ndjson"},
        params={"index": INDEX},
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("errors"):
        first = next(i for i in body["items"] if i["index"].get("error"))
        raise RuntimeError(f"bulk error: {first}")
    return len(lines) // 2


def bm25_body(q: dict[str, Any], project: str, k: int = 40) -> dict[str, Any]:
    """The only place a search query is built: project, KB and ACL are always filters."""
    return {
        "size": k,
        "_source": False,
        "track_total_hits": False,
        "query": {
            "bool": {
                "must": {"match": {"content": {"query": q["text"], "operator": "or"}}},
                "filter": [
                    {"term": {"project_id": project}},
                    {"term": {"kb_id": BIG_KB}},
                    {"terms": {"acl_groups": q["groups"]}},
                ],
            }
        },
    }


def run_bm25(
    es: httpx.Client, q: dict[str, Any], project: str = PROJECT
) -> tuple[float, list[str]]:
    t0 = time.perf_counter()
    resp = es.post(f"/{INDEX}/_search", params={"routing": project}, json=bm25_body(q, project))
    resp.raise_for_status()
    ms = (time.perf_counter() - t0) * 1000
    return ms, [h["_id"] for h in resp.json()["hits"]["hits"]]


def rrf(*rankings: list[str], k: int = 60, top: int = 40) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, rid in enumerate(ranking, start=1):
            scores[rid] = scores.get(rid, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=scores.__getitem__, reverse=True)[:top]


def summary(lat: list[float], rows: list[int]) -> dict[str, Any]:
    return {
        "p50_ms": pct(lat, 0.50),
        "p95_ms": pct(lat, 0.95),
        "p99_ms": pct(lat, 0.99),
        "mean_ms": round(statistics.mean(lat), 1),
        "min_rows": min(rows),
        "queries": len(lat),
    }


def bench(
    conn: psycopg.Connection[Any],
    es: httpx.Client,
    label: str,
    dims: int,
    queries: list[dict[str, Any]],
    warmup: int,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    vector_sql = sql_vector(label, dims)
    vconn = connect()  # separate connection: the hybrid runs both searches at once

    for q in queries[:warmup]:
        run_query(conn, vector_sql, q)
        run_bm25(es, q)

    # Rotate the three methods over distinct queries so they share the same cache state and no
    # method re-runs a query another one just warmed; measuring them one after the other
    # favoured whichever ran last on this memory-tight host.
    lat: dict[str, list[float]] = {"vector_top40": [], "bm25_top40_opensearch": [], "hybrid": []}
    rows: dict[str, list[int]] = {k: [] for k in lat}
    server: list[float] = []
    methods = list(lat)
    with ThreadPoolExecutor(max_workers=2) as pool:
        for i, q in enumerate(queries[warmup:]):
            method = methods[i % 3]
            t0 = time.perf_counter()
            if method == "vector_top40":
                ids = run_query(conn, vector_sql, q)[1]
            elif method == "bm25_top40_opensearch":
                resp = es.post(
                    f"/{INDEX}/_search", params={"routing": PROJECT}, json=bm25_body(q, PROJECT)
                )
                resp.raise_for_status()
                body = resp.json()
                server.append(float(body["took"]))
                ids = [h["_id"] for h in body["hits"]["hits"]]
            else:
                fv = pool.submit(run_query, vconn, vector_sql, q)
                fb = pool.submit(run_bm25, es, q)
                ids = rrf(fv.result()[1], fb.result()[1])
            lat[method].append((time.perf_counter() - t0) * 1000)
            rows[method].append(len(ids))
    out["vector_top40"] = summary(lat["vector_top40"], rows["vector_top40"])
    out["bm25_top40_opensearch"] = summary(
        lat["bm25_top40_opensearch"], rows["bm25_top40_opensearch"]
    ) | {"server_took_p95_ms": pct(server, 0.95)}
    out["hybrid_rrf_concurrent"] = summary(lat["hybrid"], rows["hybrid"])
    for key in ("vector_top40", "bm25_top40_opensearch", "hybrid_rrf_concurrent"):
        log(f"  {key}: {out[key]}")

    # Isolation: the same queries for another project never return this project's chunks.
    mine = set()
    for q in queries[warmup : warmup + 50]:
        mine |= set(run_bm25(es, q)[1])
    leaked = 0
    for q in queries[warmup : warmup + 50]:
        leaked += len(mine & set(run_bm25(es, q, OTHER_PROJECT)[1]))
    out["isolation_leaked_ids"] = leaked
    log(f"  isolation: {leaked} ids of {PROJECT[-2:]} returned for another project")
    vconn.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--rows", type=int, required=True)
    ap.add_argument("--other-rows", type=int, default=100_000)
    ap.add_argument("--dims", type=int, required=True)
    ap.add_argument("--queries", type=int, default=1600)  # 100 warm-up + 500 per method
    ap.add_argument("--warmup", type=int, default=100)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    conn = connect()
    es = search_client()
    load(conn, args.label, args.rows, args.other_rows, args.dims)
    hnsw_s = build_vector_index(conn, args.label, args.dims)
    indexing = index_text(conn, es, args.label)
    queries = make_queries(conn, args.label, args.queries, args.dims)
    info = es.get("/").json()
    result: dict[str, Any] = {
        "label": args.label,
        "rows_in_kb": args.rows,
        "rows_total": args.rows + args.other_rows,
        "dims": args.dims,
        "opensearch_version": info.get("version", {}).get("number"),
        "hnsw_build_s": hnsw_s,
        "indexing": indexing,
    }
    log("benchmarking")
    result["latency"] = bench(conn, es, args.label, args.dims, queries, args.warmup)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2))
    log(f"results written to {args.out}")


if __name__ == "__main__":
    main()
