"""M1.3 Prompt Studio against PostgreSQL, Valkey and the mock provider
(US-1.10.1, US-1.10.2, US-1.11.1, US-1.11.2, US-1.12.1, US-1.13.1)."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from forge.core.settings import Settings
from tests.integration.test_gateway import (  # noqa: F401 - module fixtures
    admin,
    alias,
    calls,
    clean,
    client,
    mock,
    model,
    ok,
    project,
    provider,
    settings,
    uid,
)
from tests.integration.test_platform_core import create_user, csrf, login, sql


@pytest.fixture(scope="module")
def studio(admin: TestClient, client: TestClient, mock: str) -> dict[str, Any]:  # noqa: F811
    """A project, its admin's client and platform models on the mock provider."""
    proj = project(admin, client)
    prov = provider(admin, mock)
    names = {}
    for key, model_id, extra in (
        ("echo", "p-echo", {"cost_input": "1", "cost_output": "2"}),
        ("pong", "p-ok", {}),
        ("fail", "p-fail500", {}),
        ("assist", "p-assist", {}),
    ):
        names[key] = model(admin, prov["id"], model_id, all_projects=True, **extra)["name"]
    owner = login(client, proj["owner"])
    base = f"/api/v1/projects/{proj['id']}"
    alias(owner, "default-chat", [_model_id(admin, names["assist"])], url=base)
    return {"project": proj, "owner": owner, "base": f"{base}/prompts", "models": names}


def _model_id(admin: TestClient, name: str) -> str:  # noqa: F811
    return next(m["id"] for m in ok(admin.get("/api/v1/admin/models")) if m["name"] == name)


def version(model: str, **extra: Any) -> dict[str, Any]:
    return {
        "model": model,
        "system_template": "You answer as {{ persona|default('a helpful agent') }}.",
        "user_template": "Hello {customer}, about {topic}",
        "variables": [{"name": "topic", "value": "billing", "required": False}],
        "examples": [{"input": "Hi", "output": "Hello!"}],
        "model_params": {"temperature": 0},
    } | extra


def create(s: dict[str, Any], name: str | None = None, **extra: Any) -> Any:
    body = {
        "name": name or f"Prompt {uid()}",
        "description": "Support answer",
        "version": version(s["models"]["echo"], **extra),
    }
    return ok(s["owner"].post(s["base"], headers=csrf(s["owner"]), json=body), 201)


@pytest.mark.ac("US-1.10.1/1")
@pytest.mark.ac("US-1.10.1/2")
@pytest.mark.ac("US-1.10.1/3")
def test_create_validates_and_detects_variables(studio: dict[str, Any]) -> None:
    c, base = studio["owner"], studio["base"]
    no_description = {"name": "x", "version": version(studio["models"]["echo"])}
    assert c.post(base, headers=csrf(c), json=no_description).status_code == 422

    created = create(studio)
    v = created["version"]
    assert created["status"] == "draft"
    assert created["latest_version"] == 1
    assert v["tags"] == ["draft"]
    # Variables typed inline become required ones after the declared list, in order of
    # appearance (system prompt first).
    assert [(x["name"], x["required"]) for x in v["variables"]] == [
        ("topic", False),
        ("persona", True),
        ("customer", True),
    ]

    bad = {"name": "bad", "description": "d", "version": version(studio["models"]["echo"])}
    bad["version"]["user_template"] = "line\n{% if x %}never closed"
    r = c.post(base, headers=csrf(c), json=bad)
    assert r.status_code == 422
    assert r.json()["code"] == "template_invalid"
    assert r.json()["detail"][0]["field"] == "user_template"
    assert r.json()["detail"][0]["line"] == 2

    bad["version"] = version("no-such-model")
    assert c.post(base, headers=csrf(c), json=bad).json()["code"] == "model_not_found"

    dup = {"name": created["name"].upper(), "description": "d", "version": version("p")}
    assert c.post(base, headers=csrf(c), json=dup).json()["code"] == "name_taken"

    parsed = ok(
        c.post(
            f"{base}/parse",
            headers=csrf(c),
            json={"system_template": "{% for %}", "user_template": "{a} {{ b }}"},
        )
    )
    assert parsed["variables"] == ["a", "b"]
    assert parsed["issues"][0]["field"] == "system_template"


@pytest.mark.ac("US-1.10.2/1")
def test_versions_are_immutable_and_tagged(
    studio: dict[str, Any],
    client: TestClient,  # noqa: F811
    settings: Settings,  # noqa: F811
) -> None:
    c, base = studio["owner"], studio["base"]
    pid = create(studio)["id"]
    v2 = version(studio["models"]["pong"], user_template="Second {customer}", note="shorter")
    saved = ok(c.post(f"{base}/{pid}/versions", headers=csrf(c), json=v2), 201)
    assert saved["latest_version"] == 2
    assert saved["tags"] == {"draft": 2}
    assert (
        ok(c.get(f"{base}/{pid}/versions/1"))["user_template"] == "Hello {customer}, about {topic}"
    )
    assert [v["version"] for v in ok(c.get(f"{base}/{pid}/versions"))] == [2, 1]

    published = ok(c.post(f"{base}/{pid}/publish", headers=csrf(c), json={"version": 1}))
    assert published["status"] == "published"
    assert published["published_version"] == 1
    assert ok(c.get(f"{base}/{pid}/versions/1"))["tags"] == ["prod"]
    staged = ok(c.put(f"{base}/{pid}/tags/staging", headers=csrf(c), json={"version": 2}))
    assert staged["tags"] == {"draft": 2, "prod": 1, "staging": 2}
    assert (
        c.put(f"{base}/{pid}/tags/draft", headers=csrf(c), json={"version": 1}).status_code == 422
    )
    assert c.post(f"{base}/{pid}/publish", headers=csrf(c), json={"version": 9}).status_code == 404

    # Versions cannot be changed or deleted by the application roles, even inside the project.
    for statement in (
        f"UPDATE prompt_version SET model = 'x' WHERE prompt_id = '{pid}'",
        f"DELETE FROM prompt_version WHERE prompt_id = '{pid}'",
    ):
        with pytest.raises(Exception, match="permission denied"):
            sql(client, settings, statement, app_role=True)

    unpublished = ok(c.delete(f"{base}/{pid}/tags/prod", headers=csrf(c)))
    assert unpublished["status"] == "draft"


@pytest.mark.ac("US-1.11.2/1")
@pytest.mark.ac("US-1.11.2/2")
def test_library_filters_duplicate_rename_delete(studio: dict[str, Any]) -> None:
    c, base = studio["owner"], studio["base"]
    tag = uid()
    drafts = [create(studio, f"lib {tag} {i}") for i in range(3)]
    ok(c.post(f"{base}/{drafts[0]['id']}/publish", headers=csrf(c), json={}))

    def names(**params: Any) -> list[str]:
        return [p["name"] for p in ok(c.get(base, params={"q": f"lib {tag}"} | params))["items"]]

    assert len(names()) == 3
    assert names(status="published") == [f"lib {tag} 0"]
    assert set(names(status="draft")) == {f"lib {tag} 1", f"lib {tag} 2"}
    assert names(q="%") == []  # LIKE wildcards are escaped

    first = ok(c.get(base, params={"q": f"lib {tag}", "limit": 2}))
    rest = ok(c.get(base, params={"q": f"lib {tag}", "limit": 2, "cursor": first["next_cursor"]}))
    assert len(first["items"]) == 2
    assert rest["next_cursor"] is None
    assert {p["id"] for p in first["items"] + rest["items"]} == {d["id"] for d in drafts}
    assert c.get(base, params={"cursor": "not-a-cursor"}).json()["code"] == "invalid_cursor"

    src = drafts[1]
    taken = c.post(
        f"{base}/{src['id']}/duplicate", headers=csrf(c), json={"name": drafts[2]["name"]}
    )
    assert taken.json()["code"] == "name_taken"
    copy = ok(
        c.post(f"{base}/{src['id']}/duplicate", headers=csrf(c), json={"name": f"copy {tag}"}), 201
    )
    assert copy["latest_version"] == 1
    assert copy["version"]["user_template"] == src["version"]["user_template"]
    assert copy["status"] == "draft"

    renamed = ok(c.patch(f"{base}/{copy['id']}", headers=csrf(c), json={"name": f"renamed {tag}"}))
    assert renamed["name"] == f"renamed {tag}"
    assert c.delete(f"{base}/{copy['id']}", headers=csrf(c)).status_code == 204
    assert c.get(f"{base}/{copy['id']}").status_code == 404


@pytest.mark.ac("US-1.11.1/1")
@pytest.mark.ac("US-1.11.1/2")
def test_test_run_renders_calls_and_traces(
    studio: dict[str, Any],
    client: TestClient,  # noqa: F811
) -> None:
    c, base = studio["owner"], studio["base"]
    pid = create(studio)["id"]

    missing = c.post(f"{base}/{pid}/test", headers=csrf(c), json={"variables": {"persona": "x"}})
    assert missing.status_code == 422
    assert missing.json()["code"] == "missing_variables"
    assert missing.json()["detail"] == ["customer"]
    unknown = c.post(
        f"{base}/{pid}/test",
        headers=csrf(c),
        json={"variables": {"customer": "Ada", "persona": "x", "typo": "1"}},
    )
    assert unknown.json()["code"] == "unknown_variables"

    values = {"customer": "Ada", "persona": "a pirate"}
    run = ok(c.post(f"{base}/{pid}/test", headers=csrf(c), json={"variables": values}))
    assert run["status"] == "ok"
    assert json.loads(run["output"]) == [
        {"role": "system", "content": "You answer as a pirate."},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
        {"role": "user", "content": "Hello Ada, about billing"},
    ]
    assert run["usage"] == {"tokens_in": 11, "tokens_out": 7, "total_tokens": 18}
    assert Decimal(run["cost"]) == Decimal("0.000025")  # 11 x 1 + 7 x 2 per 1M tokens
    assert run["latency_ms"] >= 0
    assert run["model_name"] == studio["models"]["echo"]

    trace = ok(c.get(f"{base}/runs/{run['id']}"))
    assert trace["variables"] == {"topic": "billing"} | values
    assert trace["messages"][-1]["content"] == "Hello Ada, about billing"
    assert trace["params"] == {"temperature": 0}
    rows = calls(client, studio["project"]["id"])
    row = next(r for r in rows if str(r["id"]) == trace["llm_call_id"])
    assert row["trace_id"] == f"prompt_run:{run['id']}"
    assert next(r["id"] for r in ok(c.get(f"{base}/{pid}/runs"))) == run["id"]

    # A provider failure is a failed run with its error, not an HTTP error.
    failed = ok(
        c.post(
            f"{base}/{pid}/test",
            headers=csrf(c),
            json={"variables": values, "model": studio["models"]["fail"]},
        )
    )
    assert failed["status"] == "error"
    assert failed["error_code"] == "upstream_unavailable"
    assert failed["output"] is None


@pytest.mark.ac("US-1.12.1/1")
def test_playground_runs_models_in_parallel(studio: dict[str, Any]) -> None:
    c, base, m = studio["owner"], studio["base"], studio["models"]
    pid = create(studio)["id"]
    body = {
        "variables": {"customer": "Bo", "persona": "p"},
        "models": [m["echo"], m["fail"], m["pong"]],
    }
    runs = ok(c.post(f"{base}/{pid}/playground", headers=csrf(c), json=body))
    assert [r["model"] for r in runs] == body["models"]
    assert [r["status"] for r in runs] == ["ok", "error", "ok"]
    assert len({r["group_id"] for r in runs}) == 1
    assert runs[2]["output"] == "pong from p-ok"
    for models in ([m["echo"]], [m["echo"]] * 2, [m["echo"], m["pong"], m["fail"], "a", "b"]):
        r = c.post(f"{base}/{pid}/playground", headers=csrf(c), json=body | {"models": models})
        assert r.status_code == 422


@pytest.mark.ac("US-1.13.1/1")
@pytest.mark.ac("US-1.13.1/2")
def test_help_me_write_uses_default_chat_and_is_traced(
    studio: dict[str, Any],
    admin: TestClient,  # noqa: F811
) -> None:
    c, base = studio["owner"], studio["base"]
    body = {"description": "Answer customer questions about invoices politely"}
    result = ok(c.post(f"{base}/assist", headers=csrf(c), json=body))
    assert result["framework"] == "rtf"
    assert result["recommended"] is True
    assert result["user_template"] == "Answer {customer_name}: {question}"
    trace = ok(c.get(f"{base}/runs/{result['run_id']}"))
    assert trace["kind"] == "assist"
    assert trace["model"] == "default-chat"
    assert trace["model_name"] == studio["models"]["assist"]
    assert "<description>" in trace["messages"][1]["content"]

    chosen = ok(c.post(f"{base}/assist", headers=csrf(c), json=body | {"framework": "co_star"}))
    assert chosen["framework"] == "co_star"
    assert chosen["recommended"] is False

    # A model that does not answer with the JSON shape gives a clear error.
    project_url = f"/api/v1/projects/{studio['project']['id']}"
    alias(c, "default-chat", [_model_id(admin, studio["models"]["pong"])], url=project_url)
    try:
        r = c.post(f"{base}/assist", headers=csrf(c), json=body)
        assert r.status_code == 502
        assert r.json()["code"] == "assist_invalid_output"
    finally:
        alias(c, "default-chat", [_model_id(admin, studio["models"]["assist"])], url=project_url)


@pytest.mark.ac("US-1.10.1/4")
def test_hostile_template_is_refused_at_render(studio: dict[str, Any]) -> None:
    c, base = studio["owner"], studio["base"]
    pid = create(studio, user_template="{{ x.__class__.__mro__ }}", variables=[])["id"]
    r = c.post(
        f"{base}/{pid}/test", headers=csrf(c), json={"variables": {"x": "1", "persona": "p"}}
    )
    assert r.status_code == 422
    assert r.json()["code"] == "template_render_failed"
    blocked = c.post(
        base,
        headers=csrf(c),
        json={
            "name": f"inc {uid()}",
            "description": "d",
            "version": version(studio["models"]["echo"], user_template="{% include 'x' %}"),
        },
    )
    assert blocked.json()["code"] == "template_invalid"


def test_permissions_isolation_and_archive(
    studio: dict[str, Any],
    admin: TestClient,  # noqa: F811
    client: TestClient,  # noqa: F811
) -> None:
    c, base = studio["owner"], studio["base"]
    pid = create(studio)["id"]
    roles = {r["role_type"]: r["id"] for r in ok(c.get(base.replace("/prompts", "/roles")))}
    viewer = create_user(client)
    ok(
        c.post(
            base.replace("/prompts", "/members"),
            headers=csrf(c),
            json={"identifiers": [viewer["username"]], "role_name_id": roles["viewer"]},
        )
    )
    assert login(client, viewer).get(base).status_code == 403

    other = project(admin, client)
    stranger = login(client, other["owner"])
    other_base = f"/api/v1/projects/{other['id']}/prompts"
    assert stranger.get(f"{other_base}/{pid}").status_code == 404  # RLS: not in that project
    assert stranger.get(f"{base}/{pid}").status_code in (403, 404)

    archived = project(admin, client)
    owner = login(client, archived["owner"])
    ok(admin.post(f"/api/v1/admin/projects/{archived['id']}/archive", headers=csrf(admin)))
    body = {"name": "x", "description": "d", "version": version("p")}
    r = owner.post(f"/api/v1/projects/{archived['id']}/prompts", headers=csrf(owner), json=body)
    assert r.status_code == 409
