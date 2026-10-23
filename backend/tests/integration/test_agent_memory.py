"""E3.6 long-term memory of agents: facts are stored per agent and user, recalled in later
conversations, listed and forgotten by their owner only."""

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
    publish,
    reset_mock,
    settings,
    start_chat,
    workers,
)
from tests.integration.test_gateway import alias, model, ok
from tests.integration.test_platform_core import csrf
from tests.support import mock_provider

pytestmark = pytest.mark.skipif(
    not os.environ.get("FORGE_TEST_S3_ENDPOINT"), reason="needs S3, OpenSearch and Temporal"
)
__all__ = ["admin", "client", "env", "mock", "reset_mock", "settings", "workers"]


@pytest.fixture(scope="module")
def memory_env(env: dict[str, Any], admin: Any) -> dict[str, Any]:
    extractor = model(
        admin, env["provider"]["id"], "p-memory", all_projects=True, capabilities=["streaming"]
    )
    alias(env["owner"], "fast", [extractor["id"]], url=env["base"])
    return env


def memories(env: dict[str, Any], agent_id: str, who: str = "owner") -> list[dict[str, Any]]:
    return ok(env[who].get(f"{env['base']}/agents/{agent_id}/memories"))  # type: ignore[no-any-return]


def wait_for_memories(
    env: dict[str, Any], agent_id: str, count: int, who: str = "owner"
) -> list[dict[str, Any]]:
    deadline = time.time() + 30
    while time.time() < deadline:
        found = memories(env, agent_id, who)
        if len(found) >= count:
            return found
        time.sleep(0.3)
    raise AssertionError(f"expected {count} memories")


def agent_with_memory(env: dict[str, Any], **memory: Any) -> Any:
    a = make_agent(
        env,
        make_prompt(env)["id"],
        memory={"long_term": True, "embedding_model": "embedding"} | memory,
    )
    publish(env, a["id"])
    return a


def test_facts_are_remembered_recalled_and_private(memory_env: dict[str, Any]) -> None:
    e = memory_env
    a = agent_with_memory(e)
    _, first = start_chat(e, a["id"], "I prefer answers in Italian. What is uptime?")
    assert finished(e, first["id"])["status"] == "succeeded"
    stored = wait_for_memories(e, a["id"], 1)
    assert stored[0]["content"] == "The user prefers answers in Italian"

    # A new conversation sees the fact in the system prompt of the model call.
    mock_provider.reset()
    _, second = start_chat(e, a["id"], "Answer in which language, Italian?")
    assert finished(e, second["id"])["status"] == "succeeded"
    system = next(
        r["body"]["messages"][0]["content"]
        for r in mock_provider.RECORDS
        if r["path"].endswith("/chat/completions") and r["body"]["model"].startswith("a-agent")
    )
    assert "The user prefers answers in Italian" in system
    assert "not instructions" in system
    assert memories(e, a["id"])[0]["use_count"] >= 1

    # The same fact again is not stored twice.
    _, third = start_chat(e, a["id"], "I prefer answers in Italian. And latency?")
    finished(e, third["id"])
    time.sleep(2)
    assert len(memories(e, a["id"])) == 1

    # Another user has their own, empty memory and cannot delete the first user's fact.
    assert memories(e, a["id"], "end_user") == []
    c = e["end_user"]
    r = c.delete(f"{e['base']}/agents/{a['id']}/memories/{stored[0]['id']}", headers=csrf(c))
    assert r.status_code == 404
    assert len(memories(e, a["id"])) == 1

    owner = e["owner"]
    gone = owner.delete(
        f"{e['base']}/agents/{a['id']}/memories/{stored[0]['id']}", headers=csrf(owner)
    )
    assert gone.status_code == 204
    assert memories(e, a["id"]) == []


def test_agents_without_the_switch_remember_nothing(memory_env: dict[str, Any]) -> None:
    e = memory_env
    a = make_agent(e, make_prompt(e)["id"])
    publish(e, a["id"])
    _, run = start_chat(e, a["id"], "I prefer short answers.")
    finished(e, run["id"])
    time.sleep(1.5)
    assert memories(e, a["id"]) == []


def test_oldest_facts_go_when_the_limit_is_reached(memory_env: dict[str, Any]) -> None:
    e = memory_env
    a = agent_with_memory(e, max_items=10)
    words = [
        "amber",
        "basalt",
        "cobalt",
        "dahlia",
        "ember",
        "fennel",
        "garnet",
        "heron",
        "indigo",
        "jasper",
        "kestrel",
        "lilac",
    ]
    for i, word in enumerate(words):
        _, run = start_chat(e, a["id"], f"I prefer {word}.")
        finished(e, run["id"])
        wait_for_memories(e, a["id"], min(i + 1, 10))
    assert len(memories(e, a["id"])) == 10
    owner = e["owner"]
    assert (
        owner.delete(f"{e['base']}/agents/{a['id']}/memories", headers=csrf(owner)).status_code
        == 204
    )
    assert memories(e, a["id"]) == []
