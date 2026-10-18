"""OpenAI-compatible mock provider for gateway tests, run on a real port in a thread.

Behaviour comes from the requested model name (suffixes can be combined, e.g. `a-nousage`):
  -fail500  always 500               -flaky    500 twice per model, then OK
  -fail429  429                      -slow     sleeps 3 s
  -auth     401                      -bad      400
  -fail402  402 (billing)
  -tools    answers with a tool call -nousage  never reports usage
  -echo     answers with the received messages as JSON
  -assist   answers like Help Me Write (JSON with framework and prompts)
Embedding models named `emb-<n>` return n dimensions (default 8).
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections import Counter
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
RECORDS: list[dict[str, Any]] = []
ATTEMPTS: Counter[str] = Counter()
USAGE = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}


async def _failure(model: str) -> JSONResponse | None:
    ATTEMPTS[model] += 1
    if "-fail500" in model or ("-flaky" in model and ATTEMPTS[model] <= 2):
        return JSONResponse({"error": {"message": "boom", "type": "server_error"}}, 500)
    if "-fail429" in model:
        return JSONResponse({"error": {"message": "slow down", "type": "rate_limit"}}, 429)
    if "-fail402" in model:
        return JSONResponse({"error": {"message": "payment required", "type": "billing"}}, 402)
    if "-auth" in model:
        return JSONResponse({"error": {"message": "invalid api key", "type": "auth"}}, 401)
    if "-bad" in model:
        return JSONResponse({"error": {"message": "bad field", "type": "invalid_request"}}, 400)
    if "-slow" in model:
        await asyncio.sleep(3)
    return None


def _message(model: str, body: dict[str, Any]) -> dict[str, Any]:
    if "-echo" in model:
        return {"role": "assistant", "content": json.dumps(body["messages"])}
    if "-assist" in model:
        reply = {
            "framework": "rtf",
            "system_prompt": "You are a support agent.",
            "user_prompt": "Answer {customer_name}: {question}",
        }
        return {
            "role": "assistant",
            "content": "Here it is:\n```json\n" + json.dumps(reply) + "\n```",
        }
    if "-tools" in model:
        call = {
            "id": "call_1",
            "type": "function",
            "function": {"name": "get_weather", "arguments": json.dumps({"city": "Rome"})},
        }
        return {"role": "assistant", "content": None, "tool_calls": [call]}
    return {"role": "assistant", "content": f"pong from {model}"}


@app.post("/v1/chat/completions")
async def chat(request: Request) -> Any:
    body = await request.json()
    RECORDS.append(
        {"path": request.url.path, "body": body, "auth": request.headers.get("authorization")}
    )
    model = body["model"]
    if err := await _failure(model):
        return err
    msg = _message(model, body)
    finish = "tool_calls" if msg.get("tool_calls") else "stop"
    base = {"id": "mock-1", "created": int(time.time()), "model": model}
    if body.get("stream"):

        async def gen() -> Any:
            first = {"index": 0, "delta": {"role": "assistant", "content": ""}}
            yield _sse(base | {"object": "chat.completion.chunk", "choices": [first]})
            if msg.get("tool_calls"):
                tc = msg["tool_calls"][0] | {"index": 0}
                delta = {"index": 0, "delta": {"tool_calls": [tc]}}
                yield _sse(base | {"object": "chat.completion.chunk", "choices": [delta]})
            else:
                for word in msg["content"].split(" "):
                    delta = {"index": 0, "delta": {"content": word + " "}}
                    yield _sse(base | {"object": "chat.completion.chunk", "choices": [delta]})
            done = {"index": 0, "delta": {}, "finish_reason": finish}
            yield _sse(base | {"object": "chat.completion.chunk", "choices": [done]})
            include = (body.get("stream_options") or {}).get("include_usage")
            if include and "-nousage" not in model:
                yield _sse(
                    base | {"object": "chat.completion.chunk", "choices": [], "usage": USAGE}
                )
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")
    out = base | {
        "object": "chat.completion",
        "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
    }
    if "-nousage" not in model:
        out["usage"] = USAGE
    return out


@app.post("/v1/embeddings")
async def embeddings(request: Request) -> Any:
    body = await request.json()
    RECORDS.append({"path": request.url.path, "body": body})
    model = body["model"]
    if err := await _failure(model):
        return err
    dims = int(model.split("-")[1]) if model.startswith("emb-") else 8
    inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
    data = [
        {"object": "embedding", "index": i, "embedding": [0.1 * (i + 1)] * dims}
        for i in range(len(inputs))
    ]
    usage = {"prompt_tokens": 3 * len(inputs), "total_tokens": 3 * len(inputs)}
    return {"object": "list", "data": data, "model": model, "usage": usage}


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


class MockProvider:
    """Context manager: `with MockProvider() as base_url: ...` (base_url ends with /v1)."""

    def __enter__(self) -> str:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        self._server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started and time.time() < deadline:
            time.sleep(0.05)
        return f"http://127.0.0.1:{port}/v1"

    def __exit__(self, *_exc: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


def reset() -> None:
    RECORDS.clear()
    ATTEMPTS.clear()
