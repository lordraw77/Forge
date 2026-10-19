#!/usr/bin/env python3
"""Phase 1 demo (docs/requirements/phase-1.md, exit criterion 2) against a running stack, with
real providers: prompt -> documents -> RAG -> agent with that RAG and a tool -> chat with
citations -> run view with costs -> alias switched to another model, same agent.

    FORGE_URL=http://localhost:8080 FORGE_DEMO_USER=demo-admin FORGE_DEMO_PASSWORD=... \
    FORGE_DEMO_CHAT=groq-gpt-oss-20b FORGE_DEMO_CHAT2=groq-gpt-oss-120b \
    FORGE_DEMO_EMBEDDING=cf-bge-m3 scripts/demo-m15.py

The user must be a Super Admin (`forge users ensure-superuser`). Needs `requests`. It makes about
six model calls; point it at free models.
"""

from __future__ import annotations

import os
import sys
import time
import uuid

import requests

URL = os.environ.get("FORGE_URL", "http://localhost:8080")
USER = os.environ.get("FORGE_DEMO_USER", "demo-admin")
PASSWORD = os.environ["FORGE_DEMO_PASSWORD"]
CHAT = os.environ.get("FORGE_DEMO_CHAT", "groq-gpt-oss-20b")
CHAT2 = os.environ.get("FORGE_DEMO_CHAT2", "groq-gpt-oss-120b")
EMBEDDING = os.environ.get("FORGE_DEMO_EMBEDDING", "cf-bge-m3")

MANUAL = """# Forge Operations Manual

## Backups

The nightly backup job copies the PostgreSQL database to the object store at 02:00.
Restore uses the forge restore command with the backup identifier.

## Certificates

TLS certificates are renewed by the certificate manager thirty days before expiry.
"""

s = requests.Session()


def call(method: str, path: str, ok: int | tuple[int, ...] = (200, 201, 202, 204), **kw):
    headers = {"X-CSRF-Token": s.cookies.get("forge_csrf") or ""}
    r = s.request(method, URL + path, headers=headers, timeout=120, **kw)
    allowed = (ok,) if isinstance(ok, int) else ok
    if r.status_code not in allowed:
        sys.exit(f"{method} {path} -> {r.status_code}: {r.text[:400]}")
    return r.json() if r.content else None


def step(text: str) -> None:
    print(f"\n== {text}")


def wait(predicate, what: str, timeout: float = 180):
    end = time.time() + timeout
    while time.time() < end:
        value = predicate()
        if value:
            return value
        time.sleep(1)
    sys.exit(f"timed out waiting for {what}")


def run_and_wait(base: str, conversation: str, text: str) -> dict:
    queued = call("POST", f"{base}/conversations/{conversation}/messages", json={"content": text})
    return wait(
        lambda: (
            r
            if (r := call("GET", f"{base}/runs/{queued['id']}"))["status"]
            in ("succeeded", "failed", "cancelled")
            else None
        ),
        "the run",
    )


def show(run: dict) -> None:
    print(f"   status={run['status']} steps={run['steps']} tokens={run['tokens_in']}/{run['tokens_out']}"
          f" cost={run['cost']} latency={run['latency_ms']} ms")
    for st in run["step_list"]:
        extra = f" model={st['model_name']}" if st["model_name"] else ""
        print(f"   - {st['kind']:4} {st['name']:<18} {st['status']:<5} {st['latency_ms']:>5} ms"
              f" ttft={st['ttft_ms']}{extra}")
    print(f"   answer: {(run['output'] or run['error'] or '')[:300]!r}")
    for c in run["citations"]:
        print(f"   source [{c['index']}] {c['document_name']} {c['heading_path']}")


tag = uuid.uuid4().hex[:6]
call("POST", "/api/v1/auth/login", json={"login": USER, "password": PASSWORD})
me = call("GET", "/api/v1/me")

step("1. Project, aliases (super admin enables models, project admin sets aliases)")
project = call("POST", "/api/v1/admin/projects",
               json={"name": f"Demo {tag}", "admin_user_id": me["user"]["id"]})["project"]["id"]
base = f"/api/v1/projects/{project}"
models = {m["name"]: m for m in call("GET", "/api/v1/admin/models")}
for name in (CHAT, CHAT2, EMBEDDING):
    if name not in models:
        sys.exit(f"model {name} is not registered")
call("PUT", f"{base}/aliases/default-chat", json={"model_ids": [models[CHAT]["id"]]})
call("PUT", f"{base}/aliases/embedding", json={"model_ids": [models[EMBEDDING]["id"]]})
print(f"   project {project}: default-chat -> {CHAT}, embedding -> {EMBEDDING}")

step("2. Prompt and documents")
prompt = call("POST", f"{base}/prompts", json={
    "name": f"Ops assistant {tag}", "description": "instructions",
    "version": {"model": "default-chat",
                "system_template": "You answer questions about Forge operations. Be brief."}})
collection = call("POST", f"{base}/collections", json={"name": f"Manuals {tag}", "description": "ops"})
call("POST", f"{base}/collections/{collection['id']}/documents",
     files=[("files", ("manual.md", MANUAL.encode()))])

step("3. RAG pipeline (published) and agent with that RAG and a tool")
rag = call("POST", f"{base}/rag", json={
    "name": f"Ops RAG {tag}", "description": "answers from the manual",
    "config": {"collection_id": collection["id"], "reranker": None, "similarity_threshold": 0.2}})
wait(lambda: all(k["status"] == "ready"
                 for k in call("GET", f"{base}/collections/{collection['id']}/knowledge-bases"))
     or None, "indexing")
call("POST", f"{base}/rag/{rag['id']}/publish", json={})
agent = call("POST", f"{base}/agents", json={
    "name": f"Ops agent {tag}", "description": "answers from the manual",
    "config": {"prompt": {"prompt_id": prompt["id"]},
               "tools": [{"kind": "rag", "pipeline_id": rag["id"], "name": "search_manual"},
                         {"kind": "builtin", "name": "current_time"}],
               "limits": {"max_steps": 6}}})
call("POST", f"{base}/agents/{agent['id']}/publish", json={})
print(f"   agent {agent['name']} v1 published, tools: search_manual, current_time")

step("4. Chat: a question the documents answer")
conversation = call("POST", f"{base}/conversations", json={"agent_id": agent["id"]})
run1 = run_and_wait(base, conversation["id"], "When does the nightly backup run, and how is it restored?")
show(call("GET", f"{base}/runs/{run1['id']}"))

step("5. Chat: a question that needs the time tool")
run2 = run_and_wait(base, conversation["id"], "What time is it in Rome right now?")
show(call("GET", f"{base}/runs/{run2['id']}"))

step("6. Project totals (equal the sum of the runs)")
usage = call("GET", f"{base}/usage")
print(f"   runs={usage['total_runs']} tokens={usage['total_tokens']} cost={usage['total_cost']}")

step("7. Alias switched to another provider's model: the same agent, version still 1")
call("PUT", f"{base}/aliases/default-chat", json={"model_ids": [models[CHAT2]["id"]]})
conversation2 = call("POST", f"{base}/conversations", json={"agent_id": agent["id"]})
run3 = run_and_wait(base, conversation2["id"], "How are TLS certificates renewed?")
detail = call("GET", f"{base}/runs/{run3['id']}")
show(detail)
llm_models = {st["model_name"] for st in detail["step_list"] if st["kind"] == "llm"}
print(f"   agent version used: {detail['agent_version']}; model used: {sorted(llm_models)}")
ok = all(r["status"] == "succeeded" for r in (run1, run2, run3)) and CHAT2 in llm_models
print("\nDEMO " + ("OK" if ok else "FAILED"))
sys.exit(0 if ok else 1)
