"""Provider adapters (ADR-005). `LiteLLMAdapter` is the only code that imports LiteLLM.

LiteLLM is imported lazily (about 3 s and 290 MB, spike E0.5d), never at module import, and
always with the local cost map and telemetry off, so it works air-gapped. Retries and fallback
are the gateway's job: every call uses `num_retries=0`, and LiteLLM's mapped exceptions are
translated into `UpstreamError` kinds that drive the retry policy.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Literal, Protocol

from forge.gateway.registry import ModelConfig

ErrorKind = Literal[
    "rate_limit",
    "payment",
    "timeout",
    "connection",
    "server",
    "auth",
    "not_found",
    "context_window",
    "content_policy",
    "bad_request",
    "unsupported",
]
RETRYABLE: frozenset[str] = frozenset({"timeout", "connection", "server"})

# LiteLLM model prefix per Forge provider type and model use (spike E0.5d, finding 5).
_CHAT_PREFIX = {
    "vllm": "hosted_vllm",
    "llamacpp": "openai",
    "nim": "openai",
    "ollama": "ollama_chat",
    "openai": "openai",
    "azure": "azure",
    "anthropic": "anthropic",
    "bedrock": "bedrock",
    "gemini": "gemini",
    "vertex": "vertex_ai",
    "mistral": "mistral",
    "openai_compatible": "openai",
}
_EMBED_PREFIX = _CHAT_PREFIX | {"ollama": "ollama", "vllm": "hosted_vllm"}
_RERANK_PREFIX = {
    "vllm": "hosted_vllm",
    "llamacpp": "hosted_vllm",
    "openai_compatible": "hosted_vllm",
    "nim": "nvidia_nim",
}
_DEFAULT_BASE = {"nim": "https://integrate.api.nvidia.com/v1"}
# Keys a model's extra parameters may not override.
_RESERVED = frozenset(
    {"model", "messages", "input", "api_key", "api_base", "base_url", "stream", "num_retries"}
)


# Credentials that providers echo back in error messages (threat model TB3).
_SECRET_PATTERNS = re.compile(
    r"(?:sk-or-v1-|sk-ant-|sk-proj-|sk-|gsk_|csk-|nvapi-|fk_|xai-)[A-Za-z0-9_\-]{8,}"
    r"|AIza[0-9A-Za-z_\-]{30,}"
    r"|(?i:bearer)\s+[A-Za-z0-9._\-]{12,}"
)


def scrub(text: str, known: str | None = None) -> str:
    """Remove the provider's own key and anything that looks like an API key."""
    if known and len(known) >= 6:
        text = text.replace(known, "***")
    return _SECRET_PATTERNS.sub("***", text)


class UpstreamError(Exception):
    def __init__(self, kind: ErrorKind, message: str, status: int | None = None) -> None:
        message = scrub(message)[:2000]
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE


@dataclass(frozen=True)
class ProbeResult:
    latency_ms: int
    sample: str | None = None
    dimensions: int | None = None


class ProviderAdapter(Protocol):
    async def chat(self, model: ModelConfig, params: dict[str, Any]) -> dict[str, Any]: ...

    def chat_stream(
        self, model: ModelConfig, params: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]: ...

    async def embed(
        self, model: ModelConfig, inputs: list[str], params: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def rerank(
        self, model: ModelConfig, query: str, documents: list[str], top_n: int | None
    ) -> dict[str, Any]: ...

    def count_tokens(self, model: ModelConfig, messages: Any = None, text: str = "") -> int: ...

    async def probe(self, model: ModelConfig, timeout_s: float) -> ProbeResult: ...


_lock = threading.Lock()
_module: ModuleType | None = None


def load_litellm() -> ModuleType:
    """Import and configure LiteLLM once (thread-safe; call through `warm_up` at start)."""
    global _module
    with _lock:
        if _module is None:
            os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
            os.environ.setdefault("LITELLM_LOG", "ERROR")
            import litellm

            litellm.telemetry = False
            litellm.drop_params = True  # unsupported OpenAI params are dropped per provider
            litellm.suppress_debug_info = True
            litellm.turn_off_message_logging = True
            _module = litellm
        return _module


async def warm_up() -> None:
    await asyncio.to_thread(load_litellm)


def _classify(exc: Exception, model: ModelConfig | None = None) -> UpstreamError:
    err = _map(exc)
    if model is not None and model.provider.api_key:
        err.message = scrub(err.message, model.provider.api_key)
    return err


def _map(exc: Exception) -> UpstreamError:
    if isinstance(exc, UpstreamError):
        return exc
    if isinstance(exc, asyncio.TimeoutError | TimeoutError):
        return UpstreamError("timeout", "provider timed out")
    ll = load_litellm()
    e = ll.exceptions
    status = getattr(exc, "status_code", None)
    message = str(getattr(exc, "message", None) or exc)
    if status == 402:  # account without credit or billing: try the next model
        return UpstreamError("payment", message, status)
    if status == 410:  # model retired by the provider
        return UpstreamError("not_found", message, status)
    table: list[tuple[type[BaseException], ErrorKind]] = [
        (e.ContextWindowExceededError, "context_window"),
        (e.ContentPolicyViolationError, "content_policy"),
        (e.RateLimitError, "rate_limit"),
        (e.Timeout, "timeout"),
        (e.APIConnectionError, "connection"),
        (e.AuthenticationError, "auth"),
        (e.PermissionDeniedError, "auth"),
        (e.NotFoundError, "not_found"),
        (e.BadRequestError, "bad_request"),
        (e.UnprocessableEntityError, "bad_request"),
        (e.InternalServerError, "server"),
        (e.ServiceUnavailableError, "server"),
        (e.BadGatewayError, "server"),
    ]
    for cls, kind in table:
        if isinstance(exc, cls):
            return UpstreamError(kind, message, status)
    if isinstance(status, int):
        if status in (408,):
            return UpstreamError("timeout", message, status)
        if status == 429:
            return UpstreamError("rate_limit", message, status)
        if status >= 500:
            return UpstreamError("server", message, status)
        if status in (401, 403):
            return UpstreamError("auth", message, status)
        if 400 <= status < 500:
            return UpstreamError("bad_request", message, status)
    # Unmapped errors (DNS failure, reset connection inside the SDK) behave like connection
    # errors: retried, then the next model.
    return UpstreamError("connection", message, status)


def _clean(value: Any) -> Any:
    """Drop LiteLLM-specific fields and nulls from response objects."""
    if isinstance(value, dict):
        return {
            k: _clean(v)
            for k, v in value.items()
            if v is not None and k not in ("provider_specific_fields", "_hidden_params")
        }
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


class LiteLLMAdapter:
    def _kwargs(
        self, model: ModelConfig, use: Literal["chat", "embed", "rerank"]
    ) -> dict[str, Any]:
        p = model.provider
        prefixes = {"chat": _CHAT_PREFIX, "embed": _EMBED_PREFIX, "rerank": _RERANK_PREFIX}[use]
        prefix = prefixes.get(p.provider_type)
        if prefix is None:
            raise UpstreamError(
                "unsupported", f"{use} is not supported for provider type {p.provider_type}"
            )
        kw: dict[str, Any] = {
            "model": f"{prefix}/{model.model_id}",
            "timeout": model.timeout_s,
            "num_retries": 0,
        }
        base = p.base_url or _DEFAULT_BASE.get(p.provider_type)
        if base:
            kw["api_base"] = base
        cfg = p.config
        if cfg.get("headers"):
            kw["extra_headers"] = dict(cfg["headers"])
        if p.provider_type == "azure":
            kw["api_version"] = cfg.get("api_version")
        if p.provider_type == "bedrock":
            kw["aws_region_name"] = cfg.get("region")
            if p.api_key:
                creds = json.loads(p.api_key)
                kw["aws_access_key_id"] = creds.get("aws_access_key_id")
                kw["aws_secret_access_key"] = creds.get("aws_secret_access_key")
                if creds.get("aws_session_token"):
                    kw["aws_session_token"] = creds["aws_session_token"]
        elif p.provider_type == "vertex":
            kw["vertex_project"] = cfg.get("vertex_project")
            kw["vertex_location"] = cfg.get("vertex_location")
            if p.api_key:
                kw["vertex_credentials"] = p.api_key
        elif p.api_key:
            kw["api_key"] = p.api_key
        elif prefix in ("openai", "hosted_vllm"):
            # The OpenAI client refuses to start without a key; keyless local servers accept any.
            kw["api_key"] = "not-needed"
        kw.update({k: v for k, v in model.params.items() if k not in _RESERVED})
        return kw

    async def chat(self, model: ModelConfig, params: dict[str, Any]) -> dict[str, Any]:
        ll = load_litellm()
        try:
            resp = await ll.acompletion(**(self._kwargs(model, "chat") | params))
        except Exception as exc:
            raise _classify(exc, model) from exc
        return _clean(resp.model_dump())  # type: ignore[no-any-return]

    async def chat_stream(
        self, model: ModelConfig, params: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        ll = load_litellm()
        try:
            stream = await ll.acompletion(
                **(self._kwargs(model, "chat") | params),
                stream=True,
                stream_options={"include_usage": True},
            )
            async for chunk in stream:
                yield _clean(chunk.model_dump())
        except Exception as exc:
            raise _classify(exc, model) from exc

    async def embed(
        self, model: ModelConfig, inputs: list[str], params: dict[str, Any]
    ) -> dict[str, Any]:
        ll = load_litellm()
        try:
            resp = await ll.aembedding(**(self._kwargs(model, "embed") | params), input=inputs)
        except Exception as exc:
            raise _classify(exc, model) from exc
        return _clean(resp.model_dump())  # type: ignore[no-any-return]

    async def rerank(
        self, model: ModelConfig, query: str, documents: list[str], top_n: int | None
    ) -> dict[str, Any]:
        ll = load_litellm()
        kw = self._kwargs(model, "rerank")
        kw.pop("timeout", None)
        try:
            resp = await asyncio.wait_for(
                ll.arerank(**kw, query=query, documents=documents, top_n=top_n),
                model.timeout_s,
            )
        except Exception as exc:
            raise _classify(exc, model) from exc
        return _clean(resp.model_dump() if hasattr(resp, "model_dump") else dict(resp))  # type: ignore[no-any-return]

    def count_tokens(self, model: ModelConfig, messages: Any = None, text: str = "") -> int:
        """Tokenizer estimate (ADR-005: usage is not assumed to be present in streams)."""
        ll = load_litellm()
        try:
            if messages is not None:
                return int(ll.token_counter(model=model.model_id, messages=messages))
            return int(ll.token_counter(model=model.model_id, text=text))
        except Exception:
            raw = json.dumps(messages) if messages is not None else text
            return max(1, len(raw) // 4)

    async def probe(self, model: ModelConfig, timeout_s: float) -> ProbeResult:
        """Minimal request of the model's type (US-1.5.2)."""
        start = time.perf_counter()
        sample: str | None = None
        dims: int | None = None
        coro: Any
        if model.model_type in ("chat", "guard"):
            coro = self.chat(
                model, {"messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}
            )
        elif model.model_type == "embedding":
            coro = self.embed(model, ["ping"], {})
        elif model.model_type == "reranker":
            coro = self.rerank(model, "ping", ["pong"], 1)
        else:
            raise UpstreamError("unsupported", "Test Connection is not available for speech models")
        try:
            result = await asyncio.wait_for(coro, timeout_s)
        except TimeoutError as exc:
            raise UpstreamError("timeout", f"no answer within {timeout_s:g} s") from exc
        if model.model_type in ("chat", "guard"):
            choices = result.get("choices") or [{}]
            message = choices[0].get("message") or {}
            sample = str(message.get("content") or message.get("reasoning_content") or "")[:200]
        elif model.model_type == "embedding":
            data = result.get("data") or []
            dims = len(data[0]["embedding"]) if data else None
        else:
            sample = json.dumps(result.get("results", []))[:200]
        return ProbeResult(int((time.perf_counter() - start) * 1000), sample, dims)
