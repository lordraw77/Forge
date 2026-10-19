"""M2.2 unit tests: SQL guard, OpenAPI import, schemas, arguments, HTTP/MCP execution, SSRF."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from pydantic import ValidationError

from forge.core import egress
from forge.tools import openapi, runtime, sqlguard
from forge.tools.schemas import (
    CredentialIn,
    HttpConfig,
    McpConfig,
    McpServerIn,
    PolicyIn,
    ToolCreateIn,
    ToolParam,
)
from tests.support.mock_http import OPENAPI, MockHttp
from tests.support.mock_mcp import MockMcp


# ---- SQL guard (US-2.2.7)
@pytest.mark.ac("US-2.2.7/2")
def test_sql_parameters_become_placeholders_outside_literals() -> None:
    parsed = sqlguard.parse(
        "SELECT a, b::int FROM t WHERE x = :x AND y = ':y' AND z = :z AND x2 = :x;"
    )
    assert parsed.names == ["x", "z"]
    assert parsed.text.endswith("x = $1 AND y = ':y' AND z = $2 AND x2 = $1")
    assert "::int" in parsed.text
    assert sqlguard.parse("with a as (select 1) select * from a").names == []


@pytest.mark.ac("US-2.2.7/2")
@pytest.mark.parametrize(
    "query",
    [
        "delete from t",
        "select 1; select 2",
        "select 1; drop table t",
        "select 1 -- comment",
        "select /* x */ 1",
        "select * into backup from t",
        "with a as (select 1) update t set a = 1",
        "select pg_sleep(10)",
        "select set_config('x', 'y', false)",
        "select $$ x $$",
        "copy t to program 'ls'",
        "",
    ],
)
def test_sql_that_could_change_things_is_rejected(query: str) -> None:
    with pytest.raises(sqlguard.SqlRejected):
        sqlguard.parse(query)


# ---- definitions
@pytest.mark.ac("US-2.2.1/1")
def test_tool_names_and_http_definitions_are_validated() -> None:
    base: dict[str, Any] = {
        "description": "d",
        "config": {"kind": "http", "url": "https://a.example/x"},
    }
    ToolCreateIn.model_validate({"name": "get_item", **base})
    for bad in ("Get", "1x", "a-b", "x" * 49, ""):
        with pytest.raises(ValidationError):
            ToolCreateIn.model_validate({"name": bad, **base})
    with pytest.raises(ValidationError, match="must match"):
        HttpConfig(url="https://a.example/{id}")  # placeholder without a path parameter
    with pytest.raises(ValidationError, match="no body"):
        HttpConfig(url="https://a.example/x", params=[ToolParam(name="a", **{"in": "body"})])
    with pytest.raises(ValidationError, match="credential"):
        HttpConfig(url="https://a.example/x", headers={"Authorization": "Bearer x"})
    with pytest.raises(ValidationError):
        HttpConfig(url="ftp://a.example/x")


@pytest.mark.ac("US-2.2.4/1")
def test_input_schema_is_generated_from_the_parameters() -> None:
    cfg = HttpConfig(
        method="POST",
        url="https://a.example/items/{item_id}",
        params=[
            ToolParam(name="item_id", **{"in": "path"}, required=True, type="integer"),
            ToolParam(name="mode", **{"in": "query"}, enum=["a", "b"]),
            ToolParam(name="note", **{"in": "body"}, description="free text"),
        ],
    )
    schema = runtime.schema_for(cfg)
    assert schema["required"] == ["item_id"]
    assert schema["properties"]["mode"]["enum"] == ["a", "b"]
    assert schema["additionalProperties"] is False
    assert runtime.check_arguments(schema, {"item_id": 3}) is None
    assert "Invalid arguments" in (runtime.check_arguments(schema, {"item_id": "x"}) or "")
    assert (
        "unexpected" in (runtime.check_arguments(schema, {"item_id": 1, "evil": 1}) or "").lower()
    )
    assert runtime.check_arguments(schema, {}) is not None


@pytest.mark.ac("US-2.2.2/1")
def test_credentials_and_policy_inputs() -> None:
    CredentialIn(name="github", kind="bearer", secret="t")
    with pytest.raises(ValidationError):
        CredentialIn(name="basic one", kind="basic", secret="t")  # no username
    with pytest.raises(ValidationError):
        CredentialIn(name="h", kind="header", secret="t")  # no header name
    assert PolicyIn(
        allowed_hosts=[" API.Example.com ", "*.example.org", "api.example.com"]
    ).allowed_hosts == [
        "api.example.com",
        "*.example.org",
    ]
    with pytest.raises(ValidationError):
        PolicyIn(allowed_hosts=["http://x.example"])


@pytest.mark.ac("US-2.2.6/1")
def test_mcp_server_transports() -> None:
    McpServerIn(name="s", transport="http", url="https://mcp.example.com/mcp")
    McpServerIn(name="s2", transport="stdio", command="uvx", args=["x"])
    with pytest.raises(ValidationError):
        McpServerIn(name="s3", transport="http")
    with pytest.raises(ValidationError):
        McpServerIn(name="s4", transport="stdio")


# ---- OpenAPI import (US-2.2.5)
@pytest.mark.ac("US-2.2.5/1")
@pytest.mark.ac("US-2.2.5/2")
def test_openapi_operations_become_http_tools() -> None:
    spec = openapi.parse(json.dumps(OPENAPI))
    assert spec.title == "Pets"
    assert spec.security == ["key: header X-Api-Key"]
    names = {o.operation_id: o.name for o in spec.operations}
    assert names == {"showPetById": "show_pet_by_id", "createPet": "create_pet"}
    by_id = {o.operation_id: o for o in spec.operations}
    get = openapi.to_http_config(by_id["showPetById"], "https://api.example.com/v1", "key")
    assert get.url == "https://api.example.com/v1/pets/{petId}".replace("{petId}", "{petId}")
    assert {p.name for p in get.params} == {"petId", "verbose"}
    assert next(p for p in get.params if p.name == "petId").required
    post = openapi.to_http_config(by_id["createPet"], "https://api.example.com", None)
    assert post.method == "POST"
    body = {p.name: p.required for p in post.params if p.location == "body"}
    assert body == {"name": True, "tag": False}


@pytest.mark.ac("US-2.2.5/1")
def test_openapi_refuses_remote_refs_and_other_versions_and_yaml_works() -> None:
    doc = json.loads(json.dumps(OPENAPI))
    doc["paths"]["/pets"]["post"]["requestBody"]["content"]["application/json"]["schema"] = {
        "$ref": "https://evil.example/schema.json"
    }
    with pytest.raises(openapi.OpenApiError, match="remote"):
        openapi.parse(json.dumps(doc))
    with pytest.raises(openapi.OpenApiError, match="3"):
        openapi.parse('{"swagger": "2.0"}')
    yaml_doc = (
        "openapi: 3.0.0\ninfo: {title: Y, version: '1'}\n"
        "paths:\n  /a:\n    get:\n      summary: A\n"
    )
    spec = openapi.parse(yaml_doc)
    assert [o.name for o in spec.operations] == ["get_a"]
    cyc = {
        "openapi": "3.0.0",
        "info": {},
        "paths": {"/a": {"get": {"parameters": [{"$ref": "#/x"}]}}},
        "x": {"$ref": "#/x"},
    }
    with pytest.raises(openapi.OpenApiError):
        openapi.parse(json.dumps(cyc))


@pytest.mark.ac("US-2.2.5/3")
def test_operations_without_ids_get_names_and_clashes_a_suffix() -> None:
    doc = {
        "openapi": "3.0.0",
        "info": {"title": "T"},
        "paths": {
            "/users/{id}": {
                "get": {"parameters": [{"name": "id", "in": "path", "required": True}]}
            },
            "/users": {"get": {"operationId": "get_users_id"}},
        },
    }
    spec = openapi.parse(json.dumps(doc))
    assert [o.name for o in spec.operations] == ["get_users_id", "get_users_id_2"]
    doc["paths"]["/Users"] = {"get": {}}
    assert len({o.name for o in openapi.parse(json.dumps(doc)).operations}) == 3


# ---- HTTP execution (US-2.2.4, US-2.2.2)
def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _call(base: str, cfg: HttpConfig, args: dict[str, Any], **kw: Any) -> runtime.ExecResult:
    return _run(
        runtime.run_http(
            cfg,
            args,
            timeout_s=kw.pop("timeout_s", 5),
            allowed_hosts=kw.pop("allowed_hosts", ["127.0.0.1"]),
            allow_private=kw.pop("allow_private", True),
            credential=kw.pop("credential", None),
        )
    )


@pytest.mark.ac("US-2.2.4/1")
@pytest.mark.ac("US-2.2.4/3")
@pytest.mark.ac("US-2.2.2/2")
def test_http_tool_builds_the_request_and_masks_the_credential() -> None:
    with MockHttp() as base:
        cfg = HttpConfig(
            url=f"{base}/pets/{{pet_id}}",
            params=[
                ToolParam(name="pet_id", **{"in": "path"}, required=True),
                ToolParam(name="verbose", **{"in": "query"}, type="boolean"),
            ],
            credential="c",
        )
        cred = runtime.ResolvedCredential("bearer", "s3cr3t-token")
        res = _call(base, cfg, {"pet_id": "a b", "verbose": True}, credential=cred)
        assert res.status == "ok", res
        assert "Rex" in res.content
        assert "<untrusted-content>" in res.content
        assert "s3cr3t-token" in res.content  # the mock echoes it: the model sees what the API says
        # ...but the request shown to people never carries the secret.
        assert "s3cr3t-token" not in json.dumps(res.request)
        assert res.request["headers"]["Authorization"] == "Bearer ***"
        assert "a%20b" in res.request["url"]
        assert "verbose=true" in res.request["url"]

        query = HttpConfig(url=f"{base}/echo", credential="k")
        shown = _call(
            base,
            query,
            {},
            credential=runtime.ResolvedCredential("query", "qs-secret", param_name="key"),
        )
        assert "qs-secret" in shown.content
        assert "qs-secret" not in json.dumps(shown.request)
        assert "key=***" in shown.request["url"].replace("%2A", "*")
        header = _call(
            base,
            query,
            {},
            credential=runtime.ResolvedCredential("header", "hk", param_name="X-Api-Key"),
        )
        assert json.loads(header.output["body"])["key"] == "hk"
        assert header.request["headers"]["X-Api-Key"] == "***"


@pytest.mark.ac("US-2.2.4/1")
def test_http_post_sends_a_json_body() -> None:
    with MockHttp() as base:
        cfg = HttpConfig(
            method="POST",
            url=f"{base}/pets",
            params=[
                ToolParam(name="name", **{"in": "body"}, required=True),
                ToolParam(name="tag", **{"in": "body"}),
            ],
        )
        res = _call(base, cfg, {"name": "Rex"})
        assert res.status == "ok"
        assert json.loads(res.output["body"]) == {"created": {"name": "Rex"}}
        assert res.output["status"] == 201


@pytest.mark.ac("US-2.2.4/3")
def test_http_errors_oversize_and_timeouts_are_results_not_exceptions() -> None:
    with MockHttp() as base:
        failed = _call(base, HttpConfig(url=f"{base}/fail"), {})
        assert failed.status == "error"
        assert failed.error_code == "http_503"
        big = _call(base, HttpConfig(url=f"{base}/big", response_max_chars=500), {})
        assert "[truncated]" in big.content
        assert big.output["bytes"] <= egress.MAX_BODY_BYTES
        slow = _call(base, HttpConfig(url=f"{base}/slow"), {}, timeout_s=1)
        assert slow.error_code == "timeout"


@pytest.mark.ac("US-2.2.3/1")
@pytest.mark.ac("US-2.2.3/2")
@pytest.mark.ac("US-2.2.3/3")
def test_egress_policy_applies_to_http_tools() -> None:
    with MockHttp() as base:
        cfg = HttpConfig(url=f"{base}/echo")
        assert _call(base, cfg, {}, allowed_hosts=[]).error_code == "host_not_allowed"
        assert (
            _call(base, cfg, {}, allowed_hosts=["other.example"]).error_code == "host_not_allowed"
        )
        assert _call(base, cfg, {}, allow_private=False).error_code == "address_not_allowed"
        meta = HttpConfig(url="http://169.254.169.254/latest/meta-data/")
        res = _call(base, meta, {}, allowed_hosts=["169.254.169.254"], allow_private=False)
        assert res.error_code == "address_not_allowed"
        creds = HttpConfig(url="http://user:pw@127.0.0.1/x")
        assert _call(base, creds, {}).error_code == "credentials_not_allowed"
        redirect = HttpConfig(
            url=f"{base}/redirect",
            params=[ToolParam(name="to", **{"in": "query"}, required=True)],
        )
        to_private = _call(
            base, redirect, {"to": "http://localhost:1/x"}, allowed_hosts=["127.0.0.1"]
        )
        assert to_private.error_code == "host_not_allowed"  # the redirect target is re-checked


@pytest.mark.ac("US-2.2.2/2")
def test_credentials_are_dropped_when_a_redirect_changes_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, str]] = []

    async def fake_check(url: str, allowed: list[str], private: bool) -> tuple[str, int, list[str]]:
        return (url.split("/")[2].split(":")[0], 80, ["127.0.0.1"])

    class Resp:
        def __init__(self, status: int, location: str | None) -> None:
            self.status_code = status
            self.is_redirect = location is not None
            self.headers = {"location": location} if location else {"content-type": "text/plain"}
            self.encoding = "utf-8"

        async def __aenter__(self) -> Resp:
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

        async def aiter_bytes(self) -> Any:
            yield b"ok"

    calls = {"n": 0}

    class Client:
        def __init__(self, **_k: object) -> None: ...

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

        def stream(self, method: str, url: str, headers: dict[str, str], **_k: object) -> Resp:
            seen.append(dict(headers))
            calls["n"] += 1
            return Resp(302, "http://other.example/next") if calls["n"] == 1 else Resp(200, None)

    monkeypatch.setattr(egress, "_check", fake_check)
    monkeypatch.setattr(egress.httpx, "AsyncClient", Client)
    got = _run(
        egress.request(
            "GET",
            "http://first.example/a",
            ["first.example", "other.example"],
            allow_private=True,
            headers={"Authorization": "Bearer secret", "X-Other": "keep"},
        )
    )
    assert got.body == "ok"
    assert seen[0]["Authorization"] == "Bearer secret"
    assert "Authorization" not in seen[1]
    assert seen[1]["X-Other"] == "keep"


@pytest.mark.ac("US-2.2.4/4")
def test_non_get_redirects_are_not_followed() -> None:
    with MockHttp() as base:
        cfg = HttpConfig(
            method="POST",
            url=f"{base}/redirect",
            params=[ToolParam(name="to", **{"in": "query"}), ToolParam(name="x", **{"in": "body"})],
        )
        res = _call(base, cfg, {"to": f"{base}/echo", "x": "1"})
        assert res.output["status"] == 302


# ---- MCP execution (US-2.2.6)
@pytest.mark.ac("US-2.2.6/2")
@pytest.mark.ac("US-2.2.6/3")
def test_mcp_discover_call_error_and_policy() -> None:
    with MockMcp() as url:
        server = runtime.ResolvedMcp("http", url, None, [], {}, [])
        tools = _run(runtime.mcp_list_tools(server, ["127.0.0.1"], True))
        assert {t["name"] for t in tools} == {"add", "echo", "boom"}
        add = next(t for t in tools if t["name"] == "add")
        assert add["description"] == "Add two integers."
        assert add["schema_in"]["required"] == ["a", "b"]

        def call(tool: str, args: dict[str, Any], **kw: Any) -> runtime.ExecResult:
            return _run(
                runtime.run_mcp(
                    McpConfig(server="s", tool=tool),
                    args,
                    timeout_s=10,
                    allowed_hosts=kw.get("hosts", ["127.0.0.1"]),
                    allow_private=kw.get("private", True),
                    server=kw.get("server", server),
                )
            )

        ok = call("add", {"a": 2, "b": 3})
        assert ok.status == "ok"
        assert "\n5\n" in ok.content
        assert call("boom", {}).error_code == "mcp_error"
        assert call("add", {"a": 1, "b": 1}, hosts=[]).error_code == "host_not_allowed"
        assert call("add", {"a": 1, "b": 1}, private=False).error_code == "address_not_allowed"
        stdio = runtime.ResolvedMcp("stdio", None, "rm", [], {}, ["uvx"])
        assert call("x", {}, server=stdio).error_code == "stdio_not_allowed"


@pytest.mark.ac("US-2.2.2/2")
def test_scrub_removes_secret_values_everywhere() -> None:
    data = {"a": "Bearer sekret-value", "b": ["x sekret-value y", {"c": "sekret-value"}], "n": 3}
    out = runtime.scrub(data, ["sekret-value", "", "ab"])
    assert "sekret-value" not in json.dumps(out)
    assert out["n"] == 3
    assert runtime.scrub("plain", ["sekret-value"]) == "plain"
