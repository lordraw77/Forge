"""The gateway call pipeline (ADR-005), shared by `/v1/*` and internal callers.

resolve alias → check enablement and capabilities → max_tokens policy → quota reserve →
[input guardrails, E2.11] → cache lookup → call with timeout, retries and fallback →
[output guardrails, E2.11] → reconcile quota → record `llm_call`.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import random
import struct
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.core.db import project_scope
from forge.core.errors import ProblemError
from forge.core.ids import uuid7
from forge.core.logging import get_logger
from forge.gateway.adapter import ProviderAdapter, UpstreamError
from forge.gateway.limits import Limiter, Reservation, ResponseCache
from forge.gateway.models import LlmCall
from forge.gateway.registry import ModelConfig, Registry, Resolution
from forge.gateway.schemas import EffectiveLimits, TestResult, UsageOut
from forge.observability.service import record_gateway_call

log = get_logger(__name__)

CHAT_KINDS = frozenset({"chat", "guard"})
EMBED_KINDS = frozenset({"embedding"})
RERANK_KINDS = frozenset({"reranker"})
DEFAULT_OUTPUT_RESERVE = 1024
TEST_TIMEOUT_CAP_S = 30.0
MAX_EMBED_INPUTS = 2048

# Upstream error kind → (HTTP status, code, title) once every candidate has failed.
_FAILURES: dict[str, tuple[int, str, str]] = {
    "rate_limit": (429, "upstream_rate_limited", "The model provider is rate limiting"),
    "timeout": (504, "upstream_timeout", "The model provider timed out"),
    "connection": (502, "upstream_unavailable", "The model provider is unreachable"),
    "server": (502, "upstream_unavailable", "The model provider returned an error"),
    "auth": (502, "upstream_auth_failed", "The model provider rejected the credentials"),
    "payment": (502, "upstream_payment_required", "The provider account needs billing"),
    "not_found": (502, "upstream_model_not_found", "The provider does not know this model"),
    "context_window": (400, "context_length_exceeded", "The prompt exceeds the context window"),
    "content_policy": (400, "content_policy_violation", "The provider refused the content"),
    "bad_request": (400, "upstream_bad_request", "The provider rejected the request"),
    "unsupported": (400, "capability_not_supported", "Not supported by this provider type"),
}
# Errors that are the request's fault: neither retried nor sent to the next model.
_FINAL = frozenset({"bad_request", "context_window", "content_policy", "unsupported"})


@dataclass(frozen=True)
class Caller:
    project_id: uuid.UUID
    user_id: uuid.UUID | None
    api_key_id: uuid.UUID | None = None
    trace_id: str | None = None


@dataclass(frozen=True)
class CallInfo:
    """The recorded `llm_call` of a non-streaming chat, for internal callers' traces."""

    call_id: uuid.UUID
    model_name: str | None
    tokens_in: int
    tokens_out: int
    cost: Decimal
    latency_ms: int
    cache_hit: bool


@dataclass
class GatewayResponse:
    headers: dict[str, str]
    body: dict[str, Any] | None = None
    stream: AsyncIterator[bytes] | None = None
    info: CallInfo | None = None
    # Streaming: resolves with the recorded call once the stream has ended (accounting for
    # internal callers such as agents, E1.20).
    done: asyncio.Future[CallInfo] | None = None


@dataclass
class _Call:
    """Bookkeeping for one request, written to `llm_call` when it ends."""

    caller: Caller
    endpoint: str
    requested: str
    stream: bool = False
    started: float = field(default_factory=time.perf_counter)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    model: ModelConfig | None = None
    ttft_ms: int | None = None

    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self.started) * 1000)


def _needed_capabilities(params: dict[str, Any]) -> set[str]:
    needs: set[str] = set()
    if params.get("tools") or params.get("tool_choice") or params.get("functions"):
        needs.add("tools")
    fmt = params.get("response_format")
    if isinstance(fmt, dict) and fmt.get("type") in ("json_object", "json_schema"):
        needs.add("json")
    for message in params.get("messages", []):
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list) and any(
            isinstance(p, dict) and p.get("type") in ("image_url", "input_image") for p in content
        ):
            needs.add("vision")
    return needs


def _sse(payload: dict[str, Any]) -> bytes:
    return b"data: " + json.dumps(payload, separators=(",", ":")).encode() + b"\n\n"


def _float32_base64(vector: list[float]) -> str:
    return base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode()


class GatewayService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        registry: Registry,
        adapter: ProviderAdapter,
        limiter: Limiter,
        cache: ResponseCache,
        *,
        backoff_base_s: float = 0.5,
        backoff_cap_s: float = 8.0,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._sessions = sessions
        self.registry = registry
        self._adapter = adapter
        self._limiter = limiter
        self._cache = cache
        self._backoff_base = backoff_base_s
        self._backoff_cap = backoff_cap_s
        self._sleep = sleep
        self._background: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------ chat
    async def chat(self, caller: Caller, requested: str, params: dict[str, Any]) -> GatewayResponse:
        stream = bool(params.pop("stream", False))
        client_usage = bool((params.pop("stream_options", None) or {}).get("include_usage"))
        call = _Call(caller, "chat", requested, stream=stream)
        try:
            resolution = self.registry.resolve(caller.project_id, requested, CHAT_KINDS)
            chain = self._capable(resolution, params)
            limits = self.registry.limits(caller.project_id)
            headers = self._apply_max_tokens(params, limits, chain[0])
            prompt_tokens = self._adapter.count_tokens(chain[0], messages=params["messages"])
            out_reserve = (
                params.get("max_completion_tokens")
                or params.get("max_tokens")
                or chain[0].max_output_tokens
                or DEFAULT_OUTPUT_RESERVE
            )
            reservation = await self._admit(caller, limits, prompt_tokens + int(out_reserve))
        except ProblemError as exc:
            self._record_failure(call, exc)
            raise
        cache_key = self._cache_key(caller, requested, params, limits)
        if cache_key and (cached := await self._cache.get(cache_key)):
            await self._limiter.reconcile(reservation, 0)
            model = self.registry.snapshot.models.get(uuid.UUID(cached["forge_model_id"]))
            call.model = model
            body = cached["response"]
            headers |= {"x-forge-cache": "hit", "x-forge-model": body.get("model", "")}
            info = self._record(
                call, "ok", 200, tokens=(0, 0), usage_source="cache", cache_hit=True
            )
            if stream:
                return GatewayResponse(headers, stream=self._replay(body, client_usage), info=info)
            return GatewayResponse(headers, body=body, info=info)
        if cache_key:
            headers["x-forge-cache"] = "miss"

        if stream:
            return await self._chat_stream(call, chain, params, reservation, headers, client_usage)

        try:
            model, response = await self._with_fallback(
                call, chain, lambda m: self._adapter.chat(m, self._model_params(m, params))
            )
        except ProblemError as exc:
            await self._limiter.reconcile(reservation, 0)
            self._record_failure(call, exc)
            raise
        usage = response.get("usage") or {}
        source = "provider"
        if not usage.get("prompt_tokens") and not usage.get("completion_tokens"):
            text = "".join(
                str((c.get("message") or {}).get("content") or "") for c in response["choices"]
            )
            usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": self._adapter.count_tokens(model, text=text),
            }
            source = "estimated"
        tokens = (int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0))
        body = self._chat_body(response, model, tokens)
        await self._limiter.reconcile(reservation, sum(tokens))
        info = self._record(call, "ok", 200, tokens=tokens, usage_source=source)
        if cache_key:
            await self._cache.put(
                cache_key, {"forge_model_id": str(model.id), "response": body}, limits.cache_ttl_s
            )
        headers["x-forge-model"] = model.name
        return GatewayResponse(headers, body=body, info=info)

    def _capable(self, resolution: Resolution, params: dict[str, Any]) -> list[ModelConfig]:
        needs = _needed_capabilities(params)
        chain = [m for m in resolution.chain if needs <= m.capabilities]
        if not chain:
            missing = ", ".join(sorted(needs - resolution.chain[0].capabilities))
            raise ProblemError(
                400,
                "capability_not_supported",
                "Capability not supported by the model",
                f"'{resolution.requested}' does not declare: {missing}",
            )
        return chain

    @staticmethod
    def _apply_max_tokens(
        params: dict[str, Any], limits: EffectiveLimits, model: ModelConfig
    ) -> dict[str, str]:
        """Per-call ceiling (US-1.7.1 #2): clamp or reject, reported in a response header."""
        headers: dict[str, str] = {}
        field_name = "max_completion_tokens" if "max_completion_tokens" in params else "max_tokens"
        asked = params.get(field_name)
        cap = limits.max_tokens_per_call
        if cap and asked and asked > cap:
            if limits.max_tokens_policy == "reject":
                raise ProblemError(
                    400,
                    "max_tokens_exceeded",
                    "max_tokens above the project limit",
                    f"max_tokens {asked} exceeds the limit of {cap}",
                )
            params[field_name] = cap
            headers["x-forge-max-tokens-clamped"] = f"{asked}->{cap}"
        elif cap and not asked:
            params[field_name] = cap
        if params.get(field_name) and model.max_output_tokens:
            params[field_name] = min(params[field_name], model.max_output_tokens)
        if params.get(field_name):
            headers["x-forge-max-tokens"] = str(params[field_name])
        return headers

    async def _admit(self, caller: Caller, limits: EffectiveLimits, reserve: int) -> Reservation:
        return await self._limiter.admit(
            caller.project_id,
            caller.user_id,
            tokens_per_day=limits.tokens_per_day,
            user_tokens_per_day=limits.user_tokens_per_day,
            requests_per_minute=limits.requests_per_minute,
            user_requests_per_minute=limits.user_requests_per_minute,
            reserve=reserve,
        )

    def _cache_key(
        self, caller: Caller, requested: str, params: dict[str, Any], limits: EffectiveLimits
    ) -> str | None:
        if not limits.cache_enabled:
            return None
        temperature = params.get("temperature")
        deterministic = temperature is not None and float(temperature) == 0.0
        if not deterministic and not limits.cache_nonzero_temperature:
            return None
        return self._cache.key(caller.project_id, requested, params)

    @staticmethod
    def _model_params(model: ModelConfig, params: dict[str, Any]) -> dict[str, Any]:
        out = dict(params)
        out.pop("user", None)  # end-user ids stay in Forge, not with the provider
        return out

    async def _with_fallback(
        self, call: _Call, chain: list[ModelConfig], invoke: Callable[[ModelConfig], Any]
    ) -> tuple[ModelConfig, Any]:
        """Retries with exponential backoff per model, then the next model (US-1.6.3)."""
        last: UpstreamError | None = None
        for model in chain:
            if not await self._limiter.model_slot(model.id, model.rpm_limit):
                call.attempts.append({"model": model.name, "outcome": "skipped_rpm_limit"})
                last = UpstreamError("rate_limit", f"model '{model.name}' rpm limit reached")
                continue
            for attempt in range(model.max_retries + 1):
                t0 = time.perf_counter()
                try:
                    result = await asyncio.wait_for(invoke(model), model.timeout_s + 5)
                except (UpstreamError, TimeoutError) as raw:
                    err = (
                        raw
                        if isinstance(raw, UpstreamError)
                        else UpstreamError("timeout", "provider timed out")
                    )
                    last = err
                    call.attempts.append(
                        {
                            "model": model.name,
                            "attempt": attempt + 1,
                            "outcome": "error",
                            "error": err.kind,
                            "status": err.status,
                            "message": err.message[:300],
                            "latency_ms": int((time.perf_counter() - t0) * 1000),
                        }
                    )
                    log.warning(
                        "gateway.attempt_failed",
                        model=model.name,
                        attempt=attempt + 1,
                        kind=err.kind,
                        status=err.status,
                    )
                    if err.kind in _FINAL:
                        call.model = model
                        raise self._failure(err) from raw
                    if err.retryable and attempt < model.max_retries:
                        delay = min(self._backoff_cap, self._backoff_base * 2**attempt)
                        await self._sleep(random.uniform(delay / 2, delay))  # noqa: S311
                        continue
                    break  # rate limit, auth, not found or retries exhausted: next model
                call.attempts.append(
                    {
                        "model": model.name,
                        "attempt": attempt + 1,
                        "outcome": "ok",
                        "latency_ms": int((time.perf_counter() - t0) * 1000),
                    }
                )
                call.model = model
                return model, result
        assert last is not None
        raise self._failure(last)

    @staticmethod
    def _failure(err: UpstreamError) -> ProblemError:
        status, code, title = _FAILURES[err.kind]
        headers = {"Retry-After": "5"} if status == 429 else None
        return ProblemError(status, code, title, err.message, headers=headers)

    @staticmethod
    def _chat_body(
        response: dict[str, Any], model: ModelConfig, tokens: tuple[int, int]
    ) -> dict[str, Any]:
        return {
            "id": response.get("id") or f"chatcmpl-{uuid7().hex}",
            "object": "chat.completion",
            "created": response.get("created") or int(time.time()),
            "model": model.name,
            "choices": response["choices"],
            "usage": {
                "prompt_tokens": tokens[0],
                "completion_tokens": tokens[1],
                "total_tokens": sum(tokens),
            },
        }

    # ---------------------------------------------------------------- stream
    async def _chat_stream(
        self,
        call: _Call,
        chain: list[ModelConfig],
        params: dict[str, Any],
        reservation: Reservation,
        headers: dict[str, str],
        client_usage: bool,
    ) -> GatewayResponse:
        prompt_estimate = self._adapter.count_tokens(chain[0], messages=params["messages"])

        async def open_stream(model: ModelConfig) -> tuple[Any, dict[str, Any] | None]:
            model_params = self._model_params(model, params)
            if "streaming" not in model.capabilities:
                return None, await self._adapter.chat(model, model_params)  # simulated stream
            agen = self._adapter.chat_stream(model, model_params)
            try:
                first = await agen.__anext__()  # fallback is possible until the first chunk
            except StopAsyncIteration:
                first = {}
            return agen, first

        try:
            model, (agen, first) = await self._with_fallback(call, chain, open_stream)
        except ProblemError as exc:
            await self._limiter.reconcile(reservation, 0)
            self._record_failure(call, exc)
            raise
        call.ttft_ms = call.elapsed_ms()
        headers["x-forge-model"] = model.name
        done: asyncio.Future[CallInfo] = asyncio.get_running_loop().create_future()
        return GatewayResponse(
            headers,
            stream=self._relay(
                call, model, agen, first, reservation, prompt_estimate, client_usage, done
            ),
            done=done,
        )

    async def _relay(
        self,
        call: _Call,
        model: ModelConfig,
        agen: Any,
        first: dict[str, Any] | None,
        reservation: Reservation,
        prompt_estimate: int,
        client_usage: bool,
        done: asyncio.Future[CallInfo] | None = None,
    ) -> AsyncIterator[bytes]:
        """Re-emit chunks under one id and Forge's model name. The chunk carrying
        `finish_reason` is held back so the final usage can be attached to it (US-1.6.1 #2)."""
        stream_id = f"chatcmpl-{uuid7().hex}"
        created = int(time.time())
        usage: dict[str, Any] | None = None
        text_parts: list[str] = []
        held: dict[str, Any] | None = None
        status, error_code = "ok", None

        def chunk(choices: list[dict[str, Any]], extra: dict[str, Any] | None = None) -> bytes:
            body = {
                "id": stream_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model.name,
                "choices": choices,
            }
            return _sse(body | (extra or {}))

        def absorb(raw: dict[str, Any]) -> list[dict[str, Any]]:
            nonlocal usage
            if raw.get("usage"):
                usage = raw["usage"]
            out = []
            for c in raw.get("choices") or []:
                delta = c.get("delta") or {}
                if delta or c.get("finish_reason"):
                    if delta.get("content"):
                        text_parts.append(str(delta["content"]))
                    out.append(c)
            return out

        try:
            if agen is None:  # model without streaming: one chunk from a normal completion
                assert first is not None
                usage = first.get("usage")
                for c in first.get("choices") or []:
                    message = c.get("message") or {}
                    text_parts.append(str(message.get("content") or ""))
                    held = {
                        "index": c.get("index", 0),
                        "delta": message,
                        "finish_reason": c.get("finish_reason") or "stop",
                    }
            else:
                pending = [first] if first else []
                while True:
                    if pending:
                        raw = pending.pop()
                    else:
                        try:
                            raw = await agen.__anext__()
                        except StopAsyncIteration:
                            break
                    for c in absorb(raw):
                        if c.get("finish_reason"):
                            if held is not None:
                                yield chunk([held])
                            held = c
                        else:
                            yield chunk([c])
        except UpstreamError as err:
            status, error_code = "error", "upstream_stream_error"
            call.attempts.append(
                {"model": model.name, "outcome": "stream_error", "error": err.kind}
            )
            yield _sse({"error": {"message": err.message, "type": "upstream_stream_error"}})
        except (asyncio.CancelledError, GeneratorExit):
            status, error_code = "cancelled", "client_disconnected"
            raise
        finally:
            source = "provider"
            if usage and (usage.get("prompt_tokens") or usage.get("completion_tokens")):
                tokens = (
                    int(usage.get("prompt_tokens") or 0),
                    int(usage.get("completion_tokens") or 0),
                )
            else:
                tokens = (
                    prompt_estimate,
                    self._adapter.count_tokens(model, text="".join(text_parts)),
                )
                source = "estimated"
            self._spawn(self._limiter.reconcile(reservation, sum(tokens)))
            recorded = self._record(
                call,
                status,
                200,
                tokens=tokens,
                usage_source=source,
                error_code=error_code,
            )
            if done is not None and not done.done():
                done.set_result(recorded)
        final_usage = {
            "prompt_tokens": tokens[0],
            "completion_tokens": tokens[1],
            "total_tokens": sum(tokens),
        }
        if status == "ok":
            if held is not None:
                yield chunk([held], {"usage": final_usage})
            if client_usage:
                yield chunk([], {"usage": final_usage})
        yield b"data: [DONE]\n\n"

    async def _replay(self, body: dict[str, Any], client_usage: bool) -> AsyncIterator[bytes]:
        base = {
            "id": body["id"],
            "object": "chat.completion.chunk",
            "created": body["created"],
            "model": body["model"],
        }
        for c in body["choices"]:
            message = c.get("message") or {}
            choice = {
                "index": c.get("index", 0),
                "delta": message,
                "finish_reason": c.get("finish_reason"),
            }
            yield _sse(base | {"choices": [choice], "usage": body["usage"]})
        if client_usage:
            yield _sse(base | {"choices": [], "usage": body["usage"]})
        yield b"data: [DONE]\n\n"

    # ------------------------------------------------------------ embeddings
    async def embeddings(
        self, caller: Caller, requested: str, inputs: list[str], params: dict[str, Any]
    ) -> GatewayResponse:
        encoding = params.pop("encoding_format", None) or "float"
        call = _Call(caller, "embeddings", requested)
        try:
            if not inputs or len(inputs) > MAX_EMBED_INPUTS:
                raise ProblemError(
                    400, "invalid_input", f"Give 1 to {MAX_EMBED_INPUTS} input strings"
                )
            resolution = self.registry.resolve(caller.project_id, requested, EMBED_KINDS)
            chain = list(resolution.chain)
            estimate = sum(self._adapter.count_tokens(chain[0], text=t) for t in inputs)
            reservation = await self._admit(
                caller, self.registry.limits(caller.project_id), estimate
            )
        except ProblemError as exc:
            self._record_failure(call, exc)
            raise
        try:
            model, response = await self._with_fallback(
                call, chain, lambda m: self._adapter.embed(m, inputs, self._model_params(m, params))
            )
        except ProblemError as exc:
            await self._limiter.reconcile(reservation, 0)
            self._record_failure(call, exc)
            raise
        prompt_tokens = int((response.get("usage") or {}).get("prompt_tokens") or 0)
        source = "provider" if prompt_tokens else "estimated"
        prompt_tokens = prompt_tokens or estimate
        data = [
            {
                "object": "embedding",
                "index": d.get("index", i),
                "embedding": (
                    _float32_base64(d["embedding"]) if encoding == "base64" else d["embedding"]
                ),
            }
            for i, d in enumerate(response.get("data") or [])
        ]
        await self._limiter.reconcile(reservation, prompt_tokens)
        info = self._record(call, "ok", 200, tokens=(prompt_tokens, 0), usage_source=source)
        body = {
            "object": "list",
            "data": data,
            "model": model.name,
            "usage": {"prompt_tokens": prompt_tokens, "total_tokens": prompt_tokens},
        }
        return GatewayResponse({"x-forge-model": model.name}, body=body, info=info)

    # ---------------------------------------------------------------- rerank
    async def rerank(
        self,
        caller: Caller,
        requested: str,
        query: str,
        documents: list[str],
        top_n: int | None = None,
    ) -> dict[str, Any]:
        """Internal, for the RAG retriever (E1.16).

        Returns `{"model", "results": [{"index", "relevance_score"}], "info": CallInfo}`.
        """
        call = _Call(caller, "rerank", requested)
        try:
            resolution = self.registry.resolve(caller.project_id, requested, RERANK_KINDS)
            chain = list(resolution.chain)
            estimate = self._adapter.count_tokens(chain[0], text=query + " ".join(documents))
            reservation = await self._admit(
                caller, self.registry.limits(caller.project_id), estimate
            )
            model, response = await self._with_fallback(
                call, chain, lambda m: self._adapter.rerank(m, query, documents, top_n)
            )
        except ProblemError as exc:
            self._record_failure(call, exc)
            raise
        await self._limiter.reconcile(reservation, estimate)
        info = self._record(call, "ok", 200, tokens=(estimate, 0), usage_source="estimated")
        return {"model": model.name, "results": response.get("results", []), "info": info}

    # ------------------------------------------------------- test connection
    async def test_connection(self, model: ModelConfig) -> TestResult:
        """Minimal request of the model's type; timeout = model timeout, capped at 30 s."""
        timeout = min(float(model.timeout_s), TEST_TIMEOUT_CAP_S)
        start = time.perf_counter()
        try:
            probe = await self._adapter.probe(model, timeout)
        except UpstreamError as err:
            return TestResult(
                ok=False,
                latency_ms=int((time.perf_counter() - start) * 1000),
                detail=err.message or err.kind,
                error_code=_FAILURES[err.kind][1],
            )
        except Exception as exc:  # misconfiguration caught by the SDK before any request
            return TestResult(
                ok=False,
                latency_ms=int((time.perf_counter() - start) * 1000),
                detail=str(exc)[:2000],
                error_code="invalid_configuration",
            )
        detail = "Connection successful"
        if (
            model.model_type == "embedding"
            and probe.dimensions
            and model.embedding_dims
            and probe.dimensions != model.embedding_dims
        ):
            detail = (
                f"Connected, but the model returned {probe.dimensions} dimensions "
                f"instead of the configured {model.embedding_dims}"
            )
            return TestResult(
                ok=False,
                latency_ms=probe.latency_ms,
                detail=detail,
                error_code="dimension_mismatch",
                dimensions=probe.dimensions,
            )
        return TestResult(
            ok=True,
            latency_ms=probe.latency_ms,
            detail=detail,
            sample=probe.sample,
            dimensions=probe.dimensions,
        )

    # ------------------------------------------------------------- telemetry
    async def usage(self, project_id: uuid.UUID, user_id: uuid.UUID | None) -> UsageOut:
        day, tokens, requests = await self._limiter.usage(project_id)
        limits = self.registry.limits(project_id)
        midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        async with project_scope(self._sessions, project_id, user_id) as s:
            calls, cost = (
                await s.execute(
                    select(func.count(), func.coalesce(func.sum(LlmCall.cost), 0)).where(
                        LlmCall.project_id == project_id, LlmCall.created_at >= midnight
                    )
                )
            ).one()
        return UsageOut(
            day=day,
            tokens_today=tokens,
            tokens_per_day=limits.tokens_per_day,
            requests_this_minute=requests,
            requests_per_minute=limits.requests_per_minute,
            calls_today=int(calls),
            cost_today=Decimal(cost),
        )

    def _record_failure(self, call: _Call, exc: ProblemError) -> None:
        self._record(
            call,
            "rejected" if exc.status in (400, 403, 404, 429) else "error",
            exc.status,
            tokens=(0, 0),
            usage_source="none",
            error_code=exc.code,
        )

    def _record(
        self,
        call: _Call,
        status: str,
        http_status: int,
        *,
        tokens: tuple[int, int],
        usage_source: str,
        cache_hit: bool = False,
        error_code: str | None = None,
    ) -> CallInfo:
        model = call.model
        row = LlmCall(
            id=uuid7(),
            project_id=call.caller.project_id,
            user_id=call.caller.user_id,
            api_key_id=call.caller.api_key_id,
            endpoint=call.endpoint,
            requested=call.requested[:200],
            model_id=model.id if model else None,
            model_name=model.name if model else None,
            status=status,
            http_status=http_status,
            error_code=error_code,
            attempts=call.attempts,
            stream=call.stream,
            cache_hit=cache_hit,
            tokens_in=tokens[0],
            tokens_out=tokens[1],
            usage_source=usage_source,
            cost=Decimal(0) if cache_hit or model is None else model.cost(*tokens),
            ttft_ms=call.ttft_ms,
            latency_ms=call.elapsed_ms(),
            trace_id=call.caller.trace_id,
        )
        self._spawn(self._insert(row))
        record_gateway_call(
            row.endpoint,
            row.model_name,
            row.status,
            row.tokens_in,
            row.tokens_out,
            row.cost,
            row.latency_ms,
        )
        return CallInfo(
            call_id=row.id,
            model_name=row.model_name,
            tokens_in=row.tokens_in,
            tokens_out=row.tokens_out,
            cost=row.cost,
            latency_ms=row.latency_ms,
            cache_hit=cache_hit,
        )

    async def _insert(self, row: LlmCall) -> None:
        try:
            async with project_scope(self._sessions, row.project_id, row.user_id) as s:
                s.add(row)
        except Exception as exc:
            log.error("gateway.record_failed", error=str(exc), project_id=str(row.project_id))

    def _spawn(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def drain(self) -> None:
        """Wait for pending call records (shutdown, tests)."""
        while self._background:
            with contextlib.suppress(Exception):
                await asyncio.gather(*list(self._background), return_exceptions=True)
