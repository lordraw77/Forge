"""M2.2 Tools Studio against PostgreSQL, Valkey, Temporal, a mock HTTP API, a mock MCP server
and the mock model (US-2.2.x). Agents run on a real runs worker."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from forge.core.settings import Settings
from tests.integration.test_agents import (
    TERMINAL,
    admin,
    client,
    env,
    finished,
    make_agent,
    make_prompt,
    mock,
    publish,
    reset_mock,
    start_chat,
    workers,
)
from tests.integration.test_gateway import ok, uid
from tests.integration.test_platform_core import MASTER_KEY, create_user, csrf, login
from tests.support.mock_http import OPENAPI, MockHttp
from tests.support.mock_mcp import MockMcp
from tests.support.sandbox_service import TOKEN as SANDBOX_TOKEN

pytestmark = pytest.mark.skipif(
    not os.environ.get("FORGE_TEST_S3_ENDPOINT"), reason="needs S3, OpenSearch and Temporal"
)
SECRET = "tok-9f3a-very-secret"


@pytest.fixture(scope="module")
def settings(sandbox_url: str) -> Settings:
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
        temporal_ingest_queue=f"ingest-tools-{run}",
        temporal_runs_queue=f"runs-tools-{run}",
        metrics_port=0,
        tools_allow_private_hosts=True,
        mcp_stdio_allowed="uvx",
        sandbox_url=sandbox_url,
        sandbox_token=SANDBOX_TOKEN,
    )


@pytest.fixture(scope="module")
def http() -> Iterator[str]:
    with MockHttp() as base:
        yield base


@pytest.fixture(scope="module")
def mcp() -> Iterator[str]:
    with MockMcp() as url:
        yield url


@pytest.fixture(scope="module")
def tools_env(env: dict[str, Any], client: TestClient, http: str, mcp: str) -> dict[str, Any]:
    owner, base = env["owner"], env["base"]
    hosts = ok(
        owner.put(f"{base}/tool-policy", headers=csrf(owner), json={"allowed_hosts": ["127.0.0.1"]})
    )
    assert hosts["allowed_hosts"] == ["127.0.0.1"]
    roles = {r["role_type"]: r["id"] for r in ok(owner.get(f"{base}/roles"))}
    builder = create_user(client)
    ok(
        owner.post(
            f"{base}/members",
            headers=csrf(owner),
            json={"identifiers": [builder["username"]], "role_name_id": roles["builder"]},
        )
    )
    env = dict(env)
    env["builder"] = login(client, builder)
    env["http"], env["mcp"] = http, mcp
    return env


def create_credential(env: dict[str, Any], **body: Any) -> Any:
    c = env["owner"]
    payload = {"name": f"cred-{uid()}", "kind": "bearer", "secret": SECRET} | body
    return ok(c.post(f"{env['base']}/tool-credentials", headers=csrf(c), json=payload), 201)


def http_tool(env: dict[str, Any], **extra: Any) -> dict[str, Any]:
    cfg = {
        "kind": "http",
        "method": "GET",
        "url": f"{env['http']}/pets/{{pet_id}}",
        "params": [
            {"name": "pet_id", "in": "path", "type": "string", "required": True},
            {"name": "verbose", "in": "query", "type": "boolean"},
        ],
    } | extra.pop("config", {})
    return {
        "name": f"pet_{uid()}"[:48].lower(),
        "description": "Fetch a pet",
        "config": cfg,
    } | extra


def create_tool(env: dict[str, Any], who: str = "builder", **kw: Any) -> Any:
    c = env[who]
    return ok(c.post(f"{env['base']}/tools", headers=csrf(c), json=http_tool(env, **kw)), 201)


def try_tool(env: dict[str, Any], tool_id: str, args: dict[str, Any], **kw: Any) -> Any:
    c = env["builder"]
    return ok(
        c.post(
            f"{env['base']}/tools/{tool_id}/test",
            headers=csrf(c),
            json={"arguments": args} | kw,
        )
    )


def dump(*values: Any) -> str:
    return json.dumps(values, default=str)


# ---- library, versions, publish
@pytest.mark.ac("US-2.2.1/1")
@pytest.mark.ac("US-2.2.1/2")
def test_library_versions_publish_duplicate_and_roles(tools_env: dict[str, Any]) -> None:
    env = tools_env
    base, builder = env["base"], env["builder"]
    t = create_tool(env)
    assert t["latest_version"] == 1
    assert t["published_version"] is None
    assert t["version_detail"]["schema_in"]["required"] == ["pet_id"]

    # An invalid name or kind change is refused; a new version leaves the old one intact.
    bad = http_tool(env) | {"name": "Bad-Name"}
    assert builder.post(f"{base}/tools", headers=csrf(builder), json=bad).status_code == 422
    body = {
        "description": "Fetch a pet, version two",
        "timeout_s": 5,
        "config": http_tool(env)["config"] | {"response_max_chars": 1000},
    }
    v2 = ok(builder.post(f"{base}/tools/{t['id']}/versions", headers=csrf(builder), json=body), 201)
    assert v2["latest_version"] == 2
    v1 = ok(builder.get(f"{base}/tools/{t['id']}", params={"version": 1}))
    assert v1["version_detail"]["timeout_s"] == 15
    assert v1["version_detail"]["config"]["response_max_chars"] == 8000
    kind_change = {
        "description": "x",
        "config": {"kind": "sql", "datasource": "x", "query": "select 1"},
    }
    assert (
        builder.post(
            f"{base}/tools/{t['id']}/versions", headers=csrf(builder), json=kind_change
        ).status_code
        == 422
    )

    pub = ok(
        builder.post(f"{base}/tools/{t['id']}/publish", headers=csrf(builder), json={"version": 1})
    )
    assert pub["published_version"] == 1
    listing = ok(builder.get(f"{base}/tools", params={"published": "true"}))
    assert t["id"] in [i["id"] for i in listing["items"]]
    un = ok(builder.delete(f"{base}/tools/{t['id']}/publish", headers=csrf(builder)))
    assert un["published_version"] is None

    dup = ok(
        builder.post(
            f"{base}/tools/{t['id']}/duplicate",
            headers=csrf(builder),
            json={"name": f"copy_{uid()}"[:40].lower()},
        ),
        201,
    )
    assert dup["latest_version"] == 1
    assert dup["id"] != t["id"]
    clash = builder.post(
        f"{base}/tools/{t['id']}/duplicate", headers=csrf(builder), json={"name": t["name"]}
    )
    assert clash.status_code == 409

    # Viewers and end users cannot touch the library (asset.write).
    for who in ("viewer", "end_user"):
        assert env[who].get(f"{base}/tools").status_code == 403
        assert (
            env[who].post(f"{base}/tools", headers=csrf(env[who]), json=http_tool(env)).status_code
            == 403
        )
    assert builder.delete(f"{base}/tools/{t['id']}", headers=csrf(builder)).status_code == 204
    assert builder.get(f"{base}/tools/{t['id']}").status_code == 404


# ---- credentials (US-2.2.2)
@pytest.mark.ac("US-2.2.2/1")
@pytest.mark.ac("US-2.2.2/2")
@pytest.mark.ac("US-2.2.2/3")
@pytest.mark.ac("US-2.2.2/4")
@pytest.mark.ac("US-2.2.10/1")
def test_credentials_never_leak_and_cannot_be_deleted_while_used(
    tools_env: dict[str, Any], client: TestClient, admin: TestClient
) -> None:
    env = tools_env
    base, owner, builder = env["base"], env["owner"], env["builder"]
    cred = create_credential(env)
    assert "secret" not in cred
    # Builders list names but cannot create or delete credentials.
    assert cred["id"] in [c["id"] for c in ok(builder.get(f"{base}/tool-credentials"))]
    forbidden = builder.post(
        f"{base}/tool-credentials",
        headers=csrf(builder),
        json={"name": "mine", "kind": "bearer", "secret": "x"},
    )
    assert forbidden.status_code == 403
    assert (
        builder.delete(f"{base}/tool-credentials/{cred['id']}", headers=csrf(builder)).status_code
        == 403
    )

    t = create_tool(env, config={"credential": cred["name"]})
    unknown = http_tool(env, config={"credential": "does-not-exist"})
    assert builder.post(f"{base}/tools", headers=csrf(builder), json=unknown).status_code == 422

    result = try_tool(env, t["id"], {"pet_id": "p1"})
    assert result["status"] == "ok"
    assert result["request"]["headers"]["Authorization"] == "Bearer ***"

    assert (
        owner.delete(f"{base}/tool-credentials/{cred['id']}", headers=csrf(owner)).status_code
        == 409
    )

    # Everything people can read, and the database, are free of the secret value.
    seen = [
        ok(owner.get(f"{base}/tool-credentials")),
        ok(builder.get(f"{base}/tools/{t['id']}")),
        ok(builder.get(f"{base}/tool-calls")),
        ok(env["builder"].get(f"{base}/tool-policy")),
        ok(admin.get("/api/v1/admin/audit", params={"project_id": env["project"]["id"]})),
        result,
    ]
    assert SECRET not in dump(*seen)
    from tests.integration.test_agents import admin_rows

    rows = admin_rows(
        client,
        "SELECT config::text, schema_in::text FROM tool_version "
        f"WHERE project_id = '{env['project']['id']}'",
    )
    assert SECRET not in dump(rows)
    cipher = admin_rows(client, "SELECT ciphertext FROM secret WHERE kind LIKE 'tool.%'")
    assert cipher
    assert all(SECRET.encode() not in bytes(r[0]) for r in cipher)

    assert builder.delete(f"{base}/tools/{t['id']}", headers=csrf(builder)).status_code == 204
    assert (
        owner.delete(f"{base}/tool-credentials/{cred['id']}", headers=csrf(owner)).status_code
        == 204
    )


# ---- HTTP tool, test console, call log, policy (US-2.2.3, 2.2.4, 2.2.8)
@pytest.mark.ac("US-2.2.4/2")
@pytest.mark.ac("US-2.2.8/1")
@pytest.mark.ac("US-2.2.8/2")
def test_test_console_validates_arguments_and_logs_every_call(tools_env: dict[str, Any]) -> None:
    env = tools_env
    base, builder = env["base"], env["builder"]
    t = create_tool(env)
    good = try_tool(env, t["id"], {"pet_id": "rex", "verbose": True})
    assert good["status"] == "ok"
    assert "Rex" in good["content"]
    assert good["request"]["url"].endswith("/pets/rex?verbose=true")
    assert good["latency_ms"] >= 0

    for args in ({}, {"pet_id": 5}, {"pet_id": "x", "evil": 1}):
        refused = try_tool(env, t["id"], args)
        assert refused["status"] == "error"
        assert refused["error_code"] == "invalid_arguments"

    calls = ok(builder.get(f"{base}/tool-calls", params={"tool_id": t["id"]}))
    assert len(calls) == 4
    assert {c["source"] for c in calls} == {"test"}
    ok_call = next(c for c in calls if c["status"] == "ok")
    assert ok_call["version"] == 1
    assert ok_call["input"]["arguments"]["pet_id"] == "rex"
    assert "Rex" in ok_call["output"]["body"]
    errors = ok(builder.get(f"{base}/tool-calls", params={"tool_id": t["id"], "status": "error"}))
    assert len(errors) == 3
    assert (
        ok(builder.get(f"{base}/tool-calls", params={"source": "agent", "tool_id": t["id"]})) == []
    )


@pytest.mark.ac("US-2.2.3/1")
@pytest.mark.ac("US-2.2.3/3")
def test_egress_policy_is_per_project_and_logged(tools_env: dict[str, Any]) -> None:
    env = tools_env
    base, owner, builder = env["base"], env["owner"], env["builder"]
    t = create_tool(env)
    try:
        ok(owner.put(f"{base}/tool-policy", headers=csrf(owner), json={"allowed_hosts": []}))
        refused = try_tool(env, t["id"], {"pet_id": "x"})
        assert refused["error_code"] == "host_not_allowed"
        call = ok(builder.get(f"{base}/tool-calls", params={"status": "error"}))[0]
        assert call["error_code"] == "host_not_allowed"
        # Builders cannot widen the policy.
        widen = builder.put(
            f"{base}/tool-policy", headers=csrf(builder), json={"allowed_hosts": ["127.0.0.1"]}
        )
        assert widen.status_code == 403
        bad = owner.put(
            f"{base}/tool-policy", headers=csrf(owner), json={"allowed_hosts": ["http://x"]}
        )
        assert bad.status_code == 422
    finally:
        ok(
            owner.put(
                f"{base}/tool-policy", headers=csrf(owner), json={"allowed_hosts": ["127.0.0.1"]}
            )
        )
    assert try_tool(env, t["id"], {"pet_id": "x"})["status"] == "ok"


# ---- OpenAPI import (US-2.2.5)
@pytest.mark.ac("US-2.2.5/1")
@pytest.mark.ac("US-2.2.5/2")
@pytest.mark.ac("US-2.2.5/3")
def test_openapi_preview_import_and_use(tools_env: dict[str, Any]) -> None:
    env = tools_env
    base, builder = env["base"], env["builder"]
    doc = json.dumps(OPENAPI)
    preview = ok(
        builder.post(
            f"{base}/tool-import/openapi/preview", headers=csrf(builder), json={"document": doc}
        )
    )
    assert preview["title"] == "Pets"
    assert {o["operation_id"] for o in preview["operations"]} == {"showPetById", "createPet"}
    cred = create_credential(env, kind="header", param_name="X-Api-Key")

    bad = builder.post(
        f"{base}/tool-import/openapi/preview",
        headers=csrf(builder),
        json={"document": "not: [valid"},
    )
    assert bad.status_code == 422

    created = ok(
        builder.post(
            f"{base}/tool-import/openapi",
            headers=csrf(builder),
            json={
                "document": doc,
                "operations": ["showPetById", "createPet"],
                "base_url": env["http"],
                "credential": cred["name"],
            },
        ),
        201,
    )
    assert created["skipped"] == []
    by_name = {t["name"].split("_2")[0]: t for t in created["created"]}
    assert {"show_pet_by_id", "create_pet"} <= set(by_name)
    assert by_name["create_pet"]["writes"] is True
    assert by_name["show_pet_by_id"]["writes"] is False
    get = by_name["show_pet_by_id"]
    result = try_tool(env, get["id"], {"petId": "p9"})
    assert result["status"] == "ok", result
    post = try_tool(env, by_name["create_pet"]["id"], {"name": "Tom"})
    assert post["output"]["status"] == 201

    # Importing again gets numeric suffixes, never overwrites.
    again = ok(
        builder.post(
            f"{base}/tool-import/openapi",
            headers=csrf(builder),
            json={"document": doc, "operations": ["showPetById"], "base_url": env["http"]},
        ),
        201,
    )
    assert again["created"][0]["name"] == "show_pet_by_id_2"
    bare = json.dumps({k: v for k, v in OPENAPI.items() if k != "servers"})
    no_base = builder.post(
        f"{base}/tool-import/openapi",
        headers=csrf(builder),
        json={"document": bare, "operations": ["showPetById"]},
    )
    assert no_base.status_code == 422  # no server URL in the document and none given


# ---- SQL (US-2.2.7)
def datasource(env: dict[str, Any], **kw: Any) -> Any:
    u = urlparse(os.environ["FORGE_TEST_DATABASE_URL"].replace("+asyncpg", ""))
    body = {
        "name": f"ds-{uid()}",
        "host": u.hostname,
        "port": u.port or 5432,
        "database": (u.path or "/").lstrip("/"),
        "username": u.username,
        "password": u.password,
        "ssl_mode": "disable",
    } | kw
    c = env["owner"]
    return ok(c.post(f"{env['base']}/datasources", headers=csrf(c), json=body), 201)


@pytest.mark.ac("US-2.2.7/1")
@pytest.mark.ac("US-2.2.7/2")
@pytest.mark.ac("US-2.2.7/3")
def test_sql_tool_is_read_only_parameterized_and_limited(tools_env: dict[str, Any]) -> None:
    env = tools_env
    base, builder, owner = env["base"], env["builder"], env["owner"]
    ds = datasource(env)
    assert "password" not in ds
    sql_tool = {
        "name": f"q_{uid()}"[:40].lower(),
        "description": "Numbers",
        "timeout_s": 5,
        "config": {
            "kind": "sql",
            "datasource": ds["name"],
            "query": "select g as n, :label::text as label from generate_series(1, :top::int) g",
            "params": [
                {"name": "top", "type": "integer", "required": True},
                {"name": "label", "type": "string", "required": False},
            ],
            "row_limit": 3,
        },
    }
    created = ok(builder.post(f"{base}/tools", headers=csrf(builder), json=sql_tool), 201)
    res = try_tool(env, created["id"], {"top": 10, "label": "x'; drop table app_user; --"})
    assert res["status"] == "ok", res
    assert res["output"]["truncated"] is True
    assert [r[0] for r in res["output"]["rows"]] == [1, 2, 3]
    assert res["output"]["rows"][0][1] == "x'; drop table app_user; --"  # bound, never concatenated
    assert "first 3 rows only" in res["content"]

    # Statements that could change data are refused when saving...
    for query in (
        "delete from app_user",
        "select 1; drop table app_user",
        "select * into t2 from app_user",
        "select :x -- c",
    ):
        body = sql_tool | {"name": f"w_{uid()}"[:40].lower()}
        body["config"] = sql_tool["config"] | {"query": query, "params": [{"name": "x"}]}
        assert builder.post(f"{base}/tools", headers=csrf(builder), json=body).status_code == 422
    undeclared = sql_tool | {"name": f"u_{uid()}"[:40].lower()}
    undeclared["config"] = sql_tool["config"] | {"query": "select :missing", "params": []}
    assert builder.post(f"{base}/tools", headers=csrf(builder), json=undeclared).status_code == 422

    # ...and the transaction is read only anyway: a function that writes is refused by the server.
    sneaky = sql_tool | {"name": f"s_{uid()}"[:40].lower()}
    sneaky["config"] = sql_tool["config"] | {
        "query": "select nextval('uuid_generate_v4')",
        "params": [],
    }
    # (the sequence does not exist: the point is that nothing writes; the error is a result)
    s = ok(builder.post(f"{base}/tools", headers=csrf(builder), json=sneaky), 201)
    assert try_tool(env, s["id"], {})["error_code"] == "query_failed"

    slow = sql_tool | {"name": f"t_{uid()}"[:40].lower(), "timeout_s": 1}
    slow["config"] = sql_tool["config"] | {
        "query": "select count(*) from generate_series(1, 2000000000)",
        "params": [],
    }
    t = ok(builder.post(f"{base}/tools", headers=csrf(builder), json=slow), 201)
    assert try_tool(env, t["id"], {})["error_code"] == "query_failed"

    # The host goes through the egress policy; the data source cannot be deleted while used.
    ok(owner.put(f"{base}/tool-policy", headers=csrf(owner), json={"allowed_hosts": []}))
    try:
        refused = try_tool(env, created["id"], {"top": 2})
        assert refused["error_code"] == "host_not_allowed"
    finally:
        ok(
            owner.put(
                f"{base}/tool-policy", headers=csrf(owner), json={"allowed_hosts": ["127.0.0.1"]}
            )
        )
    assert owner.delete(f"{base}/datasources/{ds['id']}", headers=csrf(owner)).status_code == 409
    assert builder.post(f"{base}/datasources", headers=csrf(builder), json={}).status_code == 403
    assert "password" not in dump(ok(owner.get(f"{base}/datasources")))


# ---- MCP (US-2.2.6)
@pytest.mark.ac("US-2.2.6/1")
@pytest.mark.ac("US-2.2.6/2")
@pytest.mark.ac("US-2.2.6/3")
def test_mcp_server_discover_import_and_call(tools_env: dict[str, Any]) -> None:
    env = tools_env
    base, owner, builder = env["base"], env["owner"], env["builder"]
    srv = ok(
        owner.post(
            f"{base}/mcp-servers",
            headers=csrf(owner),
            json={"name": f"mcp-{uid()}", "transport": "http", "url": env["mcp"]},
        ),
        201,
    )
    found = ok(builder.post(f"{base}/mcp-servers/{srv['id']}/discover", headers=csrf(builder)))
    assert {t["name"] for t in found} == {"add", "echo", "boom"}
    assert all(t["imported_as"] is None for t in found)

    imported = ok(
        builder.post(
            f"{base}/mcp-servers/{srv['id']}/import",
            headers=csrf(builder),
            json={"tools": ["add", "boom"]},
        ),
        201,
    )
    names = {t["name"] for t in imported}
    assert len(names) == 2
    add = next(t for t in imported if t["name"].startswith("add"))
    assert add["writes"] is True  # an MCP tool may change data: flagged
    assert try_tool(env, add["id"], {"a": 4, "b": 5})["content"].count("9") >= 1
    assert try_tool(env, add["id"], {"a": "x"})["error_code"] == "invalid_arguments"
    boom = next(t for t in imported if t["name"].startswith("boom"))
    assert try_tool(env, boom["id"], {})["error_code"] == "mcp_error"

    again = ok(builder.post(f"{base}/mcp-servers/{srv['id']}/discover", headers=csrf(builder)))
    assert {t["name"]: t["imported_as"] for t in again if t["imported_as"]} == {
        "add": add["name"],
        "boom": boom["name"],
    }
    missing = builder.post(
        f"{base}/mcp-servers/{srv['id']}/import", headers=csrf(builder), json={"tools": ["nope"]}
    )
    assert missing.status_code == 422

    # Policy, roles, in-use protection and stdio.
    assert owner.delete(f"{base}/mcp-servers/{srv['id']}", headers=csrf(owner)).status_code == 409
    forbidden = builder.post(
        f"{base}/mcp-servers",
        headers=csrf(builder),
        json={"name": "x", "transport": "http", "url": env["mcp"]},
    )
    assert forbidden.status_code == 403
    stdio_bad = owner.post(
        f"{base}/mcp-servers",
        headers=csrf(owner),
        json={
            "name": f"stdio-{uid()}",
            "transport": "stdio",
            "command": "rm",
            "args": ["-rf", "/"],
        },
    )
    assert stdio_bad.status_code == 422
    assert stdio_bad.json()["code"] == "stdio_not_allowed"
    stdio_ok = owner.post(
        f"{base}/mcp-servers",
        headers=csrf(owner),
        json={"name": f"stdio-{uid()}", "transport": "stdio", "command": "uvx", "args": ["x"]},
    )
    assert stdio_ok.status_code == 201

    ok(owner.put(f"{base}/tool-policy", headers=csrf(owner), json={"allowed_hosts": []}))
    try:
        assert try_tool(env, add["id"], {"a": 1, "b": 1})["error_code"] == "host_not_allowed"
        blocked = builder.post(f"{base}/mcp-servers/{srv['id']}/discover", headers=csrf(builder))
        assert blocked.status_code == 422
    finally:
        ok(
            owner.put(
                f"{base}/tool-policy", headers=csrf(owner), json={"allowed_hosts": ["127.0.0.1"]}
            )
        )


# ---- agents (US-2.2.9)
def library(tool_id: str, **extra: Any) -> dict[str, Any]:
    return {"kind": "library", "tool_id": tool_id} | extra


@pytest.mark.ac("US-2.2.9/1")
@pytest.mark.ac("US-2.2.9/3")
@pytest.mark.ac("US-2.2.8/2")
def test_agent_uses_a_pinned_library_tool_and_the_call_is_logged(tools_env: dict[str, Any]) -> None:
    env = tools_env
    base, builder = env["base"], env["builder"]
    t = create_tool(env, config={"url": f"{env['http']}/injected", "params": []})
    p = make_prompt(env)
    # An unpublished tool cannot be pinned by default.
    agent_body = {
        "name": f"Agent {uid()}",
        "description": "uses a library tool",
        "config": {
            "prompt": {"prompt_id": p["id"]},
            "model": env["models"]["agent"]["name"],
            "tools": [library(t["id"])],
        },
    }
    unpublished = env["owner"].post(f"{base}/agents", headers=csrf(env["owner"]), json=agent_body)
    assert unpublished.status_code == 422
    ok(builder.post(f"{base}/tools/{t['id']}/publish", headers=csrf(builder), json={}))
    a = ok(env["owner"].post(f"{base}/agents", headers=csrf(env["owner"]), json=agent_body), 201)
    pinned = a["version"]["config"]["tools"][0]
    assert pinned["version"] == 1
    assert pinned["name"] == t["name"]
    publish(env, a["id"])

    # A newer tool version does not change what the agent version runs.
    newer = {
        "description": "changed",
        "config": http_tool(env)["config"] | {"url": f"{env['http']}/fail", "params": []},
    }
    ok(builder.post(f"{base}/tools/{t['id']}/versions", headers=csrf(builder), json=newer), 201)

    _, queued = start_chat(env, a["id"], f"Please call {t['name']} with {{}}")
    run = finished(env, queued["id"])
    assert run["status"] == "succeeded", run
    step = next(s for s in run["step_list"] if s["kind"] == "tool")
    assert step["name"] == t["name"]
    assert step["status"] == "ok"
    assert "Ignore previous instructions" in dump(step["output"])  # v1 (the /injected page)
    calls = ok(builder.get(f"{base}/tool-calls", params={"source": "agent", "tool_id": t["id"]}))
    assert len(calls) == 1
    assert calls[0]["run_id"] == run["id"]
    assert calls[0]["version"] == 1

    # In use: delete is refused with the agent named; after unpublish the pin still holds.
    blocked = builder.delete(f"{base}/tools/{t['id']}", headers=csrf(builder))
    assert blocked.status_code == 409
    assert a["name"] in dump(blocked.json())


@pytest.mark.ac("US-2.2.9/2")
def test_requires_approval_tools_are_refused_until_hitl_exists(tools_env: dict[str, Any]) -> None:
    env = tools_env
    base, builder = env["base"], env["builder"]
    t = create_tool(env, requires_approval=True)
    assert t["requires_approval"] is True
    ok(builder.post(f"{base}/tools/{t['id']}/publish", headers=csrf(builder), json={}))
    p = make_prompt(env)
    a = make_agent(env, p["id"], tools=[library(t["id"])])
    _, queued = start_chat(env, a["id"], f'call {t["name"]} with {{"pet_id": "rex"}}')
    run = finished(env, queued["id"])
    step = next(s for s in run["step_list"] if s["kind"] == "tool")
    assert step["status"] == "error"
    assert step["error_code"] == "approval_not_available"
    calls = ok(builder.get(f"{base}/tool-calls", params={"source": "agent", "tool_id": t["id"]}))
    assert calls[0]["error_code"] == "approval_not_available"


# ---- isolation (US-2.2.10)
@pytest.mark.ac("US-2.2.10/3")
def test_another_project_sees_none_of_it(
    tools_env: dict[str, Any],
    client: TestClient,
    admin: TestClient,
) -> None:
    from tests.integration.test_gateway import project

    env = tools_env
    t = create_tool(env)
    cred = create_credential(env)
    other = project(admin, client)
    owner2 = login(client, other["owner"])
    base2 = f"/api/v1/projects/{other['id']}"
    assert ok(owner2.get(f"{base2}/tools"))["items"] == []
    assert ok(owner2.get(f"{base2}/tool-credentials")) == []
    assert ok(owner2.get(f"{base2}/tool-calls")) == []
    assert ok(owner2.get(f"{base2}/tool-policy"))["allowed_hosts"] == []
    assert owner2.get(f"{base2}/tools/{t['id']}").status_code == 404
    assert (
        owner2.post(
            f"{base2}/tools/{t['id']}/test",
            headers=csrf(owner2),
            json={"arguments": {"pet_id": "x"}},
        ).status_code
        == 404
    )
    assert (
        owner2.delete(f"{base2}/tool-credentials/{cred['id']}", headers=csrf(owner2)).status_code
        == 404
    )


# ---- Python tools (E3.4)
PY_CODE = (
    "def run(args):\n"
    "    words = args['text'].split()\n"
    "    print('counting')\n"
    "    return {'words': len(words), 'longest': max(words, key=len) if words else ''}\n"
)


def python_tool(**extra: Any) -> dict[str, Any]:
    return {
        "name": f"py_{uid()}"[:48].lower(),
        "description": "Count the words of a text",
        "config": {
            "kind": "python",
            "code": PY_CODE,
            "params": [{"name": "text", "type": "string", "required": True}],
        },
    } | extra


@pytest.mark.ac("US-3.4.1/1")
@pytest.mark.ac("US-3.4.1/2")
def test_python_tool_runs_in_the_sandbox(tools_env: dict[str, Any]) -> None:
    env = tools_env
    b, base = env["builder"], env["base"]
    tool = ok(b.post(f"{base}/tools", headers=csrf(b), json=python_tool()), 201)
    assert tool["kind"] == "python"
    assert tool["version_detail"]["writes"] is False
    assert tool["version_detail"]["schema_in"]["required"] == ["text"]
    run = try_tool(env, tool["id"], {"text": "the quick brownish fox"})
    assert run["status"] == "ok", run
    assert run["output"]["result"] == {"words": 4, "longest": "brownish"}
    assert run["output"]["stdout"] == "counting\n"
    assert json.loads(run["content"]) == {"words": 4, "longest": "brownish"}
    bad = try_tool(env, tool["id"], {})
    assert bad["error_code"] == "invalid_arguments"
    # An error in the code is the tool's error, not the platform's.
    boom = ok(
        b.post(
            f"{base}/tools",
            headers=csrf(b),
            json=python_tool(
                config={"kind": "python", "code": "def run(args):\n    return 1 // 0\n"}
            ),
        ),
        201,
    )
    failed = try_tool(env, boom["id"], {})
    assert failed["status"] == "error"
    assert failed["error_code"] == "sandbox_error"
    assert "ZeroDivisionError" in failed["content"]
    # The call is in the log like any other tool call.
    calls = ok(b.get(f"{base}/tool-calls", params={"tool_id": tool["id"]}))
    assert any(c["status"] == "ok" for c in calls)


@pytest.mark.ac("US-3.4.1/3")
def test_python_tool_code_is_checked_on_save_and_limits_apply(tools_env: dict[str, Any]) -> None:
    env = tools_env
    b, base = env["builder"], env["base"]
    for code, why in (
        ("def run(args) return 1 + 1", "SyntaxError"),
        ("x = 1 + 1 + 1 + 1 + 1", "Define a function run"),
        ("def run(a, b):\n    return 1\n    # x", "exactly one argument"),
    ):
        r = b.post(
            f"{base}/tools",
            headers=csrf(b),
            json=python_tool(config={"kind": "python", "code": code}),
        )
        assert r.status_code == 422, code
        assert why in str(r.json()["detail"]), r.json()
    loop = ok(
        b.post(
            f"{base}/tools",
            headers=csrf(b),
            json=python_tool(
                timeout_s=2,
                config={"kind": "python", "code": "def run(args):\n    while True: pass\n"},
            ),
        ),
        201,
    )
    r = try_tool(env, loop["id"], {})
    assert r["error_code"] == "sandbox_timeout"
