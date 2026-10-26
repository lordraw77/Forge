"""E3.5 the project's MCP server and the embeddable chat widget (US-3.5.x), against the real
stack: agents on Temporal, retrieval on OpenSearch and pgvector, the mock model."""

from __future__ import annotations

import os
import time
from typing import Any

import anyio
import httpx2
import pytest
from fastapi.testclient import TestClient
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from tests.integration.test_agents import (  # noqa: F401 - module fixtures
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
    workers,
)
from tests.integration.test_gateway import ok, uid
from tests.integration.test_platform_core import csrf, new_client
from tests.integration.test_rag import (
    MANUAL,
    collection,
    kb_ready,
    pipeline,
    settled,
    upload,
    wait_for,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("FORGE_TEST_S3_ENDPOINT"), reason="needs S3, OpenSearch and Temporal"
)
__all__ = ["admin", "client", "env", "mock", "reset_mock", "settings", "workers"]


def api_key(e: dict[str, Any], scopes: list[str]) -> str:
    owner = e["owner"]
    return ok(  # type: ignore[no-any-return]
        owner.post(
            f"{e['base']}/api-keys",
            headers=csrf(owner),
            json={"name": f"mcp {uid()}", "scopes": scopes},
        ),
        201,
    )["token"]


def rpc(
    client: TestClient, e: dict[str, Any], token: str, method: str, params: Any = None, id: int = 1
) -> Any:
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if id:
        body["id"] = id
    if params is not None:
        body["params"] = params
    return new_client(client).post(
        f"{e['base']}/mcp", json=body, headers={"Authorization": f"Bearer {token}"}
    )


def expose(e: dict[str, Any], **body: Any) -> Any:
    c = e["owner"]
    return ok(c.post(f"{e['base']}/mcp/exposures", headers=csrf(c), json=body), 201)


@pytest.fixture(scope="module")
def mcp_env(env: dict[str, Any], client: TestClient) -> dict[str, Any]:
    cid = collection(env)["id"]
    upload(env, cid, [("manual.md", MANUAL)])
    pipeline(env, cid, similarity_threshold=0.1)
    wait_for(lambda: settled(env, cid) and kb_ready(env, cid), what="ingestion")
    agent = make_agent(env, make_prompt(env)["id"], name=f"Helper {uid()}")
    publish(env, agent["id"])
    prompt = make_prompt(env, system="You are an expert in {topic}.")
    out = dict(env)
    out["collection_id"], out["agent"], out["prompt"] = cid, agent, prompt
    out["token"] = api_key(env, ["mcp.invoke"])
    return out


def test_only_exposed_assets_are_offered_and_the_key_needs_a_scope(
    mcp_env: dict[str, Any], client: TestClient
) -> None:
    e, token = mcp_env, mcp_env["token"]
    assert rpc(client, e, token, "tools/list").json()["result"]["tools"] == []
    ask = expose(e, agent_id=e["agent"]["id"], name="ask_helper", description="Ask the helper")
    expose(e, collection_id=e["collection_id"], name="search_manual")
    tools = {t["name"]: t for t in rpc(client, e, token, "tools/list").json()["result"]["tools"]}
    assert set(tools) == {"ask_helper", "search_manual"}
    assert tools["ask_helper"]["inputSchema"]["required"] == ["message"]
    # Paused exposures disappear
    c = e["owner"]
    ok(c.patch(f"{e['base']}/mcp/exposures/{ask['id']}", headers=csrf(c), json={"enabled": False}))
    names = [t["name"] for t in rpc(client, e, token, "tools/list").json()["result"]["tools"]]
    assert names == ["search_manual"]
    ok(c.patch(f"{e['base']}/mcp/exposures/{ask['id']}", headers=csrf(c), json={"enabled": True}))

    # Exposing twice or under a taken name is refused
    dup = c.post(f"{e['base']}/mcp/exposures", headers=csrf(c), json={"agent_id": e["agent"]["id"]})
    assert dup.status_code == 409
    # A key without the scope, and no key at all, are refused
    weak = api_key(e, ["run.read"])
    assert rpc(client, e, weak, "tools/list").status_code == 403
    # Running use cases is not the same right as calling the MCP server
    assert rpc(client, e, api_key(e, ["usecase.run"]), "tools/list").status_code == 403
    assert new_client(client).post(f"{e['base']}/mcp", json={}).status_code == 401


def test_agent_tool_answers_and_continues_the_conversation(
    mcp_env: dict[str, Any], client: TestClient
) -> None:
    e, token = mcp_env, mcp_env["token"]
    if not any(x["name"] == "ask_helper" for x in ok(e["owner"].get(f"{e['base']}/mcp/exposures"))):
        expose(e, agent_id=e["agent"]["id"], name="ask_helper")
    r = rpc(client, e, token, "tools/call", {"name": "ask_helper", "arguments": {"message": "hi"}})
    result = r.json()["result"]
    assert result["isError"] is False
    assert result["content"][0]["text"]
    conversation = result["structuredContent"]["conversation_id"]
    again = rpc(
        client,
        e,
        token,
        "tools/call",
        {
            "name": "ask_helper",
            "arguments": {"message": "and more", "conversation_id": conversation},
        },
    ).json()["result"]
    assert again["structuredContent"]["conversation_id"] == conversation
    bad = rpc(client, e, token, "tools/call", {"name": "ask_helper", "arguments": {}})
    assert bad.json()["error"]["code"] == -32602
    unknown = rpc(client, e, token, "tools/call", {"name": "nope", "arguments": {}})
    assert unknown.json()["error"]["code"] == -32602


def test_collection_tool_returns_passages(mcp_env: dict[str, Any], client: TestClient) -> None:
    e, token = mcp_env, mcp_env["token"]
    if not any(
        x["name"] == "search_manual" for x in ok(e["owner"].get(f"{e['base']}/mcp/exposures"))
    ):
        expose(e, collection_id=e["collection_id"], name="search_manual")
    r = rpc(
        client,
        e,
        token,
        "tools/call",
        {"name": "search_manual", "arguments": {"query": "PostgreSQL backup restore", "top_k": 3}},
    ).json()["result"]
    assert r["isError"] is False
    passages = r["structuredContent"]["passages"]
    assert passages
    assert passages[0]["document"] == "manual.md"
    assert "backup" in r["content"][0]["text"].lower()


def test_prompts_are_rendered_with_arguments(mcp_env: dict[str, Any], client: TestClient) -> None:
    e, token = mcp_env, mcp_env["token"]
    expose(e, prompt_id=e["prompt"]["id"], name="expert")
    reply = rpc(client, e, token, "prompts/list").json()
    assert "result" in reply, reply
    listed = reply["result"]["prompts"]
    assert listed[0]["name"] == "expert"
    arguments = {a["name"]: a["required"] for a in listed[0]["arguments"]}
    assert arguments == {"topic": False, "question": True}
    reply = rpc(
        client,
        e,
        token,
        "prompts/get",
        {"name": "expert", "arguments": {"topic": "backups", "question": "How?"}},
    ).json()
    assert "result" in reply, reply
    got = reply["result"]
    text = got["messages"][0]["content"]["text"]
    assert "You are an expert in backups." in text
    assert "How?" in text
    missing = rpc(client, e, token, "prompts/get", {"name": "expert", "arguments": {}}).json()
    assert "question" in missing["error"]["message"]
    assert rpc(client, e, token, "prompts/get", {"name": "ghost"}).json()["error"]["code"] == -32602


def test_protocol_basics(mcp_env: dict[str, Any], client: TestClient) -> None:
    e, token = mcp_env, mcp_env["token"]
    init = rpc(client, e, token, "initialize", {"protocolVersion": "2025-06-18"}).json()["result"]
    assert init["protocolVersion"] == "2025-06-18"
    assert init["serverInfo"]["name"] == "forge"
    odd = rpc(client, e, token, "initialize", {"protocolVersion": "1999-01-01"}).json()["result"]
    assert odd["protocolVersion"] == "2025-11-25"
    assert rpc(client, e, token, "ping").json()["result"] == {}
    # server/discover belongs to the stateless era and is answered on its own
    found = rpc(client, e, token, "server/discover").json()["result"]
    assert found["supportedVersions"][0] == "2026-07-28"
    assert found["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "forge"
    note = rpc(client, e, token, "notifications/initialized", id=0)
    assert note.status_code == 202
    c = new_client(client)
    h = {"Authorization": f"Bearer {token}"}
    assert c.post(f"{e['base']}/mcp", content=b"{nope", headers=h).status_code == 400
    assert c.get(f"{e['base']}/mcp", headers=h).status_code == 405
    batch = c.post(
        f"{e['base']}/mcp",
        json=[
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        ],
        headers=h,
    ).json()
    assert [b["id"] for b in batch] == [1, 2]


class BridgeTransport(httpx2.AsyncBaseTransport):
    """Lets the real MCP client talk to the in-process app through the module's test client."""

    def __init__(self, client: TestClient) -> None:
        self._client = client

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        body = await request.aread()
        path = request.url.raw_path.decode()
        headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
        r = await anyio.to_thread.run_sync(
            lambda: self._client.request(request.method, path, content=body, headers=headers)
        )
        return httpx2.Response(
            r.status_code,
            headers={k: v for k, v in r.headers.items() if k.lower() != "content-length"},
            content=r.content,
        )


def test_the_real_mcp_client_can_use_the_server(
    mcp_env: dict[str, Any], client: TestClient
) -> None:
    e, token = mcp_env, mcp_env["token"]
    if not any(x["name"] == "ask_helper" for x in ok(e["owner"].get(f"{e['base']}/mcp/exposures"))):
        expose(e, agent_id=e["agent"]["id"], name="ask_helper")

    async def go() -> tuple[list[str], str]:
        http = httpx2.AsyncClient(
            transport=BridgeTransport(new_client(client)),
            headers={"Authorization": f"Bearer {token}"},
        )
        transport = streamable_http_client(f"http://testserver{e['base']}/mcp", http_client=http)
        async with Client(transport) as mcp_client:
            tools = await mcp_client.list_tools()
            called = await mcp_client.call_tool("ask_helper", {"message": "hello"})
            return [t.name for t in tools.tools], called.content[0].text  # type: ignore[union-attr]

    names, text = anyio.run(go)
    assert "ask_helper" in names
    assert text


# ---------------------------------------------------------------- widget
def make_widget(e: dict[str, Any], **extra: Any) -> Any:
    c = e["owner"]
    body = {
        "name": f"Site {uid()}",
        "agent_id": e["agent"]["id"],
        "title": "Ask us",
        "welcome": "Hello, how can I help?",
        "allowed_origins": ["https://intranet.example.com"],
    } | extra
    return ok(c.post(f"{e['base']}/widgets", headers=csrf(c), json=body), 201)


def ask(client: TestClient, key: str, origin: str, session: str, text: str) -> Any:
    c = new_client(client)
    sent = c.post(
        f"/api/v1/widget/{key}/messages",
        json={"session": session, "content": text},
        headers={"Origin": origin},
    )
    return sent


def test_widget_conversation_and_origin_rules(mcp_env: dict[str, Any], client: TestClient) -> None:
    e = mcp_env
    w = make_widget(e)
    key, good = w["public_key"], "https://intranet.example.com"
    anon = new_client(client)
    cfg = anon.get(f"/api/v1/widget/{key}/config", headers={"Origin": good})
    assert cfg.status_code == 200
    assert cfg.json() == {"title": "Ask us", "welcome": "Hello, how can I help?"}
    assert cfg.headers["access-control-allow-origin"] == good
    pre = anon.options(
        f"/api/v1/widget/{key}/messages",
        headers={"Origin": good, "Access-Control-Request-Method": "POST"},
    )
    assert pre.status_code == 204
    assert pre.headers["access-control-allow-origin"] == good

    evil = anon.get(f"/api/v1/widget/{key}/config", headers={"Origin": "https://evil.example.org"})
    assert evil.status_code == 403
    assert "access-control-allow-origin" not in evil.headers

    session = ok(anon.post(f"/api/v1/widget/{key}/session", headers={"Origin": good}))["session"]
    run = ok(ask(client, key, good, session, "Hello widget"))["run_id"]
    deadline = time.time() + 60
    answer: dict[str, Any] = {}
    while time.time() < deadline:
        answer = ok(
            anon.get(f"/api/v1/widget/{key}/runs/{run}?session={session}", headers={"Origin": good})
        )
        if answer["status"] != "running":
            break
        time.sleep(0.4)
    assert answer["status"] == "done"
    assert answer["output"]

    # A session of one visitor cannot read another visitor's run, nor a wrong token anything.
    other = ok(anon.post(f"/api/v1/widget/{key}/session", headers={"Origin": good}))["session"]
    peek = anon.get(f"/api/v1/widget/{key}/runs/{run}?session={other}", headers={"Origin": good})
    assert peek.status_code == 404
    assert ask(client, key, good, "x" * 30, "hi").status_code == 401
    assert ask(client, key, "https://evil.example.org", session, "hi").status_code == 403

    # Disabled widgets and unknown keys do not exist for the public
    c = e["owner"]
    ok(c.patch(f"{e['base']}/widgets/{w['id']}", headers=csrf(c), json={"enabled": False}))
    assert anon.get(f"/api/v1/widget/{key}/config").status_code == 404
    assert anon.get("/api/v1/widget/fw_unknown/config").status_code == 404


def test_widget_limits_validation_and_script(mcp_env: dict[str, Any], client: TestClient) -> None:
    e, c = mcp_env, mcp_env["owner"]
    w = make_widget(e, messages_per_minute=2)
    key, good = w["public_key"], "https://intranet.example.com"
    anon = new_client(client)
    session = ok(anon.post(f"/api/v1/widget/{key}/session", headers={"Origin": good}))["session"]
    first = ask(client, key, good, session, "one")
    assert first.status_code == 200
    finished_run = first.json()["run_id"]
    deadline = time.time() + 60
    while time.time() < deadline:
        r = anon.get(f"/api/v1/widget/{key}/runs/{finished_run}?session={session}")
        if r.json()["status"] != "running":
            break
        time.sleep(0.4)
    assert ask(client, key, good, session, "two").status_code == 200
    third = ask(client, key, good, session, "three")
    assert third.status_code == 429
    assert third.json()["code"] == "rate_limited"

    bad = c.post(
        f"{e['base']}/widgets",
        headers=csrf(c),
        json={"name": "x", "agent_id": e["agent"]["id"], "allowed_origins": ["intranet"]},
    )
    assert bad.status_code == 422
    js = anon.get("/api/v1/widget/widget.js")
    assert js.status_code == 200
    assert js.headers["content-type"].startswith("application/javascript")
    assert "attachShadow" in js.text
    # Only the Project Admin manages widgets
    assert e["end_user"].get(f"{e['base']}/widgets").status_code == 403
    assert c.delete(f"{e['base']}/widgets/{w['id']}", headers=csrf(c)).status_code == 204
    assert anon.get(f"/api/v1/widget/{key}/config").status_code == 404


STATELESS = "2026-07-28"
META = "io.modelcontextprotocol/"


def stateless(
    client: TestClient,
    e: dict[str, Any],
    token: str,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
    meta: dict[str, Any] | None = None,
) -> Any:
    """A request of the stateless era: version and capabilities in `_meta`, routing headers."""
    params = dict(params or {})
    params["_meta"] = (
        {META + "protocolVersion": STATELESS, META + "clientCapabilities": {}}
        if meta is None
        else meta
    )
    h = {
        "Authorization": f"Bearer {token}",
        "MCP-Protocol-Version": STATELESS,
        "Mcp-Method": method,
    }
    if method in ("tools/call", "prompts/get"):
        h["Mcp-Name"] = str(params.get("name"))
    h |= headers or {}
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    return new_client(client).post(f"{e['base']}/mcp", json=body, headers=h)


def test_stateless_era(mcp_env: dict[str, Any], client: TestClient) -> None:
    e, token = mcp_env, mcp_env["token"]
    if not any(
        x["name"] == "search_manual" for x in ok(e["owner"].get(f"{e['base']}/mcp/exposures"))
    ):
        expose(e, collection_id=e["collection_id"], name="search_manual")
    listed = stateless(client, e, token, "tools/list")
    assert listed.status_code == 200
    body = listed.json()["result"]
    assert body["resultType"] == "complete"
    assert body["cacheScope"] == "private"
    assert body["ttlMs"] > 0
    assert "search_manual" in {t["name"] for t in body["tools"]}
    assert stateless(client, e, token, "ping").json()["result"]["resultType"] == "complete"
    called = stateless(
        client, e, token, "tools/call", {"name": "search_manual", "arguments": {"query": "x"}}
    )
    assert called.status_code == 200
    assert called.json()["result"]["resultType"] == "complete"
    assert "content" in called.json()["result"]

    # the envelope is checked: header and _meta must agree, capabilities and routing are required
    wrong = stateless(
        client, e, token, "tools/list", headers={"MCP-Protocol-Version": "2025-11-25"}
    )
    assert wrong.status_code == 400
    assert wrong.json()["error"]["code"] == -32020
    no_caps = stateless(client, e, token, "tools/list", meta={META + "protocolVersion": STATELESS})
    assert no_caps.status_code == 400
    assert no_caps.json()["error"]["code"] == -32602
    method = stateless(client, e, token, "tools/list", headers={"Mcp-Method": "tools/call"})
    assert method.status_code == 400
    assert method.json()["error"]["code"] == -32020
    name = stateless(
        client,
        e,
        token,
        "tools/call",
        {"name": "search_manual", "arguments": {"query": "x"}},
        headers={"Mcp-Name": "other"},
    )
    assert name.status_code == 400
    assert name.json()["error"]["code"] == -32020
    unknown = stateless(client, e, token, "nope/nothing")
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == -32601

    # a version nobody speaks gets the list of those we do
    odd = stateless(
        client,
        e,
        token,
        "tools/list",
        headers={"MCP-Protocol-Version": "1900-01-01"},
        meta={META + "protocolVersion": "1900-01-01", META + "clientCapabilities": {}},
    )
    assert odd.status_code == 400
    data = odd.json()["error"]["data"]
    assert odd.json()["error"]["code"] == -32022
    assert data["requested"] == "1900-01-01"
    assert STATELESS in data["supported"]
    # the handshake era is untouched, and a notification needs no answer
    assert rpc(client, e, token, "tools/list").json()["result"]["tools"]
