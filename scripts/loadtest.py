#!/usr/bin/env python3
"""Load tests against the performance targets of the spec (E3.7), over HTTP.

    scripts/loadtest-env.sh                    starts a throw-away stack and runs everything (lab)
    python scripts/loadtest.py --url http://forge:8080 --login admin --password … \
        --provider-url http://mock:8100/v1 --scenarios gateway,retrieval,agents,ingest

The model provider should be a fast stand-in (the mock provider of the test suite, or any
OpenAI-compatible endpoint that answers at once): the targets exclude model time. The script
creates its own project and leaves it in place. Standard library and httpx only.

Targets (LLM time excluded):
  gateway     overhead under 50 ms p95 over a direct call to the provider
  retrieval   hybrid retrieval under 300 ms p95 (the corpus size is reported: the spec says 1M chunks)
  agents      200 concurrent agent runs per worker node, all finishing without error
  ingest      at least 50 pages/s per worker on text documents (a page = 3,000 characters)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
import uuid
from typing import Any

import httpx

PAGE_CHARS = 3000
WORDS = (
    "backup restore certificate database invoice payment network storage policy review approval "
    "contract customer shipping warehouse inventory report budget forecast security incident "
    "deployment release pipeline monitoring alert capacity quota gateway latency throughput"
).split()


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * q))] if ordered else float("nan")


class Api:
    def __init__(self, url: str, timeout: float = 120.0) -> None:
        self.http = httpx.AsyncClient(
            base_url=url.rstrip("/"),
            timeout=timeout,
            limits=httpx.Limits(max_connections=400, max_keepalive_connections=400),
        )

    def _csrf(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.http.cookies.get("forge_csrf") or ""}

    async def login(self, login: str, password: str) -> None:
        r = await self.http.post("/api/v1/auth/login", json={"login": login, "password": password})
        r.raise_for_status()

    async def _send(self, method: str, path: str, **kw: Any) -> httpx.Response:
        """A kept-alive connection closed by the server between two calls is not a failure."""
        for attempt in range(3):
            try:
                return await self.http.request(method, path, **kw)
            except (httpx.ReadError, httpx.RemoteProtocolError, httpx.ConnectError):
                if attempt == 2:
                    raise
                await asyncio.sleep(0.2)
        raise AssertionError("unreachable")

    async def post(self, path: str, expect: int = 200, **kw: Any) -> Any:
        r = await self._send("POST", path, headers=self._csrf(), **kw)
        if r.status_code != expect:
            raise SystemExit(f"POST {path}: {r.status_code} {r.text[:300]}")
        return r.json() if r.content else None

    async def put(self, path: str, **kw: Any) -> Any:
        r = await self._send("PUT", path, headers=self._csrf(), **kw)
        r.raise_for_status()
        return r.json()

    async def get(self, path: str, **kw: Any) -> Any:
        r = await self._send("GET", path, **kw)
        r.raise_for_status()
        return r.json()


class Setup:
    """The project, models and keys the scenarios use."""

    def __init__(self) -> None:
        self.project_id = ""
        self.base = ""
        self.api_key = ""
        self.chat_name = ""
        self.agent_name = ""


async def setup(api: Api, args: argparse.Namespace) -> Setup:
    s = Setup()
    me = await api.get("/api/v1/me")
    run = uuid.uuid4().hex[:6]
    created = await api.post(
        "/api/v1/admin/projects",
        201,
        json={"name": f"Load {run}", "description": "load test", "admin_user_id": me["user"]["id"]},
    )
    s.project_id, s.api_key = created["project"]["id"], created["api_key"]
    s.base = f"/api/v1/projects/{s.project_id}"
    prov = await api.post(
        "/api/v1/admin/providers",
        201,
        json={
            "name": f"load {run}",
            "provider_type": "openai_compatible",
            "base_url": args.provider_url,
            "api_key": args.provider_key,
            "config": {"allow_http": True},
        },
    )
    models: dict[str, Any] = {}
    for key, model_id, extra in (
        ("chat", args.chat_model, {}),
        ("agent", args.agent_model, {}),
        ("embed", args.embedding_model, {"model_type": "embedding", "embedding_dims": args.dims}),
    ):
        body = {
            "name": f"load-{key}-{run}",
            "display_name": model_id,
            "provider_id": prov["id"],
            "model_type": "chat",
            "model_id": model_id,
            "capabilities": ["streaming", "tools"] if key == "agent" else ["streaming"],
            "max_retries": 0,
            "timeout_s": 60,
            "all_projects": True,
        } | extra
        if key == "embed":
            body["capabilities"] = []
        models[key] = await api.post("/api/v1/admin/models", 201, json=body)
    s.chat_name, s.agent_name = models["chat"]["name"], models["agent"]["name"]
    await api.put(f"{s.base}/aliases/default-chat", json={"model_ids": [models["agent"]["id"]]})
    await api.put(f"{s.base}/aliases/embedding", json={"model_ids": [models["embed"]["id"]]})
    return s


# ---- gateway
async def timed_calls(
    client: httpx.AsyncClient, url: str, headers: dict[str, str], body: dict[str, Any],
    concurrency: int, count: int,
) -> tuple[list[float], int, float]:
    lat: list[float] = []
    errors = 0
    queue: asyncio.Queue[int] = asyncio.Queue()
    for i in range(count):
        queue.put_nowait(i)

    async def worker() -> None:
        nonlocal errors
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            t = time.perf_counter()
            try:
                r = await client.post(url, headers=headers, json=body)
                if r.status_code != 200:
                    errors += 1
                    continue
            except httpx.HTTPError:
                errors += 1
                continue
            lat.append((time.perf_counter() - t) * 1000)

    started = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    return lat, errors, time.perf_counter() - started


async def gateway(api: Api, s: Setup, args: argparse.Namespace) -> dict[str, Any]:
    body = {"model": s.chat_name, "messages": [{"role": "user", "content": "ping"}]}
    key = {"Authorization": f"Bearer {s.api_key}"}
    direct_body = {"model": args.chat_model, "messages": body["messages"]}
    direct_h = {"Authorization": f"Bearer {args.provider_key}"}
    async with httpx.AsyncClient(timeout=60) as direct:
        await timed_calls(direct, f"{args.provider_url}/chat/completions", direct_h, direct_body, 1, 20)
        base, _, _ = await timed_calls(
            direct, f"{args.provider_url}/chat/completions", direct_h, direct_body, 1, args.calls
        )
    await timed_calls(api.http, "/v1/chat/completions", key, body, 1, 20)  # warm up
    seq, errors, _ = await timed_calls(api.http, "/v1/chat/completions", key, body, 1, args.calls)
    conc, cerrors, wall = await timed_calls(
        api.http, "/v1/chat/completions", key, body, args.concurrency, args.calls * 4
    )
    overhead50 = statistics.median(seq) - statistics.median(base)
    overhead95 = pct(seq, 0.95) - pct(base, 0.95)
    return {
        "provider_direct_ms": {"p50": statistics.median(base), "p95": pct(base, 0.95)},
        "gateway_sequential_ms": {"p50": statistics.median(seq), "p95": pct(seq, 0.95)},
        "overhead_ms": {"p50": overhead50, "p95": overhead95},
        f"concurrent_{args.concurrency}_ms": {
            "p50": statistics.median(conc), "p95": pct(conc, 0.95), "p99": pct(conc, 0.99),
        },
        "throughput_rps": len(conc) / wall,
        "errors": errors + cerrors,
        "pass": overhead95 < 50 and errors + cerrors == 0,
        "target": "overhead p95 < 50 ms",
    }


# ---- ingestion and retrieval share a corpus
def document(i: int, pages: int) -> bytes:
    rnd = random.Random(i)
    parts = []
    while sum(len(p) + 1 for p in parts) < PAGE_CHARS * pages:
        parts.append(" ".join(rnd.choices(WORDS, k=20)) + ".")
    text = f"# Document {i}\n\n" + "\n\n".join(parts)
    return text[: PAGE_CHARS * pages].encode()


async def upload_corpus(
    api: Api, s: Setup, docs: int, pages_per_doc: int = 1
) -> tuple[str, float, int, float]:
    coll = await api.post(
        f"{s.base}/collections", 201, json={"name": f"load-{uuid.uuid4().hex[:6]}", "description": "load test"}
    )
    cid = coll["id"]
    pages = 0
    started = time.perf_counter()
    for start in range(0, docs, 20):
        files = []
        for i in range(start, min(docs, start + 20)):
            files.append(("files", (f"doc{i}.md", document(i, pages_per_doc))))
            pages += pages_per_doc
        await api.post(f"{s.base}/collections/{cid}/documents", files=files, data={"group_ids": [], "role_ids": []})
    uploaded = time.perf_counter() - started
    while True:
        rows = await api.get(f"{s.base}/collections/{cid}/documents", params={"limit": 1000})
        busy = [d for d in rows if d["status"] in ("pending", "processing")]
        busy += [i for d in rows for i in d["ingestions"] if i["status"] in ("pending", "processing")]
        if not busy and len(rows) >= docs:
            break
        await asyncio.sleep(0.5)
        if time.perf_counter() - started > 1800:
            raise SystemExit("ingestion did not finish in 30 minutes")
    failed = sum(1 for d in rows if d["status"] != "ready")
    return cid, time.perf_counter() - started, pages - failed, uploaded


async def build_pipeline(api: Api, s: Setup, cid: str) -> tuple[dict[str, Any], float]:
    """A RAG pipeline over the collection: its knowledge base is built by the ingest worker from
    every document at once, so the queue is full and the time is the worker's, not the upload's."""
    started = time.perf_counter()
    pipe = await api.post(
        f"{s.base}/rag", 201,
        json={
            "name": f"load {uuid.uuid4().hex[:6]}", "description": "load test pipeline",
            "config": {"collection_id": cid, "similarity_threshold": 0.0, "top_k": 5},
        },
    )
    while True:
        kbs = await api.get(f"{s.base}/collections/{cid}/knowledge-bases")
        if kbs and all(k["status"] == "ready" for k in kbs):
            return pipe, time.perf_counter() - started
        if time.perf_counter() - started > 1800:
            raise SystemExit("the knowledge base did not become ready in 30 minutes")
        await asyncio.sleep(0.5)


async def ingest(api: Api, s: Setup, args: argparse.Namespace, corpus: dict[str, Any]) -> dict[str, Any]:
    cid, seconds, pages, uploaded = await upload_corpus(api, s, args.docs, args.pages_per_doc)
    pipe, build = await build_pipeline(api, s, cid)
    corpus.update(collection=cid, pipeline=pipe)
    worker_rate = pages / build
    return {
        "documents": args.docs, "pages_per_document": args.pages_per_doc, "pages": pages,
        "upload_and_ingest_seconds": seconds, "seconds_until_all_uploaded": uploaded,
        "upload_path_pages_per_s": pages / seconds,
        "knowledge_base_build_seconds": build,
        "worker_pages_per_s": worker_rate,
        "pass": worker_rate >= 50,
        "target": ">= 50 pages/s per worker (one ingest worker; the knowledge-base build keeps its queue full)",
    }


async def retrieval(api: Api, s: Setup, args: argparse.Namespace, corpus: dict[str, Any]) -> dict[str, Any]:
    cid = corpus.get("collection")
    pipe = corpus.get("pipeline")
    if not cid:
        cid, _, _, _ = await upload_corpus(api, s, args.docs, args.pages_per_doc)
    if not pipe:
        pipe, _ = await build_pipeline(api, s, cid)
    kbs = await api.get(f"{s.base}/collections/{cid}/knowledge-bases")
    chunks = sum(k.get("chunks", 0) for k in kbs)
    rnd = random.Random(7)

    async def phase(concurrency: int, count: int) -> tuple[list[float], list[float], int]:
        sem = asyncio.Semaphore(concurrency)
        retr: list[float] = []
        total: list[float] = []
        errors = 0

        async def one() -> None:
            nonlocal errors
            async with sem:
                q = " ".join(rnd.choices(WORDS, k=4))
                t = time.perf_counter()
                r = await api._send(
                    "POST", f"{s.base}/rag/{pipe['id']}/query", headers=api._csrf(), json={"query": q}
                )
                if r.status_code != 200:
                    errors += 1
                    return
                total.append((time.perf_counter() - t) * 1000)
                tm = r.json().get("retrieval", {}).get("timings", {})
                retr.append(float(sum(tm.values())))

        await asyncio.gather(*(one() for _ in range(count)))
        return retr, total, errors

    await phase(1, 10)  # warm up
    seq_r, seq_t, e1 = await phase(1, args.queries)
    con_r, con_t, e2 = await phase(args.concurrency, args.queries)
    p95 = pct(seq_r, 0.95)
    return {
        "chunks_in_corpus": chunks,
        "retrieval_sequential_ms": {"p50": statistics.median(seq_r) if seq_r else None, "p95": p95},
        "request_sequential_ms": {"p50": statistics.median(seq_t) if seq_t else None, "p95": pct(seq_t, 0.95)},
        f"retrieval_concurrent_{args.concurrency}_ms": {
            "p50": statistics.median(con_r) if con_r else None, "p95": pct(con_r, 0.95),
        },
        "errors": e1 + e2,
        "pass": p95 < 300 and e1 + e2 == 0,
        "target": "p95 < 300 ms one query at a time (the spec's corpus is 1M chunks; this one is the size above)",
    }


# ---- agents
async def agents(api: Api, s: Setup, args: argparse.Namespace) -> dict[str, Any]:
    prompt = await api.post(
        f"{s.base}/prompts", 201,
        json={
            "name": f"load prompt {uuid.uuid4().hex[:6]}", "description": "x",
            "version": {
                "model": s.agent_name, "system_template": "You answer briefly.",
                "user_template": "{question}", "variables": [],
            },
        },
    )
    agent = await api.post(
        f"{s.base}/agents", 201,
        json={
            "name": f"load agent {uuid.uuid4().hex[:6]}", "description": "x",
            "config": {"prompt": {"prompt_id": prompt["id"]}, "model": s.agent_name, "tools": [], "limits": {}},
        },
    )
    await api.post(f"{s.base}/agents/{agent['id']}/publish", json={})
    started = time.perf_counter()
    sem = asyncio.Semaphore(50)

    async def start() -> str:
        async with sem:
            conv = await api.post(f"{s.base}/conversations", 201, json={"agent_id": agent["id"]})
            run = await api.post(f"{s.base}/conversations/{conv['id']}/messages", 202, json={"content": "hello"})
            return str(run["id"])

    ids = await asyncio.gather(*(start() for _ in range(args.agents)))
    started_all = time.perf_counter() - started
    pending, status = set(ids), {}
    deadline = time.perf_counter() + args.agent_timeout
    while pending and time.perf_counter() < deadline:
        async def check(rid: str) -> None:
            run = await api.get(f"{s.base}/runs/{rid}")
            if run["status"] in ("succeeded", "failed", "cancelled"):
                status[rid] = run["status"]
                pending.discard(rid)
        await asyncio.gather(*(check(r) for r in list(pending)))
        await asyncio.sleep(1)
    wall = time.perf_counter() - started
    ok = sum(1 for v in status.values() if v == "succeeded")
    return {
        "runs": args.agents, "succeeded": ok, "not_finished": len(pending),
        "seconds_to_start_all": started_all, "seconds_to_finish_all": wall,
        "pass": ok == args.agents,
        "target": "200 concurrent runs per worker node (one runs worker ran)",
    }


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--login", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--provider-url", required=True, help="OpenAI-compatible base URL, ends with /v1")
    p.add_argument("--provider-key", default="sk-load")
    p.add_argument("--chat-model", default="p-ok")
    p.add_argument("--agent-model", default="a-agent")
    p.add_argument("--embedding-model", default="emb-64-bow")
    p.add_argument("--dims", type=int, default=64)
    p.add_argument("--scenarios", default="gateway,ingest,retrieval,agents")
    p.add_argument("--calls", type=int, default=300)
    p.add_argument("--concurrency", type=int, default=16)
    p.add_argument("--docs", type=int, default=60)
    p.add_argument("--pages-per-doc", type=int, default=10, help="a page is 3,000 characters")
    p.add_argument("--queries", type=int, default=200)
    p.add_argument("--agents", type=int, default=200)
    p.add_argument("--agent-timeout", type=float, default=600)
    p.add_argument("--json", help="write the results here")
    p.add_argument("--strict", action="store_true", help="exit 1 when a target is missed")
    args = p.parse_args()

    api = Api(args.url)
    await api.login(args.login, args.password)
    s = await setup(api, args)
    results: dict[str, Any] = {}
    corpus: dict[str, Any] = {}
    for name in args.scenarios.split(","):
        fn = {"gateway": gateway, "ingest": ingest, "retrieval": retrieval, "agents": agents}[name]
        print(f"== {name} ...", flush=True)
        if name in ("ingest", "retrieval"):
            results[name] = await fn(api, s, args, corpus)  # type: ignore[operator]
        else:
            results[name] = await fn(api, s, args)  # type: ignore[operator]
        print(json.dumps(results[name], indent=2, default=str), flush=True)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(results, fh, indent=2, default=str)
    print("\n| scenario | result | target |\n| --- | --- | --- |")
    for name, r in results.items():
        print(f"| {name} | {'PASS' if r['pass'] else 'FAIL'} | {r['target']} |")
    await api.http.aclose()
    return 0 if all(r["pass"] for r in results.values()) or not args.strict else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
