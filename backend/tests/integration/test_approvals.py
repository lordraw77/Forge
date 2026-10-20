"""M2.4 human in the loop: approval nodes, `hitl` nodes and tools that require approval pause a
run on Temporal until a person decides, from the API or from a one-time email link (US-2.10.x)."""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from forge.core.settings import Settings
from tests.integration.test_agents import admin, client, env, make_prompt, mock, reset_mock, workers
from tests.integration.test_gateway import ok
from tests.integration.test_platform_core import csrf, new_client
from tests.integration.test_rag import wait_for
from tests.integration.test_tools import create_tool, http, mcp, settings, tools_env
from tests.integration.test_usecase_runs import agent_ref, finished, new_uc, node, rt, start
from tests.support.mock_http import SEEN, MockHttp
from tests.support.smtp_sink import SmtpSink

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
    "rt",
    "settings",
    "tools_env",
    "workers",
]
_ = (MockHttp, make_prompt)


@pytest.fixture(scope="module")
def smtp(settings: Settings, rt: dict[str, Any]) -> Iterator[SmtpSink]:
    """Approval emails go to a local sink; the workers share this Settings object."""
    with SmtpSink() as sink:
        settings.smtp_host, settings.smtp_port = "127.0.0.1", sink.port
        settings.smtp_starttls, settings.public_url = False, "http://forge.test"
        yield sink
        settings.smtp_host = ""


def expire(client: TestClient, settings: Settings, approval_id: str) -> int:
    """Make an approval overdue (as the platform role, which sees every project)."""
    from sqlalchemy import text

    from forge.core.db import create_engine

    async def go() -> int:
        engine = create_engine(settings, app_role=False)
        try:
            async with engine.begin() as conn:
                await conn.execute(text("SET LOCAL ROLE forge_admin"))
                done = await conn.execute(
                    text(
                        "UPDATE approval SET expires_at = now() - interval '1 second' WHERE id = :i"
                    ),
                    {"i": approval_id},
                )
                return int(done.rowcount)
        finally:
            await engine.dispose()

    return int(client.portal.call(go))  # type: ignore[attr-defined]


def waiting(env: dict[str, Any], run_id: str, who: str = "builder", timeout: float = 60) -> Any:
    def hit() -> Any:
        r = ok(env[who].get(f"{env['base']}/use-case-runs/{run_id}"))
        if r["status"] in {"failed", "cancelled", "succeeded"}:
            raise AssertionError(f"run ended: {r['status']} {r.get('error_code')} {r.get('error')}")
        return r if r["status"] == "waiting_approval" else None

    return wait_for(hit, timeout=timeout, what=f"run {run_id} waiting for approval")


def pending(env: dict[str, Any], who: str = "owner") -> list[dict[str, Any]]:
    return ok(env[who].get(f"{env['base']}/approvals"))


def approval_of(env: dict[str, Any], run_id: str, who: str = "owner") -> dict[str, Any]:
    def hit() -> Any:
        return next((a for a in pending(env, who) if a["run_id"] == run_id), None)

    return wait_for(hit, timeout=30, what="the approval")


def decide(env: dict[str, Any], approval_id: str, who: str = "owner", **body: Any) -> Any:
    c = env[who]
    return c.post(
        f"{env['base']}/approvals/{approval_id}/decision",
        headers=csrf(c),
        json={"decision": "approve"} | body,
    )


def review_graph(env: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "nodes": [
            node("start", "start"),
            node("a", "agent", "start", agent_ref=agent_ref(env), output="draft"),
            node(
                "ok",
                "human_approval",
                "a",
                input="Release notes: {draft}",
                instructions="Check the tone",
                output="final",
                **extra,
            ),
            node("end", "end", "ok", output="{final}"),
        ]
    }


def test_approval_node_pauses_the_run_and_resumes_with_the_edit(rt: dict[str, Any]) -> None:
    base, e, b, o = rt["base"], rt["end_user"], rt["builder"], rt["owner"]
    uc = new_uc(rt, "graph", review_graph(rt))
    ok(b.post(f"{base}/use-cases/{uc['id']}/publish", headers=csrf(b), json={}))
    ok(
        o.put(
            f"{base}/roles/{rt['roles']['end_user']}/catalog-access",
            headers=csrf(o),
            json={"catalogs": [{"catalog_id": rt["catalog"]["id"], "enabled": True}]},
        )
    )
    started = ok(
        e.post(
            f"{base}/use-cases/{uc['id']}/runs",
            headers=csrf(e),
            json={"inputs": {"topic": "x"}},
        ),
        202,
    )
    run = waiting(rt, started["id"], who="end_user")
    # The starter sees that it waits, not what is under review.
    assert run["status"] == "waiting_approval"
    approval_step = next(s for s in run["step_list"] if s["step_key"] == "ok:approval")
    assert approval_step["status"] == "running"
    assert approval_step["input"] is None
    assert e.get(f"{base}/approvals").status_code == 403
    item = approval_of(rt, started["id"])
    assert item["kind"] == "review"
    assert item["status"] == "pending"
    assert item["use_case_name"] == uc["name"]
    detail = ok(b.get(f"{base}/approvals/{item['id']}"))
    assert "Release notes: pong" in detail["content"]["text"]
    assert detail["instructions"] == "Check the tone"
    assert e.get(f"{base}/approvals/{item['id']}").status_code == 403
    # Bad decisions.
    assert decide(rt, item["id"], decision="reject").status_code == 422  # feedback is required
    assert decide(rt, item["id"], edited={"a": 1}).status_code == 422  # text expected
    assert decide(rt, item["id"], edited="x" * 70_000).status_code == 422
    assert decide(rt, item["id"], who="end_user").status_code == 403
    assert decide(rt, "00000000-0000-7000-8000-0000000000aa").status_code == 404
    done = ok(decide(rt, item["id"], edited="Reviewed notes", comment="fixed the tone"))
    assert done["status"] == "approved"
    assert done["decided_via"] == "ui"
    assert decide(rt, item["id"]).status_code == 409
    run = finished(rt, started["id"], who="end_user")
    assert run["status"] == "succeeded", run
    assert run["outputs"]["result"] == "Reviewed notes"
    full = ok(b.get(f"{base}/use-case-runs/{started['id']}"))
    step = next(s for s in full["step_list"] if s["step_key"] == "ok~approval")
    assert step["status"] == "succeeded"
    assert step["output"]["value"] == "Reviewed notes"
    assert item["id"] not in [a["id"] for a in pending(rt)]
    decided = ok(o.get(f"{base}/approvals", params={"pending": False}))
    assert any(a["id"] == item["id"] and a["status"] == "approved" for a in decided)


def test_rejection_fails_the_run_with_the_feedback(rt: dict[str, Any]) -> None:
    base, b = rt["base"], rt["builder"]
    uc = new_uc(rt, "graph", review_graph(rt))
    started = ok(start(rt, uc["id"]), 202)
    item = approval_of(rt, started["id"])
    ok(decide(rt, item["id"], decision="reject", comment="not for customers"))
    run = finished(rt, started["id"])
    assert run["status"] == "failed"
    assert run["error_code"] == "approval_rejected"
    assert "not for customers" in run["error"]
    assert run["error_node"] == "ok"
    assert ok(b.get(f"{base}/approvals/{item['id']}"))["status"] == "rejected"


def test_hitl_agent_is_rerun_with_the_feedback_until_approved(rt: dict[str, Any]) -> None:
    graph = {
        "nodes": [
            node("start", "start"),
            node("a", "agent", "start", agent_ref=agent_ref(rt), task="Write", hitl=True),
        ]
    }
    uc = new_uc(rt, "graph", graph)
    started = ok(start(rt, uc["id"]), 202)
    first = approval_of(rt, started["id"])
    assert "pong" in ok(rt["owner"].get(f"{rt['base']}/approvals/{first['id']}"))["content"]["text"]
    ok(decide(rt, first["id"], decision="reject", comment="make it formal"))
    second = wait_for(
        lambda: next((a for a in pending(rt) if a["run_id"] == started["id"]), None),
        timeout=30,
        what="the second approval",
    )
    assert second["id"] != first["id"]
    ok(decide(rt, second["id"]))
    run = finished(rt, started["id"])
    assert run["status"] == "succeeded", run
    agent_steps = [s for s in run["step_list"] if s["node_type"] == "agent"]
    assert [s["step_key"] for s in agent_steps] == ["a", "a~r1"]
    assert "make it formal" in agent_steps[1]["input"]["message"]
    assert run["steps"] == 2


def tool_graph(rt: dict[str, Any], **extra: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    b, base = rt["builder"], rt["base"]
    tool = create_tool(rt, requires_approval=True, **extra)
    ok(b.post(f"{base}/tools/{tool['id']}/publish", headers=csrf(b), json={}))
    graph = {
        "nodes": [
            node("start", "start"),
            node(
                "fetch",
                "tool",
                "start",
                tool_ref=f"{tool['name']}@1",
                args={"pet_id": "rex"},
                output="pet",
            ),
            node("end", "end", "fetch", output="{pet}"),
        ]
    }
    return tool, graph


def test_tool_that_requires_approval_waits_and_uses_the_edited_arguments(
    rt: dict[str, Any],
) -> None:
    _, graph = tool_graph(rt)
    uc = new_uc(rt, "graph", graph)
    SEEN.clear()
    started = ok(start(rt, uc["id"]), 202)
    item = approval_of(rt, started["id"])
    detail = ok(rt["owner"].get(f"{rt['base']}/approvals/{item['id']}"))
    assert detail["kind"] == "tool_call"
    assert detail["content"]["args"] == {"pet_id": "rex"}
    assert not [c for c in SEEN if c["path"].startswith("/pets/")]  # nothing was called yet
    assert decide(rt, item["id"], edited="text").status_code == 422  # arguments are an object
    ok(decide(rt, item["id"], edited={"pet_id": "fido"}))
    run = finished(rt, started["id"])
    assert run["status"] == "succeeded", run
    assert [c["path"] for c in SEEN if c["path"].startswith("/pets/")] == ["/pets/fido"]
    # A rejected call never reaches the API.
    SEEN.clear()
    started = ok(start(rt, uc["id"]), 202)
    item = approval_of(rt, started["id"])
    ok(decide(rt, item["id"], decision="reject", comment="wrong pet"))
    run = finished(rt, started["id"])
    assert run["error_code"] == "approval_rejected"
    assert not [c for c in SEEN if c["path"].startswith("/pets/")]


def test_assignees_limit_who_sees_and_decides(rt: dict[str, Any]) -> None:
    base, b, o = rt["base"], rt["builder"], rt["owner"]
    uc = new_uc(rt, "graph", review_graph(rt, assignees=["Operator"]))
    started = ok(start(rt, uc["id"]), 202)
    item = approval_of(rt, started["id"], who="owner")  # Project Admins always may
    assert pending(rt, "builder") == [] or item["id"] not in [
        a["id"] for a in pending(rt, "builder")
    ]
    assert b.get(f"{base}/approvals/{item['id']}").status_code == 404
    assert decide(rt, item["id"], who="builder").status_code == 404
    ok(decide(rt, item["id"], who="owner"))
    assert finished(rt, started["id"])["status"] == "succeeded"
    assert o.get(f"{base}/approvals/{item['id']}").status_code == 200


def test_cancelling_a_waiting_run_cancels_its_approval(rt: dict[str, Any]) -> None:
    base, b = rt["base"], rt["builder"]
    uc = new_uc(rt, "graph", review_graph(rt))
    started = ok(start(rt, uc["id"]), 202)
    item = approval_of(rt, started["id"])
    ok(b.post(f"{base}/use-case-runs/{started['id']}/cancel", headers=csrf(b)))
    run = finished(rt, started["id"])
    assert run["status"] == "cancelled"

    def cancelled() -> Any:
        d = ok(b.get(f"{base}/approvals/{item['id']}"))
        return d if d["status"] == "cancelled" else None

    wait_for(cancelled, timeout=20, what="approval cancelled")
    assert decide(rt, item["id"]).status_code == 409


def test_an_approval_that_nobody_decides_expires_and_fails_the_run(
    rt: dict[str, Any], settings: Settings, client: TestClient
) -> None:
    uc = new_uc(rt, "graph", review_graph(rt, timeout_hours=1))
    started = ok(start(rt, uc["id"]), 202)
    item = approval_of(rt, started["id"])
    assert expire(client, settings, item["id"])
    run = finished(rt, started["id"])
    assert run["status"] == "failed"
    assert run["error_code"] == "approval_timeout"
    assert decide(rt, item["id"]).status_code == 409


def test_waiting_for_a_person_does_not_use_up_the_run_time_limit(rt: dict[str, Any]) -> None:
    graph = review_graph(rt)
    graph["limits"] = {"timeout_s": 5}
    uc = new_uc(rt, "graph", graph)
    started = ok(start(rt, uc["id"]), 202)
    item = approval_of(rt, started["id"])
    time.sleep(8)  # longer than the limit of the work
    assert ok(rt["builder"].get(f"{rt['base']}/use-case-runs/{started['id']}"))["status"] == (
        "waiting_approval"
    )
    ok(decide(rt, item["id"]))
    run = finished(rt, started["id"])
    assert run["status"] == "succeeded", run


def test_email_link_decides_once_and_a_webhook_is_told(
    rt: dict[str, Any], smtp: SmtpSink, client: TestClient
) -> None:
    base, b = rt["base"], rt["builder"]
    builder_mail = ok(b.get("/api/v1/me"))["user"]["email"]
    end_user_mail = ok(rt["end_user"].get("/api/v1/me"))["user"]["email"]
    uc = new_uc(rt, "graph", review_graph(rt))
    SEEN.clear()
    started = ok(start(rt, uc["id"], webhook_url=f"{rt['http']}/hook"), 202)
    item = approval_of(rt, started["id"])
    mail = wait_for(lambda: smtp.to(builder_mail) or None, timeout=30, what="the approval email")[
        -1
    ]
    body = mail.get_payload(decode=True).decode()
    assert uc["name"] in mail["Subject"]
    assert f"http://forge.test/approvals/{item['id']}" in body
    token = re.search(r"/approve/(fap_[0-9a-f]{32}_[\w-]+)", body).group(1)  # type: ignore[union-attr]
    assert not smtp.to(end_user_mail)  # end users cannot decide, so nobody writes to them
    hooks = wait_for(
        lambda: [c for c in SEEN if c["path"] == "/hook" and "approval_id" in c["body"]] or None,
        timeout=30,
        what="the approval webhook",
    )
    assert hooks[0]["body"]["event"] == "use_case_run.approval_requested"
    assert hooks[0]["body"]["approval_id"] == item["id"]
    assert "content" not in hooks[0]["body"]
    # The public page: opening shows the content and decides nothing.
    public = new_client(client)
    link = "/api/v1/approval-links"
    view = ok(public.get(f"{link}/{token}"))
    assert "Release notes: pong" in view["content"]["text"]
    assert ok(b.get(f"{base}/approvals/{item['id']}"))["status"] == "pending"
    assert public.get(f"{link}/fap_{'0' * 32}_nope").status_code == 404
    assert public.get(f"{link}/garbage").status_code == 404
    bad = public.post(f"{link}/{token}/decision", json={"decision": "reject"})
    assert bad.status_code == 422
    done = ok(public.post(f"{link}/{token}/decision", json={"decision": "approve", "edited": "ok"}))
    assert done["decided_via"] == "link"
    assert done["status"] == "approved"
    # One time only, whatever the way the approval was decided.
    assert public.get(f"{link}/{token}").status_code == 404
    assert public.post(f"{link}/{token}/decision", json={"decision": "approve"}).status_code == 404
    run = finished(rt, started["id"])
    assert run["status"] == "succeeded", run
    assert run["outputs"]["result"] == "ok"
    # The decider recorded is the user the link was sent to.
    assert ok(b.get(f"{base}/approvals/{item['id']}"))["decided_by"] is not None


def test_a_token_dies_when_the_approval_is_decided_elsewhere(
    rt: dict[str, Any], smtp: SmtpSink, client: TestClient
) -> None:
    b = rt["builder"]
    builder_mail = ok(b.get("/api/v1/me"))["user"]["email"]
    before = len(smtp.to(builder_mail))
    uc = new_uc(rt, "graph", review_graph(rt))
    started = ok(start(rt, uc["id"]), 202)
    item = approval_of(rt, started["id"])
    mails = wait_for(
        lambda: smtp.to(builder_mail)[before:] or None, timeout=30, what="the approval email"
    )
    token = re.search(
        r"/approve/(fap_[0-9a-f]{32}_[\w-]+)", mails[-1].get_payload(decode=True).decode()
    ).group(1)  # type: ignore[union-attr]
    ok(decide(rt, item["id"]))
    assert new_client(client).get(f"/api/v1/approval-links/{token}").status_code == 404
    assert finished(rt, started["id"])["status"] == "succeeded"


def test_approvals_of_another_project_are_invisible(
    rt: dict[str, Any], admin: TestClient, client: TestClient
) -> None:
    from tests.integration.test_gateway import project
    from tests.integration.test_platform_core import login

    other = project(admin, client)
    stranger = login(client, other["owner"])
    uc = new_uc(rt, "graph", review_graph(rt))
    started = ok(start(rt, uc["id"]), 202)
    item = approval_of(rt, started["id"])
    foreign = f"/api/v1/projects/{other['id']}"
    assert ok(stranger.get(f"{foreign}/approvals")) == []
    assert stranger.get(f"{foreign}/approvals/{item['id']}").status_code == 404
    assert stranger.get(f"{rt['base']}/approvals").status_code == 404
    ok(decide(rt, item["id"], decision="reject", comment="cleanup"))
    finished(rt, started["id"])
