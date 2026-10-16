"""E0.5d runner: LiteLLM against mock providers; capability matrix, request contracts, overhead.

Run through scripts/spike-e05d.sh, which executes it in a network namespace with only
loopback (no outbound network), to prove the gateway library works air-gapped.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import uvicorn

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
import litellm

from spikes.e05d import mock_providers

litellm.telemetry = False
PORT = 18080
BASE = f"http://127.0.0.1:{PORT}"
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }
]
MESSAGES = [{"role": "user", "content": "ping"}]

# Forge provider type -> LiteLLM call parameters (model prefix selects the wire format).
PROVIDERS: dict[str, dict[str, Any]] = {
    "vLLM": {"prefix": "hosted_vllm/", "api_base": f"{BASE}/v1", "api_key": "k-vllm"},
    "OpenAI-compatible (llama.cpp, NIM, TGI, LM Studio)": {
        "prefix": "openai/",
        "api_base": f"{BASE}/v1",
        "api_key": "k-oai",
    },
    "Ollama (native API)": {"prefix": "ollama_chat/", "api_base": BASE},
    "Azure OpenAI": {
        "prefix": "azure/",
        "api_base": BASE,
        "api_key": "k-azure",
        "api_version": "2025-04-01-preview",
    },
    "Anthropic": {"prefix": "anthropic/", "api_base": BASE, "api_key": "k-anthropic"},
    "Mistral": {"prefix": "mistral/", "api_base": f"{BASE}/v1", "api_key": "k-mistral"},
}


def call_kwargs(provider: str, model: str) -> dict[str, Any]:
    cfg = dict(PROVIDERS[provider])
    prefix = cfg.pop("prefix")
    return {"model": f"{prefix}{model}", **cfg}


def start_mock() -> None:
    config = uvicorn.Config(mock_providers.app, host="127.0.0.1", port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            httpx.get(f"{BASE}/docs", timeout=0.2)
            return
        except httpx.HTTPError:
            time.sleep(0.05)
    raise RuntimeError("mock server did not start")


# ---- scenarios: each returns (ok, detail)
async def chat(p: str) -> tuple[bool, str]:
    r = await litellm.acompletion(messages=MESSAGES, **call_kwargs(p, "m-chat"))
    u = r.usage
    ok = (
        r.choices[0].message.content == "pong"
        and u.prompt_tokens == 11
        and u.completion_tokens == 7
    )
    return (
        ok,
        f"content={r.choices[0].message.content!r} usage={u.prompt_tokens}/{u.completion_tokens}",
    )


async def stream(p: str) -> tuple[bool, str]:
    resp = await litellm.acompletion(
        messages=MESSAGES,
        stream=True,
        stream_options={"include_usage": True},
        **call_kwargs(p, "m-stream"),
    )
    text, usage, chunks = "", None, 0
    async for chunk in resp:
        chunks += 1
        if chunk.choices and chunk.choices[0].delta.content:
            text += chunk.choices[0].delta.content
        if getattr(chunk, "usage", None):
            usage = chunk.usage
    has_usage = usage is not None and usage.completion_tokens == 7
    return (
        text == "pong" and has_usage,
        f"text={text!r} chunks={chunks} usage={'yes' if has_usage else 'missing'}",
    )


async def tools(p: str) -> tuple[bool, str]:
    r = await litellm.acompletion(messages=MESSAGES, tools=TOOLS, **call_kwargs(p, "m-tools"))
    calls = r.choices[0].message.tool_calls or []
    ok = (
        bool(calls)
        and calls[0].function.name == "get_weather"
        and json.loads(calls[0].function.arguments) == {"city": "Rome"}
    )
    return ok, f"tool_calls={[c.function.name for c in calls]}"


async def rate_limit(p: str) -> tuple[bool, str]:
    try:
        await litellm.acompletion(messages=MESSAGES, num_retries=0, **call_kwargs(p, "m-fail429"))
    except litellm.RateLimitError:
        return True, "RateLimitError"
    except Exception as exc:
        return False, type(exc).__name__
    return False, "no error"


async def retry_5xx(p: str) -> tuple[bool, str]:
    model = f"m-{p[:3].lower()}-fail500"
    try:
        r = await litellm.acompletion(messages=MESSAGES, num_retries=3, **call_kwargs(p, model))
    except Exception as exc:
        return False, f"{type(exc).__name__} after {mock_providers.ATTEMPTS[model]} attempts"
    attempts = mock_providers.ATTEMPTS[model]
    return r.choices[
        0
    ].message.content == "pong" and attempts == 3, f"succeeded on attempt {attempts}"


async def timeout(p: str) -> tuple[bool, str]:
    t0 = time.monotonic()
    try:
        await litellm.acompletion(
            messages=MESSAGES, timeout=1, num_retries=0, **call_kwargs(p, "m-slow")
        )
    except litellm.Timeout:
        return True, f"Timeout after {time.monotonic() - t0:.1f}s"
    except Exception as exc:
        return False, type(exc).__name__
    return False, "no timeout"


SCENARIOS: dict[str, Callable[[str], Awaitable[tuple[bool, str]]]] = {
    "chat": chat,
    "stream": stream,
    "tools": tools,
    "429 mapped": rate_limit,
    "5xx retried": retry_5xx,
    "timeout": timeout,
}


async def embeddings_and_rerank() -> dict[str, tuple[bool, str]]:
    out: dict[str, tuple[bool, str]] = {}
    for name, kwargs in {
        "embeddings vLLM": {"model": "hosted_vllm/e", "api_base": f"{BASE}/v1", "api_key": "k"},
        "embeddings OpenAI-compatible": {
            "model": "openai/e",
            "api_base": f"{BASE}/v1",
            "api_key": "k",
        },
        "embeddings Ollama": {"model": "ollama/e", "api_base": BASE},
    }.items():
        try:
            r = await litellm.aembedding(input=["a", "b"], **kwargs)
            ok = len(r.data) == 2 and len(r.data[0]["embedding"]) == 4
            out[name] = (ok, f"{len(r.data)} vectors, dim {len(r.data[0]['embedding'])}")
        except Exception as exc:
            out[name] = (False, f"{type(exc).__name__}: {str(exc)[:120]}")
    try:
        r = await litellm.arerank(
            model="hosted_vllm/r",
            query="q",
            documents=["d1", "d2", "d3"],
            top_n=2,
            api_base=BASE,
            api_key="k",
        )
        ok = len(r.results) == 2 and r.results[0]["index"] == 0
        out["rerank vLLM"] = (
            ok,
            f"{len(r.results)} results, path {mock_providers.RECORDS[-1]['path']}",
        )
    except Exception as exc:
        out["rerank vLLM"] = (False, f"{type(exc).__name__}: {str(exc)[:120]}")
    return out


def contracts() -> dict[str, tuple[bool, str]]:
    """Check what actually went over the wire for provider-specific details."""
    rec = mock_providers.RECORDS

    def first(pred: Callable[[dict[str, Any]], bool]) -> dict[str, Any] | None:
        return next((r for r in rec if pred(r)), None)

    az = first(lambda r: r["path"].startswith("/openai/deployments/"))
    an = first(lambda r: r["path"] == "/v1/messages")
    vl = first(
        lambda r: (
            r["path"] == "/v1/chat/completions"
            and r["headers"].get("authorization") == "Bearer k-vllm"
        )
    )
    ol = first(lambda r: r["path"] == "/api/chat")
    return {
        "Azure: deployment in path, api-version, api-key header": (
            bool(
                az
                and az["query"].get("api-version") == "2025-04-01-preview"
                and az["headers"].get("api-key") == "k-azure"
            ),
            json.dumps({k: az[k] for k in ("path", "query")} if az else {}),
        ),
        "Anthropic: x-api-key and anthropic-version headers": (
            bool(
                an
                and an["headers"].get("x-api-key") == "k-anthropic"
                and an["headers"].get("anthropic-version")
            ),
            f"anthropic-version={an['headers'].get('anthropic-version') if an else None}",
        ),
        "vLLM: bearer token": (vl is not None, "Authorization: Bearer …" if vl else "missing"),
        "Ollama: native /api/chat": (ol is not None, "POST /api/chat" if ol else "missing"),
    }


async def overhead(n: int = 300) -> dict[str, float]:
    """Per-call latency of LiteLLM vs a raw httpx call to the same mock endpoint."""
    body = {"model": "m-chat", "messages": MESSAGES}
    raw, lite = [], []
    async with httpx.AsyncClient() as client:
        for _ in range(20):
            await client.post(f"{BASE}/v1/chat/completions", json=body)
            await litellm.acompletion(
                messages=MESSAGES,
                **call_kwargs("OpenAI-compatible (llama.cpp, NIM, TGI, LM Studio)", "m-chat"),
            )
        for _ in range(n):
            t0 = time.perf_counter()
            await client.post(
                f"{BASE}/v1/chat/completions", json=body, headers={"Authorization": "Bearer k"}
            )
            raw.append((time.perf_counter() - t0) * 1000)
            t0 = time.perf_counter()
            await litellm.acompletion(
                messages=MESSAGES,
                **call_kwargs("OpenAI-compatible (llama.cpp, NIM, TGI, LM Studio)", "m-chat"),
            )
            lite.append((time.perf_counter() - t0) * 1000)

    def p(v: list[float], q: float) -> float:
        return round(sorted(v)[int(len(v) * q)], 2)

    return {
        "raw_p50_ms": p(raw, 0.5),
        "raw_p95_ms": p(raw, 0.95),
        "litellm_p50_ms": p(lite, 0.5),
        "litellm_p95_ms": p(lite, 0.95),
        "overhead_p50_ms": round(p(lite, 0.5) - p(raw, 0.5), 2),
        "overhead_p95_ms": round(p(lite, 0.95) - p(raw, 0.95), 2),
        "litellm_mean_ms": round(statistics.mean(lite), 2),
    }


def import_cost() -> dict[str, float]:
    code = (
        "import time,resource,os;os.environ['LITELLM_LOCAL_MODEL_COST_MAP']='True';"
        "t=time.perf_counter();import litellm;"
        "print(round(time.perf_counter()-t,2), resource.getrusage(resource.RUSAGE_SELF).ru_maxrss//1024)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.split()
    return {"import_s": float(out[0]), "rss_after_import_mb": float(out[1])}


def network_isolated() -> bool:
    try:
        httpx.get("https://pypi.org", timeout=2)
    except httpx.HTTPError:
        return True
    return False


async def main() -> int:
    start_mock()
    isolated = network_isolated()
    print(
        f"litellm {litellm.__version__ if hasattr(litellm, '__version__') else ''} | outbound network blocked: {isolated}\n"
    )

    matrix: dict[str, dict[str, tuple[bool, str]]] = {}
    for provider in PROVIDERS:
        matrix[provider] = {}
        for name, fn in SCENARIOS.items():
            try:
                matrix[provider][name] = await fn(provider)
            except Exception as exc:
                matrix[provider][name] = (False, f"{type(exc).__name__}: {str(exc)[:160]}")

    cols = list(SCENARIOS)
    print(f"{'provider':52} " + " ".join(f"{c:12}" for c in cols))
    for provider, res in matrix.items():
        print(f"{provider:52} " + " ".join(f"{('PASS' if res[c][0] else 'FAIL'):12}" for c in cols))
    failures = {f"{p} / {c}": res[c][1] for p, res in matrix.items() for c in cols if not res[c][0]}

    extra = await embeddings_and_rerank()
    extra.update(contracts())
    print()
    for name, (ok, detail) in extra.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}")

    perf = await overhead()
    imp = import_cost()
    print(f"\noverhead: {perf}\nimport: {imp}")
    if failures:
        print("\nfailure details:")
        for k, v in failures.items():
            print(f"  {k}: {v}")

    report = {
        "outbound_network_blocked": isolated,
        "matrix": {
            p: {c: {"ok": ok, "detail": d} for c, (ok, d) in res.items()}
            for p, res in matrix.items()
        },
        "extra": {k: {"ok": ok, "detail": d} for k, (ok, d) in extra.items()},
        "overhead": perf,
        "import": imp,
    }
    out = os.environ.get("SPIKE_E05D_OUT")
    if out:
        with open(out, "w") as fh:
            json.dump(report, fh, indent=2)
    all_ok = isolated and not failures and all(ok for ok, _ in extra.values())
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
