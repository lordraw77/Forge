"""M2.3d end-user runs: sessions, output files, downloads, delivery modes, webhook, file inputs
and the requirements attachment (US-2.9.1 to US-2.9.5)."""

from __future__ import annotations

import io
import json
import os
import uuid
from typing import Any

import pytest
from docx import Document
from fastapi.testclient import TestClient

from tests.integration.test_agents import admin, client, env, mock, reset_mock, workers
from tests.integration.test_gateway import ok, uid
from tests.integration.test_platform_core import csrf
from tests.integration.test_tools import http, mcp, settings, tools_env
from tests.integration.test_usecase_runs import agent_ref, finished, new_uc, rt
from tests.support.mock_http import SEEN, MockHttp

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
_ = MockHttp


@pytest.fixture(scope="module")
def granted(rt: dict[str, Any]) -> dict[str, Any]:
    """The end user's Role Name can see the catalog the tests file their use cases in."""
    owner, base = rt["owner"], rt["base"]
    grant = {"catalogs": [{"catalog_id": rt["catalog"]["id"], "enabled": True}]}
    ok(
        owner.put(
            f"{base}/roles/{rt['roles']['end_user']}/catalog-access",
            headers=csrf(owner),
            json=grant,
        )
    )
    return rt


def published(env: dict[str, Any], **extra: Any) -> dict[str, Any]:
    b, base = env["builder"], env["base"]
    uc = new_uc(
        env, "supervisor", {"agents": [{"agent_ref": agent_ref(env), "task": "Answer"}]}, **extra
    )
    ok(b.post(f"{base}/use-cases/{uc['id']}/publish", headers=csrf(b), json={}))
    return uc


def run_as(
    env: dict[str, Any], uc_id: str, who: str = "end_user", inputs: Any = None, **kw: Any
) -> Any:
    c = env[who]
    body = {"inputs": inputs if inputs is not None else {"topic": "x"}} | kw
    return c.post(f"{env['base']}/use-cases/{uc_id}/runs", headers=csrf(c), json=body)


def test_end_user_sees_progress_and_result_but_not_the_workflow(granted: dict[str, Any]) -> None:
    e, b, base = granted["end_user"], granted["builder"], granted["base"]
    uc = published(granted)
    started = ok(run_as(granted, uc["id"], inputs={"topic": "invoices"}), 202)
    assert started["session_id"]
    assert started["graph"] is None
    assert started["limits"] == {}
    run = finished(granted, started["id"], who="end_user")
    assert run["status"] == "succeeded"
    assert run["outputs"].keys() <= {"result", "file_error"}
    assert "pong" in run["outputs"]["result"]
    assert run["graph"] is None
    step = run["step_list"][0]
    assert step["ref"] is None
    assert step["input"] is None
    assert step["output"] is None
    assert step["agent_run_id"] is None
    # A builder sees the whole run.
    full = ok(b.get(f"{base}/use-case-runs/{started['id']}"))
    assert full["graph"]["nodes"]
    assert full["step_list"][0]["input"] is not None
    assert "decisions" in full["outputs"]
    # The end user never reads the draft or the workflow.
    assert e.get(f"{base}/use-cases/{uc['id']}").status_code == 403


def test_sessions_group_runs_and_rerun_repeats_the_inputs(granted: dict[str, Any]) -> None:
    e, o, base = granted["end_user"], granted["owner"], granted["base"]
    uc = published(granted)
    first = ok(run_as(granted, uc["id"], inputs={"topic": "alpha beta"}), 202)
    finished(granted, first["id"], who="end_user")
    second = ok(
        run_as(granted, uc["id"], inputs={"topic": "gamma"}, session_id=first["session_id"]), 202
    )
    assert second["session_id"] == first["session_id"]
    finished(granted, second["id"], who="end_user")
    again = ok(e.post(f"{base}/use-case-runs/{first['id']}/rerun", headers=csrf(e)), 202)
    assert again["id"] != first["id"]
    assert again["inputs"] == {"topic": "alpha beta"}
    assert again["session_id"] == first["session_id"]
    assert again["version"] == 1
    finished(granted, again["id"], who="end_user")
    sessions = ok(e.get(f"{base}/use-case-sessions", params={"use_case_id": uc["id"]}))
    assert [s["id"] for s in sessions] == [first["session_id"]]
    assert sessions[0]["runs"] == 3
    assert sessions[0]["title"] == "alpha beta"
    assert sessions[0]["last_status"] == "succeeded"
    detail = ok(e.get(f"{base}/use-case-sessions/{first['session_id']}"))
    assert [r["id"] for r in detail["run_list"]] == [first["id"], second["id"], again["id"]]
    assert ok(e.get(f"{base}/use-case-runs", params={"session_id": first["session_id"]}))
    # Sessions and reruns are private to their author.
    assert o.get(f"{base}/use-case-sessions/{first['session_id']}").status_code == 404
    assert ok(o.get(f"{base}/use-case-sessions")) == []
    assert (
        run_as(granted, uc["id"], who="builder", session_id=first["session_id"]).status_code == 404
    )
    assert o.post(f"{base}/use-case-runs/{first['id']}/rerun", headers=csrf(o)).status_code == 202
    ghost = str(uuid.uuid4())
    assert run_as(granted, uc["id"], session_id=ghost).status_code == 404


def test_docx_output_is_generated_stored_and_downloadable(granted: dict[str, Any]) -> None:
    e, b, o, base = granted["end_user"], granted["builder"], granted["owner"], granted["base"]
    uc = published(granted, output_type="docx")
    run = finished(granted, ok(run_as(granted, uc["id"]), 202)["id"], who="end_user")
    assert run["status"] == "succeeded", run
    assert len(run["files"]) == 1
    f = run["files"][0]
    assert f["name"].endswith(".docx")
    assert f["kind"] == "output"
    got = e.get(f"{base}/use-case-files/{f['id']}/content")
    assert got.status_code == 200
    assert got.headers["content-disposition"].startswith("attachment")
    assert got.headers["x-content-type-options"] == "nosniff"
    text = "\n".join(p.text for p in Document(io.BytesIO(got.content)).paragraphs)
    assert "pong" in text
    # Same visibility as the run: other end users do not see it, operators do.
    assert b.get(f"{base}/use-case-files/{f['id']}/content").status_code == 200
    assert o.get(f"{base}/use-case-files/{f['id']}/content").status_code == 200
    assert granted["viewer"].get(f"{base}/use-case-files/{f['id']}/content").status_code == 403
    assert e.get(f"{base}/use-case-files/{uuid.uuid4()}/content").status_code == 404
    md = e.get(f"{base}/use-case-runs/{run['id']}/export")
    assert md.status_code == 200
    assert "pong" in md.text
    assert ".md" in md.headers["content-disposition"]
    js = e.get(f"{base}/use-case-runs/{run['id']}/export", params={"format": "json"})
    assert "pong" in json.loads(js.content)


def test_xlsx_and_pdf_outputs(granted: dict[str, Any]) -> None:
    e, base = granted["end_user"], granted["base"]
    for kind, magic in (("xlsx", b"PK"), ("pdf", b"%PDF")):
        uc = published(granted, output_type=kind)
        run = finished(granted, ok(run_as(granted, uc["id"]), 202)["id"], who="end_user")
        assert run["status"] == "succeeded", run
        data = e.get(f"{base}/use-case-files/{run['files'][0]['id']}/content").content
        assert data.startswith(magic)


def test_favorites_and_recent(granted: dict[str, Any]) -> None:
    e, base = granted["end_user"], granted["base"]
    uc = published(granted)

    def item() -> dict[str, Any]:
        return next(x for x in ok(e.get(f"{base}/catalog/use-cases")) if x["id"] == uc["id"])

    assert item()["favorite"] is False
    assert item()["last_run_at"] is None
    assert (
        e.put(f"{base}/catalog/use-cases/{uc['id']}/favorite", headers=csrf(e)).status_code == 204
    )
    assert (
        e.put(f"{base}/catalog/use-cases/{uc['id']}/favorite", headers=csrf(e)).status_code == 204
    )
    assert item()["favorite"] is True
    finished(granted, ok(run_as(granted, uc["id"]), 202)["id"], who="end_user")
    assert item()["last_run_at"] is not None
    # Favorites are personal.
    mine = next(
        x for x in ok(granted["builder"].get(f"{base}/catalog/use-cases")) if x["id"] == uc["id"]
    )
    assert mine["favorite"] is False
    assert (
        e.delete(f"{base}/catalog/use-cases/{uc['id']}/favorite", headers=csrf(e)).status_code
        == 204
    )
    assert item()["favorite"] is False
    hidden = e.put(f"{base}/catalog/use-cases/{uuid.uuid4()}/favorite", headers=csrf(e))
    assert hidden.status_code == 404


def test_sync_and_stream_delivery(granted: dict[str, Any]) -> None:
    e, base = granted["end_user"], granted["base"]
    uc = published(granted)
    sync = run_as(granted, uc["id"], delivery="sync", timeout_s=60)
    assert sync.status_code == 200, sync.text
    assert sync.json()["status"] == "succeeded"
    assert "pong" in sync.json()["outputs"]["result"]
    events: list[tuple[str, dict[str, Any]]] = []
    with e.stream(
        "POST",
        f"{base}/use-cases/{uc['id']}/runs",
        headers=csrf(e),
        json={"inputs": {"topic": "x"}, "delivery": "stream"},
    ) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        name = ""
        for line in r.iter_lines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                events.append((name, json.loads(line[6:])))
    kinds = [k for k, _ in events]
    assert kinds[0] == "run"
    assert kinds[-1] == "done"
    assert "step" in kinds
    done = events[-1][1]["run"]
    assert done["status"] == "succeeded"
    assert done["graph"] is None
    # A refreshed page picks the (finished) run up from the events endpoint.
    with e.stream("GET", f"{base}/use-case-runs/{done['id']}/events") as r:
        text = "".join(r.iter_text())
    assert "event: done" in text
    # Others cannot follow it.
    other = granted["viewer"].get(f"{base}/use-case-runs/{done['id']}/events")
    assert other.status_code == 403


def test_webhook_is_checked_and_called_when_the_run_ends(granted: dict[str, Any]) -> None:
    base, hooks = granted["base"], granted["http"]
    uc = published(granted)
    refused = run_as(granted, uc["id"], webhook_url="http://example.com/hook")
    assert refused.status_code == 422
    assert refused.json()["code"] == "invalid_webhook"
    SEEN.clear()
    started = ok(run_as(granted, uc["id"], webhook_url=f"{hooks}/hook"), 202)
    assert started["webhook_status"] == "pending"
    finished(granted, started["id"], who="end_user")

    def delivered() -> Any:
        r = ok(granted["end_user"].get(f"{base}/use-case-runs/{started['id']}"))
        return r if r["webhook_status"] == "delivered" else None

    from tests.integration.test_rag import wait_for

    wait_for(delivered, timeout=30, what="webhook")
    call = next(c for c in SEEN if c["path"] == "/hook")
    assert call["body"]["run_id"] == started["id"]
    assert call["body"]["status"] == "succeeded"
    assert "outputs" not in call["body"]


def test_a_dead_webhook_is_retried_and_recorded_without_touching_the_run(
    granted: dict[str, Any],
) -> None:
    base = granted["base"]
    uc = published(granted)
    started = ok(run_as(granted, uc["id"], webhook_url="http://127.0.0.1:9/hook"), 202)
    assert finished(granted, started["id"], who="end_user")["status"] == "succeeded"

    def failed() -> Any:
        r = ok(granted["end_user"].get(f"{base}/use-case-runs/{started['id']}"))
        return r if r["webhook_status"] == "failed" else None

    from tests.integration.test_rag import wait_for

    run = wait_for(failed, timeout=90, what="webhook failure after the retries")
    assert run["status"] == "succeeded"
    assert run["webhook_error"]


def test_file_inputs_and_the_requirements_attachment(granted: dict[str, Any]) -> None:
    e, b, o, base = (granted[k] for k in ("end_user", "builder", "owner", "base"))
    fields = [
        {"name": "topic", "type": "text", "required": True},
        {"name": "doc", "type": "file", "required": True},
    ]
    uc = published(granted, input_fields=fields)
    up = e.post(
        f"{base}/use-case-files",
        headers=csrf(e),
        files={"file": ("notes.txt", b"The quarterly figures are confidential-ish.", "text/plain")},
    )
    stored = ok(up, 201)
    assert stored["kind"] == "input"
    assert stored["name"] == "notes.txt"
    bad = e.post(
        f"{base}/use-case-files",
        headers=csrf(e),
        files={"file": ("image.png", b"\x89PNG\x00\xff\xfe", "image/png")},
    )
    assert bad.status_code == 422
    assert bad.json()["code"] == "unsupported_file"
    empty = e.post(
        f"{base}/use-case-files", headers=csrf(e), files={"file": ("a.txt", b"", "text/plain")}
    )
    assert empty.status_code == 422
    # The form takes a file id the caller uploaded, nothing else.
    for value in ("not-a-uuid", str(uuid.uuid4())):
        r = run_as(granted, uc["id"], inputs={"topic": "t", "doc": value})
        assert r.status_code == 422, r.text
        assert r.json()["code"] == "invalid_input"
    other = b.post(
        f"{base}/use-case-files", headers=csrf(b), files={"file": ("b.txt", b"mine", "text/plain")}
    )
    stolen = run_as(granted, uc["id"], inputs={"topic": "t", "doc": ok(other, 201)["id"]})
    assert stolen.status_code == 422
    started = ok(run_as(granted, uc["id"], inputs={"topic": "t", "doc": stored["id"]}), 202)
    run = finished(granted, started["id"], who="end_user")
    assert run["status"] == "succeeded", run
    seen = ok(b.get(f"{base}/use-case-runs/{started['id']}"))
    message = json.dumps(seen["step_list"][0]["input"])
    assert "quarterly figures" in message
    # Uploaded files are served as attachments of an inert type, to their author and operators.
    back = e.get(f"{base}/use-case-files/{stored['id']}/content")
    assert back.headers["content-type"] == "application/octet-stream"
    assert b.get(f"{base}/use-case-files/{stored['id']}/content").status_code == 200
    assert granted["viewer"].get(f"{base}/use-case-files/{stored['id']}/content").status_code == 403
    assert o.get(f"{base}/use-case-files/{stored['id']}/content").status_code == 200
    # Requirements attachment (builders only), one per use case.
    url = f"{base}/use-cases/{uc['id']}/requirements-file"
    assert ok(b.get(url)) is None
    put = b.post(
        url, headers=csrf(b), files={"file": ("spec.md", b"# Spec\nAll invoices.", "text/markdown")}
    )
    assert ok(put, 201)["name"] == "spec.md"
    again = b.post(url, headers=csrf(b), files={"file": ("v2.txt", b"second", "text/plain")})
    assert ok(again, 201)["name"] == "v2.txt"
    assert ok(b.get(url))["name"] == "v2.txt"
    assert e.get(url).status_code == 403
    assert (
        e.post(url, headers=csrf(e), files={"file": ("x.txt", b"x", "text/plain")}).status_code
        == 403
    )
    assert b.delete(url, headers=csrf(b)).status_code == 204
    assert ok(b.get(url)) is None


def test_api_key_sees_what_its_creators_role_name_sees(
    granted: dict[str, Any], client: TestClient
) -> None:
    owner, base = granted["owner"], granted["base"]
    admin_role = granted["roles"]["project_admin"]
    uc = published(granted)
    made = owner.post(
        f"{base}/api-keys",
        headers=csrf(owner),
        json={"name": f"ci {uid()}", "scopes": ["usecase.run"]},
    )
    auth = {"Authorization": f"Bearer {ok(made, 201)['token']}"}
    # Nothing is enabled for the creator's Role Name: the catalog is empty, the use case 404.
    assert ok(client.get(f"{base}/catalog/use-cases", headers=auth)) == []
    body = {"inputs": {"topic": "x"}, "delivery": "sync", "timeout_s": 60}
    url = f"{base}/use-cases/{uc['id']}/runs"
    assert client.post(url, headers=auth, json=body).status_code == 404
    path = f"{base}/roles/{admin_role}/catalog-access"
    grant = {"catalogs": [{"catalog_id": granted["catalog"]["id"], "enabled": True}]}
    ok(owner.put(path, headers=csrf(owner), json=grant))
    try:
        assert uc["id"] in [
            x["id"] for x in ok(client.get(f"{base}/catalog/use-cases", headers=auth))
        ]
        run = client.post(url, headers=auth, json=body)
        assert run.status_code == 200, run.text
        assert run.json()["status"] == "succeeded"
        assert run.json()["graph"] is None
    finally:
        ok(owner.put(path, headers=csrf(owner), json={"catalogs": []}))
    assert client.post(url, headers=auth, json=body).status_code == 404
