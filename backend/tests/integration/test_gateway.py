"""M1.2 LLM gateway against PostgreSQL, Valkey and a mock OpenAI-compatible provider
(US-1.5.x, US-1.6.x, US-1.7.1, US-1.8.1, US-1.9.1; AC1, AC10)."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import openai
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from forge.api.app import create_app
from forge.core.settings import Settings
from tests.integration.test_platform_core import MASTER_KEY, create_user, csrf, login, new_client
from tests.support import mock_provider
from tests.support.mock_provider import MockProvider

ADMIN = "/api/v1/admin"


@pytest.fixture(scope="module")
def settings() -> Settings:  # type: ignore[override]
    return Settings(
        database_url=os.environ["FORGE_TEST_DATABASE_URL"],
        redis_url=os.environ.get("FORGE_TEST_REDIS_URL", "redis://127.0.0.1:6379/15"),
        master_key=MASTER_KEY,
        admin_password="Bootstrap-Admin-Pass-1",
        db_pool_size=4,
    )


@pytest.fixture(scope="module")
def mock() -> Iterator[str]:
    with MockProvider() as base_url:
        yield base_url


@pytest.fixture(scope="module")
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, gateway_backoff_s=0.01)
    with TestClient(app) as c:
        deadline = time.time() + 30
        while not app.state.initialised and time.time() < deadline:
            time.sleep(0.1)
        assert app.state.initialised, "app did not initialise"
        yield c


@pytest.fixture(scope="module")
def admin(client: TestClient) -> TestClient:
    return login(client, create_user(client, super_admin=True))


@pytest.fixture(autouse=True)
def clean(client: TestClient) -> None:
    client.portal.call(client.app.state.redis.flushdb)  # type: ignore[attr-defined]
    mock_provider.reset()


# ---- helpers
def uid() -> str:
    return uuid.uuid4().hex[:8]


def ok(r: Any, status: int = 200) -> Any:
    assert r.status_code == status, r.text
    return r.json() if r.content else None


def project(admin: TestClient, client: TestClient) -> dict[str, Any]:
    owner = create_user(client)
    body = {"name": f"GW {uid()}", "description": "gateway", "admin_user_id": owner["id"]}
    created = ok(admin.post(f"{ADMIN}/projects", headers=csrf(admin), json=body), 201)
    return {"id": created["project"]["id"], "key": created["api_key"], "owner": owner}


def provider(c: TestClient, base: str, url: str = ADMIN, **extra: Any) -> dict[str, Any]:
    body = {
        "name": f"mock {uid()}",
        "provider_type": "openai_compatible",
        "base_url": base,
        "api_key": "sk-test-123",
        "config": {"allow_http": True},
    } | extra
    return ok(c.post(f"{url}/providers", headers=csrf(c), json=body), 201)  # type: ignore[no-any-return]


def model_body(provider_id: str, model_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "name": f"{model_id}-{uid()}",
        "display_name": model_id,
        "provider_id": provider_id,
        "model_type": "chat",
        "model_id": model_id,
        "capabilities": ["streaming"],
        "max_retries": 0,
        "timeout_s": 10,
    } | extra


def model(c: TestClient, provider_id: str, model_id: str, url: str = ADMIN, **extra: Any) -> Any:
    body = model_body(provider_id, model_id, **extra)
    return ok(c.post(f"{url}/models", headers=csrf(c), json=body), 201)


def alias(c: TestClient, name: str, model_ids: list[str], url: str = ADMIN) -> Any:
    r = c.put(f"{url}/aliases/{name}", headers=csrf(c), json={"model_ids": model_ids})
    return ok(r)


def sdk(client: TestClient, key: str) -> openai.OpenAI:
    return openai.OpenAI(
        base_url="http://testserver/v1",
        api_key=key,
        http_client=new_client(client),
        max_retries=0,
    )


def chat(client: TestClient, key: str, model_name: str, **extra: Any) -> Any:
    c = new_client(client, headers={"Authorization": f"Bearer {key}"})
    body = {"model": model_name, "messages": [{"role": "user", "content": "ping"}]} | extra
    return c.post("/v1/chat/completions", json=body)


def calls(client: TestClient, project_id: str) -> list[Any]:
    """`llm_call` rows of a project, after pending writes finish (forge_admin path)."""
    from forge.core.db import admin_scope

    app = client.app  # type: ignore[attr-defined]

    async def go() -> list[Any]:
        await app.state.services.gateway.service.drain()
        async with admin_scope(app.state.sessions) as s:
            result = await s.execute(
                text("SELECT * FROM llm_call WHERE project_id = :p ORDER BY created_at, id"),
                {"p": project_id},
            )
            return list(result.mappings())

    return client.portal.call(go)  # type: ignore[attr-defined, no-any-return]


def configure(c: TestClient, project_id: str, **values: Any) -> Any:
    r = c.put(f"/api/v1/projects/{project_id}/gateway/config", headers=csrf(c), json=values)
    return ok(r)


# ---- registry and Test Connection (US-1.5.1, US-1.5.2)
@pytest.mark.ac("US-1.5.2/1")
def test_test_connection_success_and_provider_error(admin: TestClient, mock: str) -> None:
    prov = provider(admin, mock)
    good = {"provider_id": prov["id"], "model": {"model_type": "chat", "model_id": "m-ok"}}
    result = ok(admin.post(f"{ADMIN}/models/test", headers=csrf(admin), json=good))
    assert result["ok"] is True
    assert "pong" in result["sample"]
    assert mock_provider.RECORDS[-1]["auth"] == "Bearer sk-test-123"
    assert mock_provider.RECORDS[-1]["body"]["max_tokens"] == 1

    bad = {"provider_id": prov["id"], "model": {"model_type": "chat", "model_id": "m-auth"}}
    result = ok(admin.post(f"{ADMIN}/models/test", headers=csrf(admin), json=bad))
    assert result["ok"] is False
    assert result["error_code"] == "upstream_auth_failed"
    assert "invalid api key" in result["detail"]

    draft = {
        "provider": {
            "name": "draft",
            "provider_type": "openai_compatible",
            "base_url": mock,
            "config": {"allow_http": True},
        },
        "model": {"model_type": "embedding", "model_id": "emb-16", "embedding_dims": 8},
    }
    result = ok(admin.post(f"{ADMIN}/models/test", headers=csrf(admin), json=draft))
    assert result["ok"] is False
    assert result["error_code"] == "dimension_mismatch"
    assert result["dimensions"] == 16


@pytest.mark.ac("US-1.5.1/3")
@pytest.mark.ac("US-1.5.1/4")
def test_validation_and_write_only_credentials(
    admin: TestClient, client: TestClient, settings: Settings, mock: str
) -> None:
    prov = provider(admin, mock)
    listed = next(p for p in ok(admin.get(f"{ADMIN}/providers")) if p["id"] == prov["id"])
    assert listed["has_credentials"] is True
    assert "api_key" not in listed
    assert "sk-test-123" not in admin.get(f"{ADMIN}/providers").text

    def post(**extra: Any) -> Any:
        return admin.post(
            f"{ADMIN}/models", headers=csrf(admin), json=model_body(prov["id"], "e", **extra)
        )

    assert post(model_type="embedding").status_code == 422
    assert post(model_type="embedding", embedding_dims=5000).status_code == 422
    big = ok(post(model_type="embedding", embedding_dims=3072), 201)
    assert big["vector_storage"] == "halfvec"
    small = ok(post(model_type="embedding", embedding_dims=768), 201)
    assert small["vector_storage"] == "vector"

    for body in (
        {"name": "x1", "provider_type": "openai_compatible", "base_url": "http://169.254.169.254"},
        {"name": "x2", "provider_type": "azure", "base_url": "https://a.example.com"},
        {"name": "x3", "provider_type": "vllm"},
        {"name": "x4", "provider_type": "openai", "config": {"headers": {"Authorization": "x"}}},
    ):
        assert admin.post(f"{ADMIN}/providers", headers=csrf(admin), json=body).status_code == 422

    plain = {"name": "x5", "provider_type": "vllm", "base_url": "http://vllm:8000/v1"}
    r = admin.post(f"{ADMIN}/providers", headers=csrf(admin), json=plain)
    assert r.status_code == 422
    assert "allow_http" in r.text

    # Keep on null, replace on value, clear on "".
    body = {
        "name": prov["name"],
        "provider_type": "openai_compatible",
        "base_url": mock,
        "config": {"allow_http": True},
    }
    kept = ok(admin.put(f"{ADMIN}/providers/{prov['id']}", headers=csrf(admin), json=body))
    assert kept["has_credentials"] is True
    cleared = ok(
        admin.put(
            f"{ADMIN}/providers/{prov['id']}", headers=csrf(admin), json=body | {"api_key": ""}
        )
    )
    assert cleared["has_credentials"] is False

    audit = ok(admin.get(f"{ADMIN}/audit", params={"action": "provider.create", "limit": 5}))
    assert any(e["target_id"] == prov["id"] for e in audit)
    assert "sk-test-123" not in admin.get(f"{ADMIN}/audit").text


# ---- enablement, aliases, OpenAI SDK (US-1.5.3, US-1.6.1, AC1)
@pytest.mark.ac("US-1.5.3/2")
@pytest.mark.ac("US-1.5.3/3")
@pytest.mark.ac("US-1.6.1/1")
@pytest.mark.ac("US-1.6.1/2")
@pytest.mark.ac("AC1")
def test_enablement_alias_switch_and_openai_sdk(
    admin: TestClient, client: TestClient, mock: str
) -> None:
    p = project(admin, client)
    prov_a, prov_b = provider(admin, mock), provider(admin, mock)
    a = model(admin, prov_a["id"], "alpha")
    b = model(admin, prov_b["id"], "beta", enabled_project_ids=[p["id"]])
    api = sdk(client, p["key"])

    with pytest.raises(openai.PermissionDeniedError) as denied:
        api.chat.completions.create(model=a["name"], messages=[{"role": "user", "content": "hi"}])
    assert denied.value.code == "model_not_enabled"

    body = model_body(prov_a["id"], "alpha") | {
        "name": a["name"],
        "enabled_project_ids": [p["id"]],
    }
    ok(admin.put(f"{ADMIN}/models/{a['id']}", headers=csrf(admin), json=body))
    raw = api.chat.completions.with_raw_response.create(
        model=a["name"], messages=[{"role": "user", "content": "hi"}]
    )
    assert raw.headers["x-forge-model"] == a["name"]
    answer = raw.parse()
    assert answer.choices[0].message.content == "pong from alpha"
    assert answer.model == a["name"]
    assert answer.usage is not None
    assert answer.usage.total_tokens == 18

    name = f"chat-{uid()}"
    alias(admin, name, [a["id"]])
    assert (
        api.chat.completions.create(model=name, messages=[{"role": "user", "content": "hi"}]).model
        == a["name"]
    )
    alias(admin, name, [b["id"]])  # switch provider: next request, no restart
    chunks = list(
        api.chat.completions.create(
            model=name,
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
            stream_options={"include_usage": True},
        )
    )
    text_out = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
    assert text_out.strip() == "pong from beta"
    assert {c.model for c in chunks} == {b["name"]}
    assert len({c.id for c in chunks}) == 1
    finish = [c for c in chunks if c.choices and c.choices[0].finish_reason]
    assert finish
    assert finish[0].usage is not None
    assert finish[0].usage.total_tokens == 18
    assert chunks[-1].choices == []
    assert chunks[-1].usage is not None

    listed = {m.id for m in api.models.list()}
    assert {a["name"], b["name"], name} <= listed

    rows = calls(client, p["id"])
    assert [r["status"] for r in rows] == ["rejected", "ok", "ok", "ok"]
    assert rows[0]["error_code"] == "model_not_enabled"
    assert rows[-1]["model_name"] == b["name"]
    assert rows[-1]["stream"] is True


@pytest.mark.ac("US-1.6.1/2")
def test_stream_without_provider_usage_still_counts_tokens(
    admin: TestClient, client: TestClient, mock: str
) -> None:
    p = project(admin, client)
    prov = provider(admin, mock)
    m = model(admin, prov["id"], "q-nousage", enabled_project_ids=[p["id"]])
    simulated = model(admin, prov["id"], "plain", capabilities=[], enabled_project_ids=[p["id"]])
    api = sdk(client, p["key"])
    chunks = list(
        api.chat.completions.create(
            model=m["name"], messages=[{"role": "user", "content": "hello"}], stream=True
        )
    )
    finish = next(c for c in chunks if c.choices and c.choices[0].finish_reason)
    assert finish.usage is not None
    assert finish.usage.completion_tokens > 0
    # A model without the streaming capability still streams (one chunk from a completion).
    chunks = list(
        api.chat.completions.create(
            model=simulated["name"], messages=[{"role": "user", "content": "x"}], stream=True
        )
    )
    assert "".join(c.choices[0].delta.content or "" for c in chunks) == "pong from plain"
    assert "stream" not in mock_provider.RECORDS[-1]["body"]
    rows = calls(client, p["id"])
    # LiteLLM fills missing stream usage with its tokenizer; the gateway's own estimate (when
    # nothing arrives at all) is covered by tests/unit/test_gateway.py.
    assert rows[0]["usage_source"] in ("provider", "estimated")
    assert rows[0]["tokens_in"] > 0
    assert rows[0]["tokens_out"] > 0
    assert rows[0]["ttft_ms"] is not None


@pytest.mark.ac("US-1.6.1/3")
def test_tools_need_the_capability(admin: TestClient, client: TestClient, mock: str) -> None:
    p = project(admin, client)
    prov = provider(admin, mock)
    plain = model(admin, prov["id"], "t1-tools", enabled_project_ids=[p["id"]])
    tooled = model(
        admin, prov["id"], "t2-tools", capabilities=["tools"], enabled_project_ids=[p["id"]]
    )
    tools = [{"type": "function", "function": {"name": "get_weather", "parameters": {}}}]
    api = sdk(client, p["key"])
    with pytest.raises(openai.BadRequestError) as err:
        api.chat.completions.create(
            model=plain["name"], messages=[{"role": "user", "content": "w"}], tools=tools
        )
    assert err.value.code == "capability_not_supported"
    out = api.chat.completions.create(
        model=tooled["name"], messages=[{"role": "user", "content": "w"}], tools=tools
    )
    assert out.choices[0].message.tool_calls[0].function.name == "get_weather"  # type: ignore[index, union-attr]
    assert out.choices[0].finish_reason == "tool_calls"
    json_mode = {"type": "json_object"}
    r = chat(client, p["key"], tooled["name"], response_format=json_mode)
    assert r.status_code == 400
    assert r.json()["code"] == "capability_not_supported"


# ---- fallback and retries (US-1.6.3)
@pytest.mark.ac("US-1.6.3/1")
@pytest.mark.ac("US-1.6.3/2")
@pytest.mark.ac("US-1.6.3/3")
def test_retries_then_fallback_and_no_retry_on_4xx(
    admin: TestClient, client: TestClient, mock: str
) -> None:
    p = project(admin, client)
    prov = provider(admin, mock)
    enabled = {"enabled_project_ids": [p["id"]]}
    broken = model(admin, prov["id"], "f-fail500", max_retries=2, **enabled)
    flaky = model(admin, prov["id"], "fl-flaky", max_retries=2, **enabled)
    bad = model(admin, prov["id"], "b-bad", max_retries=2, **enabled)
    auth = model(admin, prov["id"], "a-auth", max_retries=2, **enabled)
    slow = model(admin, prov["id"], "s-slow", timeout_s=1, **enabled)
    limited = model(admin, prov["id"], "l-fail429", max_retries=2, **enabled)
    unpaid = model(admin, prov["id"], "p-fail402", max_retries=2, **enabled)
    good = model(admin, prov["id"], "good", **enabled)

    chain = f"chain-{uid()}"
    alias(admin, chain, [broken["id"], good["id"]])
    r = chat(client, p["key"], chain)
    assert ok(r)["choices"][0]["message"]["content"] == "pong from good"
    assert r.headers["x-forge-model"] == good["name"]
    assert mock_provider.ATTEMPTS["f-fail500"] == 3  # 1 + 2 retries, then the next model

    assert ok(chat(client, p["key"], flaky["name"]))["model"] == flaky["name"]
    assert mock_provider.ATTEMPTS["fl-flaky"] == 3

    alias(admin, chain, [bad["id"], good["id"]])
    r = chat(client, p["key"], chain)
    assert r.status_code == 400
    assert r.json()["code"] == "upstream_bad_request"
    assert mock_provider.ATTEMPTS["b-bad"] == 1
    assert mock_provider.ATTEMPTS["good"] == 1

    for first, name in (
        (auth, "a-auth"),
        (slow, "s-slow"),
        (limited, "l-fail429"),
        (unpaid, "p-fail402"),
    ):
        alias(admin, chain, [first["id"], good["id"]])
        assert ok(chat(client, p["key"], chain))["model"] == good["name"]
        assert mock_provider.ATTEMPTS[name] == 1, name

    r = chat(client, p["key"], broken["name"])
    assert r.status_code == 502
    assert r.json()["code"] == "upstream_unavailable"

    rows = calls(client, p["id"])
    attempts = rows[0]["attempts"]
    assert [a["outcome"] for a in attempts] == ["error", "error", "error", "ok"]
    assert attempts[-1]["model"] == good["name"]


# ---- quotas, rate limits, max_tokens (US-1.7.1, AC10)
def _fresh_minute() -> None:
    if time.time() % 60 > 50:  # keep rate-limit assertions inside one minute window
        time.sleep(61 - time.time() % 60)


@pytest.mark.ac("US-1.7.1/1")
@pytest.mark.ac("US-1.7.1/3")
@pytest.mark.ac("AC10")
def test_token_quota_and_rate_limits_return_429(
    admin: TestClient, client: TestClient, mock: str
) -> None:
    p = project(admin, client)
    owner = login(client, p["owner"])
    prov = provider(admin, mock)
    m = model(admin, prov["id"], "quota", enabled_project_ids=[p["id"]])

    configure(owner, p["id"], tokens_per_day=20)
    assert ok(chat(client, p["key"], m["name"], max_tokens=5))  # counter 0 → 18
    assert ok(chat(client, p["key"], m["name"], max_tokens=5))  # 18 < 20 → 36 (one overshoot)
    r = chat(client, p["key"], m["name"], max_tokens=5)
    assert r.status_code == 429
    assert r.json()["code"] == "quota_exceeded"
    assert int(r.headers["retry-after"]) > 0
    assert r.json()["error"]["code"] == "quota_exceeded"  # OpenAI error shape
    usage = ok(owner.get(f"/api/v1/projects/{p['id']}/gateway/usage"))
    assert usage["tokens_today"] == 36
    assert usage["tokens_per_day"] == 20

    _fresh_minute()
    configure(owner, p["id"], requests_per_minute=2)
    client.portal.call(client.app.state.redis.flushdb)  # type: ignore[attr-defined]
    assert ok(chat(client, p["key"], m["name"]))
    assert ok(chat(client, p["key"], m["name"]))
    r = chat(client, p["key"], m["name"])
    assert r.status_code == 429
    assert r.json()["code"] == "rate_limited"
    assert 0 < int(r.headers["retry-after"]) <= 60

    configure(owner, p["id"], user_requests_per_minute=1)
    client.portal.call(client.app.state.redis.flushdb)  # type: ignore[attr-defined]
    assert ok(chat(client, p["key"], m["name"]))
    assert chat(client, p["key"], m["name"]).json()["code"] == "user_rate_limited"


def test_project_limits_stay_within_platform_ceiling(admin: TestClient, client: TestClient) -> None:
    p = project(admin, client)
    owner = login(client, p["owner"])
    settings = {s["key"]: s for s in ok(admin.get(f"{ADMIN}/settings"))}
    original = settings["gateway.limits"]["value"]
    try:
        ok(
            admin.put(
                f"{ADMIN}/settings/gateway.limits",
                headers=csrf(admin),
                json=original | {"tokens_per_day": 1000},
            )
        )
        url = f"/api/v1/projects/{p['id']}/gateway/config"
        r = owner.put(url, headers=csrf(owner), json={"tokens_per_day": 2000})
        assert r.status_code == 422
        assert r.json()["code"] == "above_platform_limit"
        cfg = ok(owner.get(url))
        assert cfg["effective"]["tokens_per_day"] == 1000
        cfg = configure(owner, p["id"], tokens_per_day=500)
        assert cfg["effective"]["tokens_per_day"] == 500
    finally:
        admin.put(f"{ADMIN}/settings/gateway.limits", headers=csrf(admin), json=original)


@pytest.mark.ac("US-1.7.1/2")
def test_max_tokens_clamped_or_rejected(admin: TestClient, client: TestClient, mock: str) -> None:
    p = project(admin, client)
    owner = login(client, p["owner"])
    prov = provider(admin, mock)
    m = model(admin, prov["id"], "mt", enabled_project_ids=[p["id"]])
    configure(owner, p["id"], max_tokens_per_call=5)
    r = chat(client, p["key"], m["name"], max_tokens=50)
    assert r.status_code == 200
    assert r.headers["x-forge-max-tokens-clamped"] == "50->5"
    assert mock_provider.RECORDS[-1]["body"]["max_tokens"] == 5
    chat(client, p["key"], m["name"])
    assert mock_provider.RECORDS[-1]["body"]["max_tokens"] == 5  # default to the ceiling
    configure(owner, p["id"], max_tokens_per_call=5, max_tokens_policy="reject")
    r = chat(client, p["key"], m["name"], max_tokens=50)
    assert r.status_code == 400
    assert r.json()["code"] == "max_tokens_exceeded"


# ---- response cache (US-1.8.1)
@pytest.mark.ac("US-1.8.1/1")
def test_exact_match_cache(admin: TestClient, client: TestClient, mock: str) -> None:
    p = project(admin, client)
    owner = login(client, p["owner"])
    prov = provider(admin, mock)
    m = model(
        admin,
        prov["id"],
        "cached",
        cost_input="1000",
        cost_output="2000",
        enabled_project_ids=[p["id"]],
    )
    first = chat(client, p["key"], m["name"], temperature=0)
    assert "x-forge-cache" not in first.headers  # off by default
    configure(owner, p["id"], cache_enabled=True, cache_ttl_s=60)
    assert chat(client, p["key"], m["name"], temperature=0).headers["x-forge-cache"] == "miss"
    hit = chat(client, p["key"], m["name"], temperature=0)
    assert hit.headers["x-forge-cache"] == "hit"
    assert hit.json()["choices"][0]["message"]["content"] == "pong from cached"
    assert "x-forge-cache" not in chat(client, p["key"], m["name"], temperature=0.7).headers
    assert mock_provider.ATTEMPTS["cached"] == 3
    rows = calls(client, p["id"])
    assert [bool(r["cache_hit"]) for r in rows] == [False, False, True, False]
    assert rows[0]["cost"] == Decimal("0.025")  # 11 * 1000/1M + 7 * 2000/1M
    assert rows[2]["cost"] == 0
    usage = ok(owner.get(f"/api/v1/projects/{p['id']}/gateway/usage"))
    assert usage["calls_today"] == 4


# ---- embeddings (US-1.6.2)
@pytest.mark.ac("US-1.6.2")
def test_embeddings_with_openai_sdk(admin: TestClient, client: TestClient, mock: str) -> None:
    p = project(admin, client)
    prov = provider(admin, mock)
    m = model(
        admin,
        prov["id"],
        "emb-16",
        model_type="embedding",
        embedding_dims=16,
        enabled_project_ids=[p["id"]],
    )
    api = sdk(client, p["key"])
    out = api.embeddings.create(model=m["name"], input=["a", "b"])  # SDK default: base64
    assert [len(d.embedding) for d in out.data] == [16, 16]
    assert abs(out.data[1].embedding[0] - 0.2) < 1e-6
    assert out.usage.prompt_tokens == 6
    floats = api.embeddings.create(model=m["name"], input="a", encoding_format="float")
    assert len(floats.data[0].embedding) == 16
    r = chat(client, p["key"], m["name"])
    assert r.status_code == 400
    assert r.json()["code"] == "wrong_model_type"


# ---- deletion guards (US-1.9.1)
@pytest.mark.ac("US-1.9.1")
def test_delete_blocked_while_referenced(admin: TestClient, mock: str) -> None:
    prov = provider(admin, mock)
    m = model(admin, prov["id"], "ref")
    name = f"ref-{uid()}"
    alias(admin, name, [m["id"]])
    r = admin.delete(f"{ADMIN}/models/{m['id']}", headers=csrf(admin))
    assert r.status_code == 409
    assert r.json()["code"] == "model_in_use"
    assert r.json()["detail"]["references"][0]["name"] == name
    r = admin.delete(f"{ADMIN}/providers/{prov['id']}", headers=csrf(admin))
    assert r.status_code == 409
    assert r.json()["code"] == "provider_in_use"
    ok(admin.delete(f"{ADMIN}/aliases/{name}", headers=csrf(admin)), 204)
    ok(admin.delete(f"{ADMIN}/models/{m['id']}", headers=csrf(admin)), 204)
    ok(admin.delete(f"{ADMIN}/providers/{prov['id']}", headers=csrf(admin)), 204)


# ---- project scope and isolation (ADR-002)
@pytest.mark.ac("US-1.5.1")
@pytest.mark.ac("US-1.5.3/1")
def test_project_models_are_isolated(admin: TestClient, client: TestClient, mock: str) -> None:
    pa, pb = project(admin, client), project(admin, client)
    alice, bob = login(client, pa["owner"]), login(client, pb["owner"])
    url_a, url_b = f"/api/v1/projects/{pa['id']}", f"/api/v1/projects/{pb['id']}"
    own = provider(alice, mock, url=url_a)
    m = model(alice, own["id"], "private", url=url_a)
    assert m["scope"] == "project"

    platform_prov = provider(admin, mock)
    r = alice.post(
        f"{url_a}/models", headers=csrf(alice), json=model_body(platform_prov["id"], "steal")
    )
    assert r.status_code == 422
    assert r.json()["code"] == "unknown_provider"
    assert [p["id"] for p in ok(alice.get(f"{url_a}/providers"))] == [own["id"]]
    assert ok(bob.get(f"{url_b}/providers")) == []
    assert m["id"] not in {x["id"] for x in ok(bob.get(f"{url_b}/models"))}
    assert bob.put(f"{url_a}/models/{m['id']}", headers=csrf(bob), json={}).status_code == 404

    assert ok(chat(client, pa["key"], m["name"]))["model"] == m["name"]
    r = chat(client, pb["key"], m["name"])
    assert r.status_code == 404
    assert r.json()["code"] == "model_not_found"

    # A project alias overrides the platform default of the same name.
    shared = model(admin, platform_prov["id"], "shared", all_projects=True)
    alias(admin, "fast", [shared["id"]])
    try:
        alias(alice, "fast", [m["id"]], url=url_a)
        assert ok(chat(client, pa["key"], "fast"))["model"] == m["name"]
        assert ok(chat(client, pb["key"], "fast"))["model"] == shared["name"]
        listed = {a["name"]: a for a in ok(alice.get(f"{url_a}/aliases"))}
        assert listed["fast"]["overrides_platform"] is True
        assert {"default-chat", "embedding", "rerank"} <= set(listed)
    finally:
        admin.delete(f"{ADMIN}/aliases/fast", headers=csrf(admin))


@pytest.mark.ac("US-1.6.1/1")
def test_v1_requires_a_project_key_with_llm_invoke(admin: TestClient, client: TestClient) -> None:
    p = project(admin, client)
    owner = login(client, p["owner"])
    body = {"model": "x", "messages": [{"role": "user", "content": "x"}]}
    anon = new_client(client).post("/v1/chat/completions", json=body)
    assert anon.status_code == 401
    assert anon.json()["error"]["code"] == "unauthenticated"
    r = owner.post("/v1/chat/completions", json=body, headers=csrf(owner))
    assert r.status_code == 401
    assert r.json()["code"] == "api_key_required"
    key = ok(
        owner.post(
            f"/api/v1/projects/{p['id']}/api-keys",
            headers=csrf(owner),
            json={"name": "read only", "scopes": ["run.read"]},
        ),
        201,
    )["token"]
    r = chat(client, key, "x")
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"
    r = chat(client, p["key"], "missing")
    assert r.status_code == 404
    assert r.json()["error"]["type"] == "model_not_found"


@pytest.mark.ac("US-1.5.3/3")
def test_registry_follows_changes_from_another_process(
    admin: TestClient, client: TestClient
) -> None:
    """A write made elsewhere (another API replica) reaches this process via NOTIFY."""
    from forge.core.db import admin_scope
    from forge.core.ids import uuid7

    app = client.app  # type: ignore[attr-defined]
    prov = provider(admin, "http://127.0.0.1:9/v1")
    m = model(admin, prov["id"], "remote")
    name = f"remote-{uid()}"

    async def write_elsewhere() -> None:
        async with admin_scope(app.state.sessions) as s:
            await s.execute(
                text(
                    "INSERT INTO model_alias (id, name, model_ids)"
                    " VALUES (:i, :n, ARRAY[:m]::uuid[])"
                ),
                {"i": uuid7(), "n": name, "m": m["id"]},
            )
            await s.execute(text("SELECT pg_notify('forge_config', 'gateway')"))

    async def seen() -> bool:
        import asyncio

        for _ in range(60):
            if name in app.state.services.gateway.registry.snapshot.platform_aliases:
                return True
            await asyncio.sleep(0.05)
        return False

    client.portal.call(write_elsewhere)  # type: ignore[attr-defined]
    assert client.portal.call(seen)  # type: ignore[attr-defined]


# ---- NFR: gateway overhead under 50 ms p95 (spec NFR, plan E1.7)
def test_gateway_overhead_p95(admin: TestClient, client: TestClient, mock: str) -> None:
    import statistics

    import httpx

    p = project(admin, client)
    prov = provider(admin, mock)
    m = model(admin, prov["id"], "bench", enabled_project_ids=[p["id"]])
    body = {"messages": [{"role": "user", "content": "ping"}], "max_tokens": 5}
    c = new_client(client, headers={"Authorization": f"Bearer {p['key']}"})

    def p95(samples: list[float]) -> float:
        return statistics.quantiles(samples, n=20)[18]

    direct, gateway = [], []
    with httpx.Client(base_url=mock) as raw:
        for _ in range(10):  # warm up both paths
            raw.post("/chat/completions", json=body | {"model": "bench"})
            c.post("/v1/chat/completions", json=body | {"model": m["name"]})
        for _ in range(100):
            t0 = time.perf_counter()
            raw.post("/chat/completions", json=body | {"model": "bench"})
            direct.append((time.perf_counter() - t0) * 1000)
            t0 = time.perf_counter()
            assert (
                c.post("/v1/chat/completions", json=body | {"model": m["name"]}).status_code == 200
            )
            gateway.append((time.perf_counter() - t0) * 1000)
    overhead = p95(gateway) - p95(direct)
    median = statistics.median(gateway) - statistics.median(direct)
    print(
        f"GATEWAY_OVERHEAD direct_p95={p95(direct):.1f}ms gateway_p95={p95(gateway):.1f}ms "
        f"overhead_p95={overhead:.1f}ms overhead_p50={median:.1f}ms"
    )
    assert overhead < 50
