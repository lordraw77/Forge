"""M2.5 `tool_args` stage: a guardrail rule can stop a tool call before it runs (US-2.11.x)."""

from __future__ import annotations

import os
import time
from typing import Any

import pytest

from tests.integration.test_agents import (
    admin,
    client,
    env,
    finished,
    make_agent,
    make_prompt,
    mock,
    reset_mock,
    settings,
    start_chat,
    tool,
    workers,
)
from tests.integration.test_gateway import ok, uid
from tests.integration.test_platform_core import csrf

pytestmark = pytest.mark.skipif(
    not os.environ.get("FORGE_TEST_S3_ENDPOINT"), reason="needs S3, OpenSearch and Temporal"
)
__all__ = ["admin", "client", "env", "mock", "reset_mock", "settings", "workers"]


def test_tool_arguments_are_checked_before_the_call(env: dict[str, Any]) -> None:
    p = make_prompt(env)
    a = make_agent(env, p["id"], tools=[tool("current_time")])
    owner, base = env["owner"], env["base"]
    rules = [
        {
            "type": "banned_topics",
            "stage": ["tool_args"],
            "action": "block",
            "params": {"terms": ["Europe/Rome"]},
        }
    ]
    ok(owner.put(f"{base}/guardrails/policy", headers=csrf(owner), json={"rules": rules}))
    _, queued = start_chat(env, a["id"], "What time is it in Rome?")
    run = finished(env, queued["id"])
    steps = {s["name"]: s for s in run["step_list"] if s["kind"] == "tool"}
    assert steps["current_time"]["status"] == "error"
    assert steps["current_time"]["error_code"] == "guardrail_blocked"
    assert "Europe/Rome" not in str(run["output"]).split("blocked")[0]  # never ran
    events = ok(owner.get(f"{base}/guardrails/events"))
    assert events[0]["stage"] == "tool_args"
    assert events[0]["source"] == "tool:current_time"
    # Remove the policy: the same question works again
    ok(owner.put(f"{base}/guardrails/policy", headers=csrf(owner), json={"rules": []}))


def test_an_agent_adds_its_own_rules_to_the_projects(env: dict[str, Any]) -> None:
    p = make_prompt(env)
    owner, base = env["owner"], env["base"]
    ok(owner.put(f"{base}/guardrails/policy", headers=csrf(owner), json={"rules": []}))
    time.sleep(5.5)  # the workers cache a project's policy for 5 s (POLICY_TTL_S)
    own = [
        {
            "type": "banned_topics",
            "stage": ["tool_args"],
            "action": "block",
            "params": {"terms": ["Europe/Rome"]},
        }
    ]
    strict = make_agent(env, p["id"], tools=[tool("current_time")], guardrails=own)
    plain = make_agent(env, p["id"], tools=[tool("current_time")])
    _, q1 = start_chat(env, strict["id"], "What time is it in Rome?")
    run = finished(env, q1["id"])
    tool_step = next(s for s in run["step_list"] if s["kind"] == "tool")
    assert tool_step["error_code"] == "guardrail_blocked"  # the project has no policy at all
    _, q2 = start_chat(env, plain["id"], "What time is it in Rome?")
    ran = finished(env, q2["id"])
    assert next(s for s in ran["step_list"] if s["kind"] == "tool")["status"] == "ok"
    # invalid agent rules are refused when the version is saved
    bad = {"type": "json_schema", "action": "block"}  # no schema
    r = owner.post(
        f"{base}/agents",
        headers=csrf(owner),
        json={
            "name": f"Bad {uid()}",
            "description": "x",
            "config": {"prompt": {"prompt_id": p["id"], "version": 1}, "guardrails": [bad]},
        },
    )
    assert r.status_code == 422, r.text
