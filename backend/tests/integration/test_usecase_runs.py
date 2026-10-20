"""M2.3b graph engine on Temporal: supervisor and graph runs with real agent, tool and prompt
steps against the mock model and the mock HTTP API (US-2.6.x, US-2.7.1)."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from forge.core.settings import Settings
from tests.integration.test_agents import (
    admin,
    admin_rows,
    client,
    env,
    make_agent,
    make_prompt,
    mock,
    publish,
    reset_mock,
    workers,
)
from tests.integration.test_gateway import ok, uid
from tests.integration.test_platform_core import MASTER_KEY, create_user, csrf, login
from tests.integration.test_rag import wait_for
from tests.integration.test_tools import http, http_tool, mcp, settings, tools_env
from tests.support.mock_http import MockHttp

pytestmark = pytest.mark.skipif(
    not os.environ.get("FORGE_TEST_S3_ENDPOINT"), reason="needs S3, OpenSearch and Temporal"
)
__all__ = [
    "admin",
    "client",
    "env",
    "http",
    "mcp",
    "mock",
    "reset_mock",
    "settings",
    "tools_env",
    "workers",
]
TERMINAL = {"succeeded", "failed", "cancelled"}
_ = MockHttp


@pytest.fixture(scope="module")
def rt(tools_env: dict[str, Any], client: TestClient) -> dict[str, Any]:
    out = dict(tools_env)
    owner, base = out["owner"], out["base"]
    roles = {r["role_type"]: r["id"] for r in ok(owner.get(f"{base}/roles"))}
    out["roles"] = roles
    cat = next(c for c in ok(owner.get(f"{base}/catalogs")) if c["name"] == "Forge.ITOps")
    kat = ok(
        owner.post(
            f"{base}/catalogs/{cat['id']}/categories",
            headers=csrf(owner),
            json={"name": f"Runs {uid()}"},
        ),
        201,
    )["categories"][-1]
    out["filing"] = {"catalog_id": cat["id"], "category_id": kat["id"]}
    out["catalog"], out["category"] = cat, kat
    return out


def agent_ref(env: dict[str, Any], **kw: Any) -> str:
    a = make_agent(env, make_prompt(env)["id"], **kw)
    publish(env, a["id"])
    return f"{a['name']}@1"


def new_uc(
    env: dict[str, Any], pattern: str, definition: dict[str, Any], **extra: Any
) -> dict[str, Any]:
    c = env["builder"]
    body = {
        "name": f"RUN {uid()}",
        "pattern": pattern,
        "definition": definition,
        **env["filing"],
        "input_fields": [{"name": "topic", "type": "text", "required": True}],
    } | extra
    return ok(c.post(f"{env['base']}/use-cases", headers=csrf(c), json=body), 201)


def start(
    env: dict[str, Any],
    uc_id: str,
    inputs: dict[str, Any] | None = None,
    who: str = "builder",
    **kw: Any,
) -> Any:
    c = env[who]
    body = {"inputs": inputs if inputs is not None else {"topic": "x"}, "mode": "test"} | kw
    return c.post(f"{env['base']}/use-cases/{uc_id}/runs", headers=csrf(c), json=body)


def finished(env: dict[str, Any], run_id: str, who: str = "builder", timeout: float = 60) -> Any:
    def done() -> Any:
        r = ok(env[who].get(f"{env['base']}/use-case-runs/{run_id}"))
        return r if r["status"] in TERMINAL else None

    return wait_for(done, timeout=timeout, what=f"use case run {run_id}")


def run_ok(env: dict[str, Any], uc_id: str, inputs: dict[str, Any] | None = None, **kw: Any) -> Any:
    return finished(env, ok(start(env, uc_id, inputs, **kw), 202)["id"])


def keys(run: dict[str, Any]) -> list[str]:
    return [s["step_key"] for s in run["step_list"]]


def node(id: str, type: str, *after: str, **extra: Any) -> dict[str, Any]:
    return {"id": id, "type": type, **({"after": list(after)} if after else {}), **extra}


def test_supervisor_team_runs_in_order(rt: dict[str, Any]) -> None:
    lead, dev = agent_ref(rt), agent_ref(rt)
    uc = new_uc(
        rt,
        "supervisor",
        {
            "agents": [
                {"agent_ref": lead, "task": "Plan the work"},
                {"agent_ref": dev, "task": "Do it"},
            ]
        },
    )
    run = run_ok(rt, uc["id"], {"topic": "invoices"})
    assert run["status"] == "succeeded", run
    assert keys(run) == ["agent_1", "agent_2"]
    assert [s["status"] for s in run["step_list"]] == ["succeeded", "succeeded"]
    assert run["outputs"]["result"] == "pong from a-agent"
    assert run["mode"] == "test"
    assert run["version"] is None
    assert run["steps"] == 2
    assert run["tokens_in"] > 0
    assert run["cost"] >= 0
    first, second = (s["input"]["message"] for s in run["step_list"])
    assert "topic: invoices" in first
    assert first.startswith("Plan the work")
    assert "Previous agent output:\npong from a-agent" in second
    assert [n["id"] for n in run["graph"]["nodes"]] == ["start", "agent_1", "agent_2", "end"]


def test_published_run_pins_the_version_and_follows_catalog_access(rt: dict[str, Any]) -> None:
    owner, b, e, base = rt["owner"], rt["builder"], rt["end_user"], rt["base"]
    uc = new_uc(rt, "supervisor", {"agents": [{"agent_ref": agent_ref(rt), "task": "Answer"}]})
    ok(b.post(f"{base}/use-cases/{uc['id']}/publish", headers=csrf(b), json={}))
    # An End User without access sees nothing, so cannot run it (404, not 403).
    assert start(rt, uc["id"], who="end_user", mode="published").status_code == 404
    grant = {"catalogs": [{"catalog_id": rt["catalog"]["id"], "enabled": True}]}
    ok(
        owner.put(
            f"{base}/roles/{rt['roles']['end_user']}/catalog-access",
            headers=csrf(owner),
            json=grant,
        )
    )
    bad = start(rt, uc["id"], {"nope": 1}, who="end_user", mode="published")
    assert bad.status_code == 422
    assert bad.json()["code"] == "invalid_input"
    assert start(rt, uc["id"], who="end_user", mode="test").status_code == 403
    started = ok(start(rt, uc["id"], who="end_user", mode="published"), 202)
    run = finished(rt, started["id"], who="end_user")
    assert run["status"] == "succeeded"
    assert run["version"] == 1
    assert run["mode"] == "published"
    # A later edit and publish does not touch a finished run; it keeps its graph and version.
    ok(
        b.put(
            f"{base}/use-cases/{uc['id']}",
            headers=csrf(b),
            json={
                **rt["filing"],
                "definition": {"agents": []},
                "description": "later",
            },
        )
    )
    again = ok(b.get(f"{base}/use-case-runs/{run['id']}"))
    assert again["version"] == 1
    assert again["graph"]["nodes"][1]["type"] == "agent"
    assert ok(e.get(f"{base}/use-case-runs/{run['id']}"))["graph"] is None  # end users: no graph
    # End users see their own runs only; a builder sees all.
    mine = ok(e.get(f"{base}/use-case-runs"))
    assert [r["id"] for r in mine] == [run["id"]]
    assert run["id"] in [
        r["id"] for r in ok(b.get(f"{base}/use-case-runs", params={"use_case_id": uc["id"]}))
    ]
    other = ok(start(rt, uc["id"], who="builder", mode="published"), 202)
    assert e.get(f"{base}/use-case-runs/{other['id']}").status_code == 404
    finished(rt, other["id"])
    ok(
        owner.put(
            f"{base}/roles/{rt['roles']['end_user']}/catalog-access",
            headers=csrf(owner),
            json={"catalogs": []},
        )
    )
    # Runs block delete; the use case is deprecated instead.
    blocked = b.delete(f"{base}/use-cases/{uc['id']}", headers=csrf(b))
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "use_case_has_runs"
    # A viewer cannot run anything.
    assert start(rt, uc["id"], who="viewer", mode="published").status_code == 403


def test_not_ready_drafts_are_refused(rt: dict[str, Any]) -> None:
    unpublished = make_agent(rt, make_prompt(rt)["id"])
    uc = new_uc(rt, "supervisor", {"agents": [{"agent_ref": f"{unpublished['name']}@1"}]})
    r = start(rt, uc["id"])
    assert r.status_code == 422
    assert r.json()["code"] == "not_ready"
    graph = new_uc(
        rt,
        "graph",
        {
            "nodes": [
                node("start", "start"),
                node("a", "agent", "start", agent_ref="x@1", input="{ghost}"),
            ]
        },
    )
    r = start(rt, graph["id"])
    assert r.status_code == 422
    assert "undefined_variable" in [i["code"] for i in r.json()["detail"]]
    detail = ok(rt["builder"].get(f"{rt['base']}/use-cases/{graph['id']}"))
    assert "undefined_variable" in [i["code"] for i in detail["issues"]]


def test_graph_with_router_loop_tool_and_prompt(rt: dict[str, Any]) -> None:
    b, base = rt["builder"], rt["base"]
    worker = agent_ref(rt)
    pet = ok(b.post(f"{base}/tools", headers=csrf(b), json=http_tool(rt)), 201)
    ok(b.post(f"{base}/tools/{pet['id']}/publish", headers=csrf(b), json={}))
    prompt = make_prompt(rt)
    ok(b.post(f"{base}/prompts/{prompt['id']}/publish", headers=csrf(b), json={}))
    graph = {
        "nodes": [
            node("start", "start"),
            node("each", "for_each", "start", items="{topic}", max_iterations=5),
            node(
                "work",
                "agent",
                "each.loop",
                agent_ref=worker,
                task="Handle",
                input="{item} #{index}",
                output="done",
            ),
            node(
                "route",
                "router",
                "each.done",
                sections=[
                    {
                        "name": "many",
                        "conditions": [
                            {"field": "done", "op": "contains", "value": "pong from a-agent"}
                        ],
                    }
                ],
            ),
            node(
                "lookup",
                "tool",
                "route.many",
                tool_ref=f"{pet['name']}@1",
                args={"pet_id": "p7"},
                output="pet",
            ),
            node("fallback", "agent", "route.default", agent_ref=worker, input="never"),
            node(
                "say",
                "llm",
                "lookup",
                prompt_ref=f"{prompt['name']}@1",
                variables={"topic": "{pet}", "question": "{done}"},
                output="said",
            ),
            node("end", "end", "say", "fallback", after_mode="any", output="{done}"),
        ]
    }
    uc = new_uc(rt, "graph", graph)
    run = run_ok(rt, uc["id"], {"topic": "alpha\nbeta"})
    assert run["status"] == "succeeded", (run["error_code"], run["error"], run["error_node"])
    assert keys(run) == ["each[0]/work", "each[1]/work", "lookup", "say"]
    out = run["outputs"]
    assert out["result"] == ["pong from a-agent", "pong from a-agent"]
    assert out["skipped"] == ["fallback"]
    assert out["decisions"] == [{"key": "route", "node": "route", "paths": ["many"]}]
    steps = {s["step_key"]: s for s in run["step_list"]}
    assert steps["lookup"]["node_type"] == "tool"
    assert "p7" in str(steps["lookup"]["output"])
    assert steps["say"]["node_type"] == "llm"
    assert steps["each[1]/work"]["input"]["message"].endswith("beta #1")
    # The tool call is in the call log, tied to the run.
    calls = ok(b.get(f"{base}/tool-calls", params={"tool_id": pet["id"]}))
    assert any(c["source"] == "agent" for c in calls)


def test_failures_and_limits_name_the_cause_and_node(rt: dict[str, Any]) -> None:
    worker = agent_ref(rt)
    chain = [node("start", "start")] + [
        node(f"a{i}", "agent", *([f"a{i - 1}"] if i else ["start"]), agent_ref=worker)
        for i in range(4)
    ]
    uc = new_uc(rt, "graph", {"nodes": chain, "limits": {"max_steps": 2}})
    run = run_ok(rt, uc["id"])
    assert run["status"] == "failed"
    assert run["error_code"] == "max_steps"
    assert run["error_node"] == "a2"
    assert run["steps"] == 2
    assert [s["status"] for s in run["step_list"]] == [
        "succeeded",
        "succeeded",
    ]
    approval = new_uc(
        rt,
        "graph",
        {
            "nodes": [
                node("start", "start"),
                node("a", "agent", "start", agent_ref=worker),
                node("ok", "human_approval", "a"),
            ]
        },
    )
    run = run_ok(rt, approval["id"])
    assert run["status"] == "failed"
    assert run["error_code"] == "approval_not_available"
    loop = new_uc(
        rt,
        "graph",
        {
            "nodes": [
                node("start", "start"),
                node("each", "for_each", "start", items="{topic}", max_iterations=2),
                node("w", "agent", "each.loop", agent_ref=worker),
            ]
        },
    )
    run = run_ok(rt, loop["id"], {"topic": "a\nb\nc"})
    assert run["status"] == "failed"
    assert run["error_code"] == "max_iterations_exceeded"
    assert run["steps"] == 0
    # A JSON output that does not match its schema fails the run instead of delivering it.
    bad_json = new_uc(
        rt,
        "supervisor",
        {"agents": [{"agent_ref": worker}]},
        output_type="json",
        output_schema={"type": "object"},
    )
    run = run_ok(rt, bad_json["id"])
    assert run["status"] == "failed"
    assert run["error_code"] == "output_invalid"


def test_tool_error_fails_the_step(rt: dict[str, Any]) -> None:
    b, base = rt["builder"], rt["base"]
    failing = ok(
        b.post(
            f"{base}/tools",
            headers=csrf(b),
            json=http_tool(rt, config={"url": f"{rt['http']}/fail", "params": []}),
        ),
        201,
    )
    ok(b.post(f"{base}/tools/{failing['id']}/publish", headers=csrf(b), json={}))
    uc = new_uc(
        rt,
        "graph",
        {
            "nodes": [
                node("start", "start"),
                node("t", "tool", "start", tool_ref=f"{failing['name']}@1"),
            ]
        },
    )
    run = run_ok(rt, uc["id"])
    assert run["status"] == "failed"
    assert run["error_node"] == "t"
    assert run["step_list"][0]["status"] == "failed"


def test_cancel_stops_a_running_run(rt: dict[str, Any]) -> None:
    b, base = rt["builder"], rt["base"]
    slow = agent_ref(rt, model_key="slow")
    uc = new_uc(
        rt,
        "graph",
        {"nodes": [node("start", "start"), node("a", "agent", "start", agent_ref=slow)]},
    )
    started = ok(start(rt, uc["id"]), 202)
    wait_for(
        lambda: next(
            (
                s
                for s in ok(b.get(f"{base}/use-case-runs/{started['id']}"))["step_list"]
                if s["status"] == "running"
            ),
            None,
        ),
        timeout=30,
        what="the step to start",
    )
    cancelled = ok(b.post(f"{base}/use-case-runs/{started['id']}/cancel", headers=csrf(b), json={}))
    assert cancelled["status"] in {"running", "cancelled"}
    run = finished(rt, started["id"])
    assert run["status"] == "cancelled"
    assert run["step_list"][0]["status"] == "failed"


def test_killed_worker_resumes_without_repeating_finished_steps(
    rt: dict[str, Any], settings: Settings, client: TestClient
) -> None:
    queue = f"runs-uckill-{uuid.uuid4().hex[:6]}"
    from forge.agents.service import RunStarter

    starter = RunStarter(settings.model_copy(update={"temporal_runs_queue": queue}))
    runs = client.app.state.services.usecase_runs  # type: ignore[attr-defined]
    agents = client.app.state.services.agents  # type: ignore[attr-defined]
    runs._starter = agents._starter = starter
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
        quick, slow = agent_ref(rt), agent_ref(rt, model_key="slow")
        uc = new_uc(rt, "supervisor", {"agents": [{"agent_ref": quick}, {"agent_ref": slow}]})
        started = ok(start(rt, uc["id"]), 202)
        b, base = rt["builder"], rt["base"]

        def second_running() -> Any:
            r = ok(b.get(f"{base}/use-case-runs/{started['id']}"))
            return (
                r
                if [s["step_key"] for s in r["step_list"] if s["status"] == "running"]
                == ["agent_2"]
                else None
            )

        before = wait_for(second_running, timeout=90, what="the second step to run")
        first_agent_run = admin_rows(
            client,
            "select agent_run_id from use_case_run_step "
            f"where run_id = '{started['id']}' and step_key = 'agent_1'",
        )[0][0]
        time.sleep(1.5)
        killed_at = time.time()
        first.send_signal(signal.SIGKILL)
        first.wait(timeout=10)
        assert ok(b.get(f"{base}/use-case-runs/{started['id']}"))["status"] == "running"
        second = spawn()
        try:
            run = finished(rt, started["id"], timeout=150)
        finally:
            second.send_signal(signal.SIGTERM)
            second.wait(timeout=30)
        print(f"USECASE_RECOVERY_S={time.time() - killed_at:.1f}")
        assert run["status"] == "succeeded", run
        assert before["step_list"][0]["status"] == "succeeded"
        rows = admin_rows(
            client,
            "select step_key, agent_run_id from use_case_run_step "
            f"where run_id = '{started['id']}' order by ordinal",
        )
        assert [r[0] for r in rows] == ["agent_1", "agent_2"]
        assert rows[0][1] == first_agent_run
        count = admin_rows(
            client,
            f"select count(*) from conversation where title like '[use case] agent_1' and id in "
            f"(select conversation_id from agent_run where id = '{first_agent_run}')",
        )[0][0]
        assert count == 1
    finally:
        if first.poll() is None:
            first.kill()
        runs._starter = agents._starter = RunStarter(settings)


def test_runs_are_invisible_to_other_projects(
    rt: dict[str, Any], admin: TestClient, client: TestClient
) -> None:
    from tests.integration.test_gateway import project

    other = project(admin, client)
    stranger = login(client, other["owner"])
    uc = new_uc(rt, "supervisor", {"agents": [{"agent_ref": agent_ref(rt)}]})
    run = run_ok(rt, uc["id"])
    assert stranger.get(f"{rt['base']}/use-case-runs/{run['id']}").status_code == 404
    mine = f"/api/v1/projects/{other['id']}"
    assert ok(stranger.get(f"{mine}/use-case-runs")) == []
    assert stranger.get(f"{mine}/use-case-runs/{run['id']}").status_code == 404
    _ = create_user
