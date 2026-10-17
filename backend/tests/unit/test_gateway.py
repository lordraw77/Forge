"""Gateway pipeline rules that need no database: usage estimation, stream relay, limits."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import pytest

from forge.admin.service import GatewayLimits
from forge.core.errors import ProblemError
from forge.gateway.adapter import ProbeResult, UpstreamError
from forge.gateway.limits import Reservation
from forge.gateway.models import GatewayProjectConfig, LlmCall
from forge.gateway.pipeline import Caller, GatewayService, _needed_capabilities
from forge.gateway.registry import ModelConfig, Provider, Registry, Snapshot, effective_limits
from forge.gateway.schemas import check_base_url

PROJECT = uuid.UUID(int=1)


def make_model(name: str = "m", **extra: Any) -> ModelConfig:
    provider = Provider(
        uuid.uuid4(), None, "p", "openai_compatible", "http://x/v1", None, {}, "active"
    )
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "project_id": None,
        "name": name,
        "display_name": name,
        "model_type": "chat",
        "model_id": name,
        "params": {},
        "context_window": None,
        "max_output_tokens": None,
        "embedding_dims": None,
        "cost_input": Decimal(0),
        "cost_output": Decimal(0),
        "capabilities": frozenset({"streaming"}),
        "timeout_s": 5,
        "max_retries": 0,
        "rpm_limit": None,
        "all_projects": True,
        "enabled_projects": frozenset(),
        "status": "active",
        "provider": provider,
    } | extra
    return ModelConfig(**values)


class FakeAdapter:
    def __init__(self, chunks: list[dict[str, Any]], fail_after: int | None = None) -> None:
        self.chunks = chunks
        self.fail_after = fail_after

    async def chat(self, model: ModelConfig, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "four words here ok"}}
            ]
        }

    async def chat_stream(
        self, model: ModelConfig, params: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        for i, chunk in enumerate(self.chunks):
            if self.fail_after is not None and i == self.fail_after:
                raise UpstreamError("server", "connection dropped")
            yield chunk

    async def embed(self, model: ModelConfig, inputs: list[str], params: dict[str, Any]) -> Any:
        raise NotImplementedError

    async def rerank(self, *args: Any) -> Any:
        raise NotImplementedError

    def count_tokens(self, model: ModelConfig, messages: Any = None, text: str = "") -> int:
        return len(json.dumps(messages).split()) if messages is not None else len(text.split())

    async def probe(self, model: ModelConfig, timeout_s: float) -> ProbeResult:
        return ProbeResult(1)


class FakeLimiter:
    def __init__(self) -> None:
        self.reconciled: list[int] = []

    async def admit(self, *args: Any, **kwargs: Any) -> Reservation:
        return Reservation(("a", "b"), kwargs["reserve"], "20261017")

    async def reconcile(self, r: Reservation, actual: int) -> None:
        self.reconciled.append(actual)

    async def model_slot(self, *args: Any) -> bool:
        return True


class FakeCache:
    async def get(self, key: str) -> None:
        return None


class Store:
    def get(self, cls: type) -> Any:
        return GatewayLimits()


class Recording(GatewayService):
    rows: list[LlmCall]

    async def _insert(self, row: LlmCall) -> None:
        self.rows.append(row)


def service(model: ModelConfig, adapter: FakeAdapter) -> tuple[Recording, FakeLimiter]:
    registry = Registry(None, None, Store())  # type: ignore[arg-type]
    registry._snapshot = Snapshot(models={model.id: model}, platform_models={model.name: model})
    limiter = FakeLimiter()
    svc = Recording(None, registry, adapter, limiter, FakeCache())  # type: ignore[arg-type]
    svc.rows = []
    return svc, limiter


def delta(text: str | None = None, finish: str | None = None) -> dict[str, Any]:
    choice: dict[str, Any] = {"index": 0, "delta": {"content": text} if text else {}}
    if finish:
        choice["finish_reason"] = finish
    return {"choices": [choice]}


async def collect(stream: AsyncIterator[bytes]) -> list[Any]:
    out = []
    async for raw in stream:
        payload = raw.decode().removeprefix("data: ").strip()
        out.append(payload if payload == "[DONE]" else json.loads(payload))
    return out


async def test_stream_without_usage_is_estimated_and_attached_to_final_chunk() -> None:
    model = make_model()
    svc, limiter = service(
        model, FakeAdapter([delta("one two "), delta("three"), delta(finish="stop")])
    )
    params = {"messages": [{"role": "user", "content": "hi there"}], "stream": True}
    result = await svc.chat(Caller(PROJECT, None), "m", params)
    assert result.stream is not None
    events = await collect(result.stream)
    await svc.drain()
    assert events[-1] == "[DONE]"
    finish = events[-2]
    assert finish["choices"][0]["finish_reason"] == "stop"
    assert finish["usage"]["completion_tokens"] == 3  # "one two" + "three"
    assert len({e["id"] for e in events[:-1]}) == 1
    assert {e["model"] for e in events[:-1]} == {"m"}
    assert svc.rows[0].usage_source == "estimated"
    assert limiter.reconciled == [finish["usage"]["total_tokens"]]


async def test_mid_stream_error_is_reported_in_band() -> None:
    model = make_model()
    svc, limiter = service(model, FakeAdapter([delta("a"), delta("b"), delta("c")], fail_after=2))
    params = {"messages": [{"role": "user", "content": "x"}], "stream": True}
    result = await svc.chat(Caller(PROJECT, None), "m", params)
    assert result.stream is not None
    events = await collect(result.stream)
    await svc.drain()
    assert events[-2]["error"]["type"] == "upstream_stream_error"
    assert events[-1] == "[DONE]"
    assert svc.rows[0].status == "error"
    assert limiter.reconciled  # the reservation is always settled


async def test_non_stream_without_usage_is_estimated() -> None:
    svc, _ = service(make_model(), FakeAdapter([]))
    result = await svc.chat(
        Caller(PROJECT, None), "m", {"messages": [{"role": "user", "content": "x"}]}
    )
    await svc.drain()
    assert result.body is not None
    assert result.body["usage"]["completion_tokens"] == 4
    assert svc.rows[0].usage_source == "estimated"


async def test_unknown_and_disabled_models() -> None:
    svc, _ = service(make_model(all_projects=False), FakeAdapter([]))
    with pytest.raises(ProblemError) as err:
        await svc.chat(Caller(PROJECT, None), "nope", {"messages": []})
    assert err.value.code == "model_not_found"
    with pytest.raises(ProblemError) as err:
        await svc.chat(Caller(PROJECT, None), "m", {"messages": []})
    assert err.value.code == "model_not_enabled"
    await svc.drain()
    assert [r.error_code for r in svc.rows] == ["model_not_found", "model_not_enabled"]


def test_effective_limits_respect_platform_ceilings() -> None:
    platform = GatewayLimits(tokens_per_day=1000, requests_per_minute=0, max_tokens_per_call=512)
    assert effective_limits(platform, None).tokens_per_day == 1000
    cfg = GatewayProjectConfig(
        project_id=PROJECT,
        tokens_per_day=200,
        requests_per_minute=30,
        max_tokens_per_call=0,
        cache_enabled=True,
        cache_ttl_s=10**6,
        cache_nonzero_temperature=False,
    )
    eff = effective_limits(platform, cfg)
    assert eff.tokens_per_day == 200
    assert eff.requests_per_minute == 30  # platform unlimited: the project decides
    assert eff.max_tokens_per_call == 512  # 0 cannot lift a platform ceiling
    assert eff.cache_ttl_s == platform.cache_ttl_max_s


def test_needed_capabilities() -> None:
    image = {"type": "image_url", "image_url": {"url": "data:,"}}
    params = {
        "messages": [{"role": "user", "content": [image]}],
        "tools": [{"type": "function"}],
        "response_format": {"type": "json_schema"},
    }
    assert _needed_capabilities(params) == {"vision", "tools", "json"}
    assert _needed_capabilities({"messages": [{"role": "user", "content": "x"}]}) == set()


@pytest.mark.parametrize(
    "url",
    [
        "ftp://x",
        "http://169.254.169.254/latest",
        "http://metadata.google.internal",
        "http://[fe80::1]/v1",
        "http://0.0.0.0:80",
    ],
)
def test_base_url_rejects_metadata_and_link_local(url: str) -> None:
    with pytest.raises(ValueError, match=r"."):
        check_base_url(url)


def test_base_url_allows_private_endpoints() -> None:
    assert check_base_url("http://192.168.0.140:11434/") == "http://192.168.0.140:11434"
    assert check_base_url("https://vllm.internal:8000/v1") == "https://vllm.internal:8000/v1"


def test_adapter_merges_model_defaults_under_request_params() -> None:
    from forge.gateway.adapter import LiteLLMAdapter

    model = make_model(params={"reasoning_effort": "low", "api_key": "ignored", "top_k": 5})
    kw = LiteLLMAdapter()._kwargs(model, "chat")
    assert kw["model"] == "openai/m"
    assert kw["api_base"] == "http://x/v1"
    assert kw["api_key"] == "not-needed"  # reserved keys cannot come from model params
    assert kw["num_retries"] == 0
    merged = kw | {"reasoning_effort": "high"}
    assert merged["reasoning_effort"] == "high"
    assert merged["top_k"] == 5


def test_provider_errors_are_scrubbed() -> None:
    from forge.gateway.adapter import UpstreamError, scrub

    text = (
        "invalid key sk-proj-abcdef1234567890 and Bearer abcdefghijklmnop0123 for AIza" + "x" * 35
    )
    assert "abcdef1234567890" not in scrub(text)
    assert "abcdefghijklmnop0123" not in scrub(text)
    assert "AIzax" not in scrub(text)
    assert scrub("key my-own-key-value rejected", "my-own-key-value") == "key *** rejected"
    assert "gsk_" not in UpstreamError("auth", "bad gsk_0123456789abcdef").message


def test_plain_http_needs_explicit_flag() -> None:
    from pydantic import ValidationError

    from forge.gateway.schemas import ProviderIn

    with pytest.raises(ValidationError, match="allow_http"):
        ProviderIn(name="vllm", provider_type="vllm", base_url="http://vllm:8000/v1")
    ok = ProviderIn(
        name="vllm",
        provider_type="vllm",
        base_url="http://vllm:8000/v1",
        config={"allow_http": True},
    )
    assert ok.config.allow_http is True
    ProviderIn(name="vllm", provider_type="vllm", base_url="https://vllm:8000/v1")
