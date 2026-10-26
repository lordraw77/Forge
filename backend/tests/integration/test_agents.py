"""M1.5 single agent and tracing against PostgreSQL, Valkey, OpenSearch, SeaweedFS, Temporal
and the mock provider (US-1.18.1 to 1.18.3, US-1.19.1, US-1.19.2, US-1.20.1; AC1, AC6, AC9).

Two real workers run in threads, each on a task queue of its own: ingestion (documents for the
retriever tool) and runs (the agent loop). The mock model `-agent` is scripted: it calls a tool,
then answers from the tool result.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from forge.api.app import create_app
from forge.core.settings import Settings
from tests.integration.test_gateway import alias, model, ok, project, provider, uid
from tests.integration.test_platform_core import MASTER_KEY, create_user, csrf, login
from tests.integration.test_rag import (
    MANUAL,
    NOTES,
    collection,
    kb_ready,
    pipeline,
    settled,
    upload,
    wait_for,
)
from tests.support import mock_provider
from tests.support.mock_provider import MockProvider

pytestmark = pytest.mark.skipif(
    not os.environ.get("FORGE_TEST_S3_ENDPOINT"), reason="needs S3, OpenSearch and Temporal"
)
TERMINAL = ("succeeded", "failed", "cancelled")


@pytest.fixture(scope="module")
def settings() -> Settings:
    run = uuid.uuid4().hex[:8]
    return Settings(
        database_url=os.environ["FORGE_TEST_DATABASE_URL"],
        redis_url=os.environ.get("FORGE_TEST_REDIS_URL", "redis://127.0.0.1:6379/15"),
        master_key=MASTER_KEY,
        admin_password="Bootstrap-Admin-Pass-1",
        db_pool_size=6,
        s3_endpoint=os.environ["FORGE_TEST_S3_ENDPOINT"],
        s3_access_key=os.environ["FORGE_TEST_S3_ACCESS_KEY"],
        s3_secret_key=os.environ["FORGE_TEST_S3_SECRET_KEY"],
        s3_bucket="forge-test",
        search_url=os.environ["FORGE_TEST_SEARCH_URL"],
        search_password=os.environ["FORGE_TEST_SEARCH_PASSWORD"],
        search_ca_file=os.environ["FORGE_TEST_SEARCH_CA"],
        search_index="forge-test-chunks",
        temporal_address=os.environ["FORGE_TEST_TEMPORAL_ADDRESS"],
        temporal_ingest_queue=f"ingest-agents-{run}",
        temporal_runs_queue=f"runs-agents-{run}",
        metrics_port=0,
    )


def _start_worker(queue: str, task_queue: str, settings: Settings) -> tuple[threading.Event, Any]:
    from temporalio.client import Client

    from forge.workers.runner import build_registrations, new_worker

    stop, started = threading.Event(), threading.Event()
    errors: list[BaseException] = []

    async def main() -> None:
        reg = await build_registrations(queue, settings)
        client = await Client.connect(settings.temporal_address)
        try:
            async with new_worker(client, task_queue, reg):
                started.set()
                await asyncio.to_thread(stop.wait)
        finally:
            for close in reg.closers:
                await close()

    def run() -> None:
        try:
            asyncio.run(main())
        except BaseException as exc:
            errors.append(exc)
            started.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert started.wait(90), f"{queue} worker did not start"
    assert not errors, errors
    return stop, thread


@pytest.fixture(scope="module")
def workers(settings: Settings) -> Iterator[None]:
    ingest = _start_worker("ingest", settings.temporal_ingest_queue, settings)
    runs = _start_worker("runs", settings.temporal_runs_queue, settings)
    yield
    for stop, thread in (ingest, runs):
        stop.set()
        thread.join(timeout=30)


@pytest.fixture(scope="module")
def mock() -> Iterator[str]:
    with MockProvider() as base_url:
        yield base_url


@pytest.fixture(scope="module")
def client(settings: Settings, workers: None) -> Iterator[TestClient]:
    app = create_app(settings, gateway_backoff_s=0.01)
    with TestClient(app) as c:
        deadline = time.time() + 30
        while not app.state.initialised and time.time() < deadline:
            time.sleep(0.1)
        assert app.state.initialised
        yield c


@pytest.fixture(scope="module")
def admin(client: TestClient) -> TestClient:
    return login(client, create_user(client, super_admin=True))


CHAT = {"capabilities": ["streaming", "tools"], "cost_input": 1, "cost_output": 2}


@pytest.fixture(scope="module")
def env(admin: TestClient, client: TestClient, mock: str) -> dict[str, Any]:
    proj = project(admin, client)
    prov = provider(admin, mock)
    models: dict[str, Any] = {}
    for key, model_id, extra in (
        ("agent", "a-agent", CHAT),
        ("slow", "a-agent-slowstream", CHAT),
        ("loop", "a-loop", CHAT),
        ("heavy", "a-loop", CHAT | {"cost_input": 1000, "cost_output": 1000}),
        ("hang", "a-agent-hang", CHAT | {"timeout_s": 60}),
        ("plain", "a-plain", {"capabilities": ["streaming"]}),
        (
            "embed",
            "emb-64-bow",
            {"model_type": "embedding", "embedding_dims": 64, "capabilities": []},
        ),
        ("rerank", "rr", {"model_type": "reranker", "capabilities": []}),
    ):
        models[key] = model(admin, prov["id"], model_id, all_projects=True, **extra)
    owner = login(client, proj["owner"])
    base = f"/api/v1/projects/{proj['id']}"
    alias(owner, "default-chat", [models["agent"]["id"]], url=base)
    alias(owner, "embedding", [models["embed"]["id"]], url=base)
    alias(owner, "rerank", [models["rerank"]["id"]], url=base)
    roles = {r["role_type"]: r["id"] for r in ok(owner.get(f"{base}/roles"))}
    env: dict[str, Any] = {
        "project": proj,
        "owner": owner,
        "base": base,
        "models": models,
        "mock": mock,
        "provider": prov,
    }
    for role in ("end_user", "viewer"):
        user = create_user(client)
        ok(
            owner.post(
                f"{base}/members",
                headers=csrf(owner),
                json={"identifiers": [user["username"]], "role_name_id": roles[role]},
            )
        )
        env[role] = login(client, user)
        env[f"{role}_id"] = user["id"]
    return env


@pytest.fixture(autouse=True)
def reset_mock() -> None:
    mock_provider.reset()


# ---- helpers
def make_prompt(env: dict[str, Any], system: str = "You answer briefly about {topic}.") -> Any:
    c = env["owner"]
    body = {
        "name": f"Agent prompt {uid()}",
        "description": "instructions",
        "version": {
            "model": env["models"]["agent"]["name"],
            "system_template": system,
            "user_template": "{question}",
            "variables": [{"name": "topic", "value": "operations", "required": False}],
        },
    }
    return ok(c.post(f"{env['base']}/prompts", headers=csrf(c), json=body), 201)


def make_agent(
    env: dict[str, Any],
    prompt_id: str,
    *,
    model_key: str = "agent",
    tools: list[dict[str, Any]] | None = None,
    limits: dict[str, Any] | None = None,
    name: str | None = None,
    **config: Any,
) -> Any:
    c = env["owner"]
    body = {
        "name": name or f"Agent {uid()}",
        "description": "test agent",
        "config": {
            "prompt": {"prompt_id": prompt_id},
            "model": env["models"][model_key]["name"],
            "tools": tools or [],
            "limits": limits or {},
        }
        | config,
    }
    return ok(c.post(f"{env['base']}/agents", headers=csrf(c), json=body), 201)


def publish(env: dict[str, Any], agent_id: str) -> Any:
    c = env["owner"]
    return ok(c.post(f"{env['base']}/agents/{agent_id}/publish", headers=csrf(c), json={}))


def start_chat(
    env: dict[str, Any], agent_id: str, text: str, who: str = "owner", **conv: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    c = env[who]
    conversation = ok(
        c.post(
            f"{env['base']}/conversations",
            headers=csrf(c),
            json={"agent_id": agent_id} | conv,
        ),
        201,
    )
    return conversation, send(env, conversation["id"], text, who)


def send(env: dict[str, Any], conversation_id: str, text: str, who: str = "owner") -> Any:
    c = env[who]
    return ok(
        c.post(
            f"{env['base']}/conversations/{conversation_id}/messages",
            headers=csrf(c),
            json={"content": text},
        ),
        202,
    )


def get_run(env: dict[str, Any], run_id: str, who: str = "owner") -> Any:
    return ok(env[who].get(f"{env['base']}/runs/{run_id}"))


def finished(env: dict[str, Any], run_id: str, timeout: float = 60, who: str = "owner") -> Any:
    def done() -> Any:
        run = get_run(env, run_id, who)
        return run if run["status"] in TERMINAL else None

    return wait_for(done, timeout=timeout, what=f"run {run_id}")


def events(env: dict[str, Any], run_id: str, who: str = "owner") -> list[dict[str, Any]]:
    """The run's SSE events (the stream ends after `done` or `error`)."""
    out: list[dict[str, Any]] = []
    with env[who].stream("GET", f"{env['base']}/runs/{run_id}/events") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
    return out


def admin_rows(client: TestClient, statement: str) -> Any:
    """Rows as `forge_admin` (the tables have forced row-level security)."""
    from sqlalchemy import text

    from forge.core.db import admin_scope

    async def go() -> Any:
        async with admin_scope(client.app.state.sessions) as s:  # type: ignore[attr-defined]
            return (await s.execute(text(statement))).all()

    return client.portal.call(go)  # type: ignore[attr-defined]


def tool(name: str, **extra: Any) -> dict[str, Any]:
    return {"kind": "builtin", "name": name} | extra


# ---- agent definition (US-1.18.1)
@pytest.mark.ac("US-1.18.1/1")
def test_agent_versions_are_pinned_and_references_block_delete(
    env: dict[str, Any], client: TestClient
) -> None:
    c, base = env["owner"], env["base"]
    p = make_prompt(env)
    a = make_agent(env, p["id"], tools=[tool("current_time")])
    assert a["latest_version"] == 1
    assert a["status"] == "draft"
    cfg = a["version"]["config"]
    assert cfg["prompt"]["version"] == 1  # pinned when saved (it was empty)
    assert cfg["limits"] == {
        "max_steps": 8,
        "max_tokens": 50000,
        "timeout_s": 120,
        "max_cost_usd": 1.0,
    }

    # A new prompt version does not change what the agent version uses.
    new = ok(
        c.post(
            f"{base}/prompts/{p['id']}/versions",
            headers=csrf(c),
            json={
                "model": env["models"]["agent"]["name"],
                "system_template": "Changed.",
                "user_template": "{question}",
            },
        ),
        201,
    )
    assert new["version"]["version"] == 2
    again = ok(c.get(f"{base}/agents/{a['id']}/versions"))
    assert again[0]["config"]["prompt"]["version"] == 1

    # Saving builds version 2, which pins prompt version 2 when asked for the latest.
    v2 = ok(
        c.post(
            f"{base}/agents/{a['id']}/versions",
            headers=csrf(c),
            json={
                "config": {"prompt": {"prompt_id": p["id"], "version": 2}, "tools": []},
                "note": "second",
            },
        ),
        201,
    )
    assert v2["latest_version"] == 2
    assert v2["version"]["config"]["prompt"]["version"] == 2
    assert v2["version"]["tags"] == ["draft"]

    published = publish(env, a["id"])
    assert published["status"] == "published"
    assert published["published_version"] == 2

    # The prompt is in use: delete is refused with the list of references (US-1.11.2 #3).
    blocked = c.delete(f"{base}/prompts/{p['id']}", headers=csrf(c))
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "prompt_in_use"
    assert any(r["kind"] == "agent" and r["id"] == a["id"] for r in blocked.json()["detail"])

    copy = ok(
        c.post(
            f"{base}/agents/{a['id']}/duplicate",
            headers=csrf(c),
            json={"name": f"Copy {uid()}"},
        ),
        201,
    )
    assert copy["latest_version"] == 1
    ok(c.delete(f"{base}/agents/{copy['id']}", headers=csrf(c)), 204)
    ok(c.delete(f"{base}/agents/{a['id']}", headers=csrf(c)), 204)
    ok(c.delete(f"{base}/prompts/{p['id']}", headers=csrf(c)), 204)


def test_validation_rejects_bad_configurations(env: dict[str, Any]) -> None:
    c, base = env["owner"], env["base"]
    p = make_prompt(env)

    def post(config: dict[str, Any]) -> Any:
        body = {"name": f"V {uid()}", "description": "x", "config": config}
        return c.post(f"{base}/agents", headers=csrf(c), json=body)

    good = {"prompt": {"prompt_id": p["id"]}}
    assert post(good).status_code == 201
    assert post(good | {"limits": {"max_steps": 99}}).status_code == 422  # above the ceiling
    assert post(good | {"surprise": 1}).status_code == 422  # unknown fields refused
    assert post(good | {"tools": [tool("current_time"), tool("current_time")]}).status_code == 422
    bad_host = post(good | {"tools": [tool("http_get", allowed_hosts=["bad host!"])]})
    assert bad_host.status_code == 422
    unknown_var = post({"prompt": {"prompt_id": p["id"], "variables": {"nope": "x"}}})
    assert unknown_var.status_code == 422
    assert unknown_var.json()["code"] == "unknown_variables"
    missing = post({"prompt": {"prompt_id": str(uuid.uuid4())}})
    assert missing.status_code == 404
    no_tools = post(
        good | {"model": env["models"]["plain"]["name"], "tools": [tool("current_time")]}
    )
    assert no_tools.status_code == 422
    assert no_tools.json()["code"] == "model_without_tools"
    dup = {"name": "Same name", "description": "x", "config": good}
    first = c.post(f"{base}/agents", headers=csrf(c), json=dup)
    assert first.status_code in (201, 409)
    assert c.post(f"{base}/agents", headers=csrf(c), json=dup).status_code == 409


# ---- running (US-1.18.2, US-1.19.1, US-1.20.1)
@pytest.mark.ac("US-1.18.2/1")
@pytest.mark.ac("US-1.20.1/1")
@pytest.mark.ac("US-1.20.1/2")
def test_run_calls_a_tool_streams_and_accounts(env: dict[str, Any], client: TestClient) -> None:
    p = make_prompt(env)
    a = make_agent(env, p["id"], tools=[tool("current_time")])
    conv, queued = start_chat(env, a["id"], "What time is it in Rome?")
    assert queued["status"] in ("queued", "running", "succeeded")
    run = finished(env, queued["id"])
    assert run["status"] == "succeeded", run
    assert run["output"].startswith("Tool says:")
    assert "Europe/Rome" in run["output"]

    steps = run["step_list"]
    assert [(s["kind"], s["name"]) for s in steps] == [
        ("llm", "model"),
        ("tool", "current_time"),
        ("llm", "model"),
    ]
    assert steps[1]["input"] == {"arguments": {"timezone": "Europe/Rome"}}
    assert steps[1]["tool_call_id"] == "call_1"
    assert all(s["status"] == "ok" for s in steps)
    llm = [s for s in steps if s["kind"] == "llm"]
    assert all(s["tokens_in"] == 11 and s["tokens_out"] == 7 for s in llm)
    assert all(s["ttft_ms"] is not None and s["latency_ms"] >= 0 for s in llm)
    assert all(s["llm_call_id"] for s in llm)
    assert llm[0]["model_name"] == env["models"]["agent"]["name"]

    # Run totals equal the sum of the steps (US-1.20.1 #2) and of the gateway's records (AC9).
    assert run["tokens_in"] == sum(s["tokens_in"] for s in steps) == 22
    assert run["tokens_out"] == sum(s["tokens_out"] for s in steps) == 14
    assert Decimal(run["cost"]) == sum((Decimal(s["cost"]) for s in steps), Decimal(0))
    assert Decimal(run["cost"]) > 0
    assert run["steps"] == 3

    # The gateway writes `llm_call` in the background: wait for both calls of this run.
    def both_recorded() -> Any:
        found = admin_rows(
            client,
            "SELECT count(*), coalesce(sum(tokens_in),0), coalesce(sum(tokens_out),0), "
            f"coalesce(sum(cost),0) FROM llm_call WHERE trace_id = 'run:{run['id']}'",
        )[0]
        return found if found[0] >= 2 else None

    rows = wait_for(both_recorded, timeout=15, what="llm_call records")
    assert rows[0] == 2
    assert (rows[1], rows[2]) == (run["tokens_in"], run["tokens_out"])
    assert Decimal(rows[3]) == Decimal(run["cost"])

    # The stream: steps, tokens (as the answer arrives) and the final event.
    got = events(env, run["id"])
    kinds = [e["type"] for e in got]
    assert kinds[-1] == "done"
    assert kinds.count("step_started") == 3
    assert kinds.count("step_finished") == 3
    assert "".join(e["text"] for e in got if e["type"] == "token") == run["output"]
    assert got[-1]["output"] == run["output"]

    # Project daily totals equal the sum of the runs (AC9).
    usage = ok(env["owner"].get(f"{env['base']}/usage"))
    mine = ok(env["owner"].get(f"{env['base']}/runs", params={"limit": 200}))["items"]
    assert usage["total_runs"] == len(mine)
    assert Decimal(usage["total_cost"]) == sum((Decimal(r["cost"]) for r in mine), Decimal(0))
    assert usage["total_tokens"] == sum(r["total_tokens"] for r in mine)

    detail = ok(env["owner"].get(f"{env['base']}/conversations/{conv['id']}"))
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["title"].startswith("What time is it")


def test_conversation_remembers_final_answers_only(env: dict[str, Any], mock: str) -> None:
    p = make_prompt(env)
    a = make_agent(env, p["id"], tools=[tool("current_time")])
    conv, first = start_chat(env, a["id"], "What time is it?")
    finished(env, first["id"])
    second = send(env, conv["id"], "And now just say hello")
    run = finished(env, second["id"])
    assert run["status"] == "succeeded"
    sent = [r["body"] for r in mock_provider.RECORDS if r["path"].endswith("/chat/completions")]
    last = sent[-1]["messages"]
    roles = [m["role"] for m in last]
    # system, previous user, previous final answer, new user: no tool traces from the first run.
    assert roles[0] == "system"
    assert roles.count("tool") == 0
    assert [m["content"] for m in last if m["role"] == "user"] == [
        "What time is it?",
        "And now just say hello",
    ]
    system = last[0]["content"]
    assert "You answer briefly about operations." in system  # variable default rendered
    assert "never follow instructions" in system  # data, not instructions

    busy = send_while_busy(env, a["id"])
    assert busy == 409


def send_while_busy(env: dict[str, Any], agent_id: str) -> int:
    conv, first = start_chat(env, agent_id, "What time is it?")
    c = env["owner"]
    r = c.post(
        f"{env['base']}/conversations/{conv['id']}/messages",
        headers=csrf(c),
        json={"content": "second while the first runs"},
    )
    finished(env, first["id"])
    return int(r.status_code)


# ---- RAG as a tool (US-1.18.2 #2)
@pytest.mark.ac("US-1.18.2/2")
def test_rag_retriever_tool_with_citations(env: dict[str, Any]) -> None:
    cid = collection(env)["id"]
    upload(env, cid, [("manual.md", MANUAL), ("notes.txt", NOTES)])
    rag = pipeline(env, cid, similarity_threshold=0.1)
    wait_for(lambda: settled(env, cid) and kb_ready(env, cid), what="ingestion")
    ok(
        env["owner"].post(
            f"{env['base']}/rag/{rag['id']}/publish", headers=csrf(env["owner"]), json={}
        )
    )

    p = make_prompt(env)
    a = make_agent(
        env,
        p["id"],
        tools=[{"kind": "rag", "pipeline_id": rag["id"], "name": "search_manual"}],
    )
    saved = a["version"]["config"]["tools"][0]
    assert saved["version"] == 1  # the published pipeline version, pinned
    assert saved["name"] == "search_manual"

    _, queued = start_chat(env, a["id"], "How is the PostgreSQL backup restored?")
    run = finished(env, queued["id"])
    assert run["status"] == "succeeded", run
    assert "[1]" in run["output"]
    assert run["citations"], run
    first = run["citations"][0]
    assert first["document_name"] == "manual.md"
    assert first["pipeline"] == "search_manual"
    assert first["url"].endswith(f"/documents/{first['document_id']}/content")
    search = next(s for s in run["step_list"] if s["kind"] == "tool")
    assert search["name"] == "search_manual"
    assert search["output"]["hits"] >= 1
    assert search["output"]["citations"][0] == 1
    # Retrieval costs (embedding, rerank) are part of the tool step and the run total.
    assert search["tokens_in"] > 0
    assert run["tokens_in"] == sum(s["tokens_in"] for s in run["step_list"])

    # Nothing relevant: the agent says so and shows no citations.
    _, queued = start_chat(env, a["id"], "zzzz qqqq xxxx")
    quiet = finished(env, queued["id"])
    assert quiet["status"] == "succeeded"
    assert quiet["citations"] == []

    # The pipeline is in use by the agent: delete is refused (US-1.11.2 #3).
    blocked = env["owner"].delete(f"{env['base']}/rag/{rag['id']}", headers=csrf(env["owner"]))
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "pipeline_in_use"


# ---- http_get (US-1.18.3)
@pytest.mark.ac("US-1.18.3/1")
def test_http_get_needs_an_allowlist_and_refuses_private_hosts(env: dict[str, Any]) -> None:
    p = make_prompt(env)
    url = env["mock"].replace("/v1", "") + "/page"
    host = "127.0.0.1"

    # No allowed host: refused, and the agent still answers from the refusal.
    a = make_agent(env, p["id"], tools=[tool("http_get")])
    _, queued = start_chat(env, a["id"], f"Fetch {url}")
    run = finished(env, queued["id"])
    step = next(s for s in run["step_list"] if s["kind"] == "tool")
    assert step["status"] == "error"
    assert step["error_code"] == "host_not_allowed"

    # Allowed host, but the address is private and the deployment does not allow that.
    b = make_agent(env, p["id"], tools=[tool("http_get", allowed_hosts=[host])])
    _, queued = start_chat(env, b["id"], f"Fetch {url}")
    step = next(s for s in finished(env, queued["id"])["step_list"] if s["kind"] == "tool")
    assert step["error_code"] == "address_not_allowed"
    assert mock_provider.HITS["hit"] == 0


# ---- limits (US-1.18.1 #2)
@pytest.mark.ac("US-1.18.1/2")
def test_limits_end_the_run_failed_with_the_reason(env: dict[str, Any]) -> None:
    p = make_prompt(env)
    steps = make_agent(
        env, p["id"], model_key="loop", tools=[tool("current_time")], limits={"max_steps": 2}
    )
    _, queued = start_chat(env, steps["id"], "go")
    run = finished(env, queued["id"])
    assert run["status"] == "failed"
    assert run["error_code"] == "limit_exceeded:max_steps"
    assert "2 model calls" in run["error"]
    assert run["steps"] == 4  # two model calls and their two tool calls
    assert run["tokens_in"] == sum(s["tokens_in"] for s in run["step_list"])

    cost = make_agent(
        env, p["id"], model_key="heavy", tools=[tool("current_time")], limits={"max_cost_usd": 0.01}
    )
    _, queued = start_chat(env, cost["id"], "go")
    run = finished(env, queued["id"])
    assert run["status"] == "failed"
    assert run["error_code"] == "limit_exceeded:max_cost"
    assert got_events_end(env, run["id"]) == "error"


def got_events_end(env: dict[str, Any], run_id: str) -> str:
    return events(env, run_id)[-1]["type"]


@pytest.mark.ac("US-1.18.1/2")
def test_timeout_limit(env: dict[str, Any]) -> None:
    p = make_prompt(env)
    a = make_agent(env, p["id"], model_key="hang", limits={"timeout_s": 10})
    _, queued = start_chat(env, a["id"], "hello")
    run = finished(env, queued["id"], timeout=45)
    assert run["status"] == "failed"
    assert run["error_code"] == "limit_exceeded:timeout"


# ---- stop button (US-1.19.1)
@pytest.mark.ac("US-1.19.1/1")
def test_stop_cancels_a_running_run(env: dict[str, Any]) -> None:
    p = make_prompt(env)
    a = make_agent(env, p["id"], model_key="slow")
    _, queued = start_chat(env, a["id"], "Please write a long answer slowly for the stop test")
    wait_for(lambda: get_run(env, queued["id"])["status"] == "running", what="running")
    time.sleep(0.8)
    c = env["owner"]
    out = ok(c.post(f"{env['base']}/runs/{queued['id']}/cancel", headers=csrf(c)))
    assert out["status"] in ("running", "cancelled")
    run = finished(env, queued["id"], timeout=20)
    assert run["status"] == "cancelled"
    assert run["error_code"] == "cancelled"
    assert events(env, run["id"])[-1]["type"] == "error"
    # Stopping a finished run changes nothing.
    again = ok(c.post(f"{env['base']}/runs/{queued['id']}/cancel", headers=csrf(c)))
    assert again["status"] == "cancelled"


# ---- who sees what (US-1.20.1 #3, spec role matrix)
@pytest.mark.ac("US-1.20.1/3")
def test_end_users_chat_but_see_only_their_runs(env: dict[str, Any]) -> None:
    p = make_prompt(env)
    a = make_agent(env, p["id"])
    base = env["base"]

    # Drafts are for Builders: an end user needs a published agent.
    c = env["end_user"]
    denied = c.post(f"{base}/conversations", headers=csrf(c), json={"agent_id": a["id"]})
    assert denied.status_code == 409
    assert denied.json()["code"] == "agent_not_published"
    publish(env, a["id"])
    assert [x["name"] for x in ok(c.get(f"{base}/agents/available"))] == [a["name"]]
    assert c.get(f"{base}/agents").status_code == 403  # the studio is for Builders

    _, mine = start_chat(env, a["id"], "hello from the end user", who="end_user")
    owner_conv, theirs = start_chat(env, a["id"], "hello from the owner")
    finished(env, mine["id"], who="end_user")
    finished(env, theirs["id"])

    own = ok(c.get(f"{base}/runs"))["items"]
    assert [r["id"] for r in own] == [mine["id"]]
    assert c.get(f"{base}/runs/{theirs['id']}").status_code == 404
    assert c.get(f"{base}/runs/{theirs['id']}/events").status_code == 404
    assert c.post(f"{base}/runs/{theirs['id']}/cancel", headers=csrf(c)).status_code == 404
    assert c.get(f"{base}/usage").status_code == 403

    # The Project Admin and the Viewer (run.read) see every run; the Viewer cannot chat.
    everyone = {r["id"] for r in ok(env["owner"].get(f"{base}/runs"))["items"]}
    assert {mine["id"], theirs["id"]} <= everyone
    v = env["viewer"]
    seen = {r["id"] for r in ok(v.get(f"{base}/runs"))["items"]}
    assert {mine["id"], theirs["id"]} <= seen
    assert get_run(env, mine["id"], "viewer")["input"] == "hello from the end user"
    assert (
        v.post(f"{base}/conversations", headers=csrf(v), json={"agent_id": a["id"]}).status_code
        == 403
    )
    only_mine = ok(env["owner"].get(f"{base}/runs", params={"user_id": env["end_user_id"]}))
    assert {r["id"] for r in only_mine["items"]} == {mine["id"]}

    # Another user's conversation is not visible either.
    listed = {x["id"] for x in ok(c.get(f"{base}/conversations"))}
    assert owner_conv["id"] not in listed
    assert c.get(f"{base}/conversations/{owner_conv['id']}").status_code == 404


# ---- feedback (US-1.19.2)
@pytest.mark.ac("US-1.19.2/1")
def test_feedback_is_linked_to_the_run(env: dict[str, Any]) -> None:
    p = make_prompt(env)
    a = make_agent(env, p["id"])
    publish(env, a["id"])
    _, queued = start_chat(env, a["id"], "hello", who="end_user")
    run = finished(env, queued["id"], who="end_user")
    c, base = env["end_user"], env["base"]
    r = ok(
        c.put(
            f"{base}/runs/{run['id']}/feedback",
            headers=csrf(c),
            json={"rating": "down", "comment": "Not what I asked"},
        )
    )
    assert (r["rating"], r["comment"]) == ("down", "Not what I asked")
    ok(c.put(f"{base}/runs/{run['id']}/feedback", headers=csrf(c), json={"rating": "up"}))
    got = get_run(env, run["id"], "end_user")
    assert got["feedback"]["rating"] == "up"
    # Other users cannot rate it.
    o = env["owner"]
    assert (
        o.put(
            f"{base}/runs/{run['id']}/feedback", headers=csrf(o), json={"rating": "up"}
        ).status_code
        == 404
    )
    bad = c.put(f"{base}/runs/{run['id']}/feedback", headers=csrf(c), json={"rating": "meh"})
    assert bad.status_code == 422


# ---- AC1: switching the alias changes the provider, the agent stays the same
@pytest.mark.ac("US-1.18.2/3")
def test_alias_switch_changes_the_model_without_touching_the_agent(
    env: dict[str, Any], admin: TestClient
) -> None:
    p = make_prompt(env)
    c, base = env["owner"], env["base"]
    first = env["models"]["agent"]
    other = model(admin, env["provider"]["id"], "a-agent", all_projects=True, **CHAT)
    body = {
        "name": f"Aliased {uid()}",
        "description": "uses an alias",
        "config": {"prompt": {"prompt_id": p["id"]}, "model": "fast"},
    }
    alias(c, "fast", [first["id"]], url=base)
    a = ok(c.post(f"{base}/agents", headers=csrf(c), json=body), 201)
    _, q1 = start_chat(env, a["id"], "hello")
    r1 = finished(env, q1["id"])
    alias(c, "fast", [other["id"]], url=base)
    _, q2 = start_chat(env, a["id"], "hello")
    r2 = finished(env, q2["id"])
    assert r1["status"] == r2["status"] == "succeeded"
    assert r1["step_list"][0]["model_name"] == first["name"]
    assert r2["step_list"][0]["model_name"] == other["name"]
    assert a["latest_version"] == 1  # the agent was not changed


# ---- AC6 at agent level: a killed worker resumes from the last checkpoint
@pytest.mark.ac("US-1.18.2/4")
def test_killed_worker_resumes_from_the_checkpoint(
    env: dict[str, Any], settings: Settings, client: TestClient
) -> None:
    """A second worker process serves the queue of this test; it is killed while the model
    streams its final answer, after the tool ran. The tool must not run twice."""
    queue = f"runs-kill-{uuid.uuid4().hex[:6]}"
    mock_url = env["mock"].replace("/v1", "")
    hits_before = mock_provider.HITS["hit"]
    # `a-agent-slowstream` on the shared (thread) worker would also pick the run up, so the
    # project in this test is served only by the queue of the subprocess: the API starts runs on
    # `settings.temporal_runs_queue`, therefore this test points a copy of the app at `queue`.
    from forge.agents.service import RunStarter

    starter = RunStarter(settings.model_copy(update={"temporal_runs_queue": queue}))
    client.app.state.services.agents._starter = starter  # type: ignore[attr-defined]
    environment = os.environ | {
        "FORGE_DATABASE_URL": settings.database_url.get_secret_value(),
        "FORGE_REDIS_URL": settings.redis_url,
        "FORGE_MASTER_KEY": MASTER_KEY,
        "FORGE_SEARCH_URL": settings.search_url,
        "FORGE_SEARCH_PASSWORD": settings.search_password.get_secret_value(),  # type: ignore[union-attr]
        "FORGE_SEARCH_CA_FILE": settings.search_ca_file or "",
        "FORGE_SEARCH_INDEX": settings.search_index,
        "FORGE_TEMPORAL_ADDRESS": settings.temporal_address,
        "FORGE_TEMPORAL_RUNS_QUEUE": queue,
        "FORGE_AGENT_ALLOW_PRIVATE_HOSTS": "true",
        "FORGE_METRICS_PORT": "0",
    }

    def spawn() -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [sys.executable, "-m", "forge.cli", "worker", "runs"],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    first = spawn()
    try:
        p = make_prompt(env)
        a = make_agent(
            env,
            p["id"],
            model_key="slow",
            tools=[tool("http_get", allowed_hosts=["127.0.0.1"])],
        )
        _, queued = start_chat(env, a["id"], f"Fetch {mock_url}/hit please")
        # The tool has run (one hit) and the final answer is streaming (0.4 s per word).
        wait_for(lambda: mock_provider.HITS["hit"] >= 1, timeout=90, what="the tool call")
        wait_for(
            lambda: any(s["kind"] == "tool" for s in get_run(env, queued["id"])["step_list"]),
            what="tool step",
        )
        time.sleep(0.6)
        killed_at = time.time()
        first.send_signal(signal.SIGKILL)
        first.wait(timeout=10)
        assert get_run(env, queued["id"])["status"] == "running"
        second = spawn()
        try:
            run = finished(env, queued["id"], timeout=120)
        finally:
            second.send_signal(signal.SIGTERM)
            second.wait(timeout=30)
        assert run["status"] == "succeeded", run
        # Kill to finished, including the new worker's start (about 20 s) and the re-streamed
        # answer (about 10 s of mock streaming): reported in docs/architecture/m1.5-agent.md.
        print(f"AGENT_RECOVERY_S={time.time() - killed_at:.1f}")
        assert mock_provider.HITS["hit"] == hits_before + 1  # the tool did not run again
        names = [(s["kind"], s["name"]) for s in run["step_list"]]
        assert names == [("llm", "model"), ("tool", "http_get"), ("llm", "model")]
        got = events(env, run["id"])
        assert "run_resumed" in [e["type"] for e in got]
        # Continued from the checkpoint: tool-call model step once, the killed answer once, and
        # the resumed answer once. A restart from scratch would call the model four times.
        chats = [r for r in mock_provider.RECORDS if r["path"].endswith("/chat/completions")]
        assert len(chats) == 3
    finally:
        if first.poll() is None:
            first.kill()
        client.app.state.services.agents._starter = RunStarter(settings)  # type: ignore[attr-defined]


def test_tool_log_returns_the_stored_result_on_a_repeat(
    env: dict[str, Any], client: TestClient
) -> None:
    from forge.agents.models import AgentRun  # noqa: F401 - table must exist
    from forge.agents.tools import ToolContext, ToolLog, ToolResult
    from forge.gateway.service import Caller

    p = make_prompt(env)
    a = make_agent(env, p["id"])
    _, queued = start_chat(env, a["id"], "hello")
    finished(env, queued["id"])
    svc = client.app.state.services  # type: ignore[attr-defined]
    runs = [uuid.UUID(queued["id"]), uuid.UUID(env["project"]["id"])]
    ctx = ToolContext(Caller(runs[1], None), runs[0])
    log = ToolLog(client.app.state.sessions)  # type: ignore[attr-defined]
    calls = 0

    async def effect() -> ToolResult:
        nonlocal calls
        calls += 1
        return ToolResult("ok", "sent", output={"n": calls})

    async def twice() -> tuple[Any, Any]:
        a1 = await log.run_once(ctx, None, f"run:{runs[0]}:call_x", "demo", effect)
        a2 = await log.run_once(ctx, None, f"run:{runs[0]}:call_x", "demo", effect)
        return a1, a2

    (r1, rep1), (r2, rep2) = client.portal.call(twice)  # type: ignore[attr-defined]
    assert calls == 1
    assert (rep1, rep2) == (False, True)
    assert r1.output == r2.output == {"n": 1}
    assert svc is not None


# ---- telemetry (US-1.20.2)
@pytest.mark.ac("US-1.20.2/1")
def test_metrics_and_trace_ids(env: dict[str, Any]) -> None:
    from prometheus_client import generate_latest

    p = make_prompt(env)
    a = make_agent(env, p["id"], tools=[tool("current_time")])
    _, queued = start_chat(env, a["id"], "What time is it?")
    run = finished(env, queued["id"])
    assert len(run["trace_id"]) == 32  # a W3C trace id
    text = generate_latest().decode()
    assert 'forge_agent_runs_total{status="succeeded"}' in text
    assert 'forge_tool_calls_total{status="ok",tool="current_time"}' in text
    assert "forge_gateway_calls_total" in text
    assert "forge_http_requests_total" in text
    # No ids or content in labels: routes are templates.
    assert "/runs/{run_id}" in text or "/runs/{run_id}/events" in text
    assert queued["id"] not in text


def test_checkpoints_of_old_runs_are_purged(env: dict[str, Any], client: TestClient) -> None:
    """Checkpoints live for 7 days after a run ends (ADR-004), then go; younger ones stay."""
    from forge.agents.runtime import purge_checkpoints

    p = make_prompt(env)
    a = make_agent(env, p["id"])
    _, old = start_chat(env, a["id"], "hello")
    _, recent = start_chat(env, a["id"], "hello again")
    finished(env, old["id"])
    finished(env, recent["id"])

    def count(run_id: str) -> int:
        rows = admin_rows(
            client, f"SELECT count(*) FROM langgraph.checkpoints WHERE thread_id = 'run:{run_id}'"
        )
        return int(rows[0][0])

    assert count(old["id"]) > 0
    assert count(recent["id"]) > 0
    admin_rows_exec(
        client,
        f"UPDATE agent_run SET finished_at = now() - interval '8 days' WHERE id = '{old['id']}'",
    )
    sessions = client.app.state.sessions  # type: ignore[attr-defined]
    removed = client.portal.call(purge_checkpoints, sessions)  # type: ignore[attr-defined]
    assert removed > 0
    assert count(old["id"]) == 0
    assert count(recent["id"]) > 0


def admin_rows_exec(client: TestClient, statement: str) -> None:
    from sqlalchemy import text

    from forge.core.db import admin_scope

    async def go() -> None:
        async with admin_scope(client.app.state.sessions) as s:  # type: ignore[attr-defined]
            await s.execute(text(statement))

    client.portal.call(go)  # type: ignore[attr-defined]


def test_builders_chat_on_the_published_version_unless_they_pick_another(
    env: dict[str, Any],
) -> None:
    p = make_prompt(env)
    a = make_agent(env, p["id"])
    c, base = env["owner"], env["base"]
    publish(env, a["id"])
    ok(
        c.post(
            f"{base}/agents/{a['id']}/versions",
            headers=csrf(c),
            json={"config": {"prompt": {"prompt_id": p["id"], "version": 1}, "tools": []}},
        ),
        201,
    )

    def open_chat(**extra: Any) -> Any:
        return ok(
            c.post(
                f"{base}/conversations",
                headers=csrf(c),
                json={"agent_id": a["id"]} | extra,
            ),
            201,
        )

    assert open_chat()["agent_version"] == 1  # the published one, not the newer draft
    assert open_chat(version=2)["agent_version"] == 2
