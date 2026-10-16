"""One mock server speaking several provider wire formats, recording every request.

Behaviour is driven by the requested model name:
  *-fail429   -> 429 rate limit        *-fail500 -> 500 until the 3rd attempt, then OK
  *-slow      -> sleeps 3 s            *-tools   -> answers with a tool call
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
RECORDS: list[dict[str, Any]] = []
ATTEMPTS: Counter[str] = Counter()
USAGE = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}


async def _record(request: Request) -> dict[str, Any]:
    body = await request.json()
    RECORDS.append(
        {
            "path": request.url.path,
            "query": dict(request.query_params),
            "headers": {
                k: v
                for k, v in request.headers.items()
                if k in ("authorization", "api-key", "x-api-key", "anthropic-version")
            },
            "body": body,
        }
    )
    return body


async def _behaviour(model: str) -> JSONResponse | None:
    ATTEMPTS[model] += 1
    if model.endswith("-fail429"):
        return JSONResponse({"error": {"message": "rate limited", "type": "rate_limit"}}, 429)
    if model.endswith("-fail500") and ATTEMPTS[model] < 3:
        return JSONResponse({"error": {"message": "boom", "type": "server_error"}}, 500)
    if model.endswith("-slow"):
        await asyncio.sleep(3)
    return None


def _openai_message(model: str) -> dict[str, Any]:
    if model.endswith("-tools"):
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": json.dumps({"city": "Rome"})},
                }
            ],
        }
    return {"role": "assistant", "content": "pong"}


# ---- OpenAI-compatible: vLLM, llama.cpp, NIM, Mistral, generic; Azure via deployment path
async def _openai_chat(request: Request, model_override: str | None = None) -> Any:
    body = await _record(request)
    model = model_override or body["model"]
    if err := await _behaviour(model):
        return err
    msg = _openai_message(model)
    if body.get("stream"):

        async def gen() -> Any:
            base = {
                "id": "c1",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
            }
            for piece in ["po", "ng"]:
                yield f"data: {json.dumps({**base, 'choices': [{'index': 0, 'delta': {'content': piece}, 'finish_reason': None}]})}\n\n"
            yield f"data: {json.dumps({**base, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
            if (body.get("stream_options") or {}).get("include_usage"):
                yield f"data: {json.dumps({**base, 'choices': [], 'usage': USAGE})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")
    finish = "tool_calls" if msg.get("tool_calls") else "stop"
    return {
        "id": "c1",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
        "usage": USAGE,
    }


@app.post("/v1/chat/completions")
async def openai_chat(request: Request) -> Any:
    return await _openai_chat(request)


@app.post("/openai/deployments/{deployment}/chat/completions")
async def azure_chat(deployment: str, request: Request) -> Any:
    return await _openai_chat(request, model_override=deployment)


@app.post("/v1/embeddings")
async def openai_embeddings(request: Request) -> Any:
    body = await _record(request)
    inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
    return {
        "object": "list",
        "model": body["model"],
        "data": [
            {"object": "embedding", "index": i, "embedding": [0.1, 0.2, 0.3, 0.4]}
            for i in range(len(inputs))
        ],
        "usage": {"prompt_tokens": 5 * len(inputs), "total_tokens": 5 * len(inputs)},
    }


@app.post("/rerank")
@app.post("/v1/rerank")
async def rerank(request: Request) -> Any:
    body = await _record(request)
    docs = body["documents"]
    results = [
        {"index": i, "relevance_score": round(1 - i / len(docs), 3)} for i in range(len(docs))
    ]
    return {
        "id": "r1",
        "model": body["model"],
        "results": results[: body.get("top_n") or len(docs)],
        "usage": {"total_tokens": 20},
    }


# ---- Ollama native
@app.post("/api/chat")
async def ollama_chat(request: Request) -> Any:
    body = await _record(request)
    if err := await _behaviour(body["model"]):
        return err
    message: dict[str, Any] = {"role": "assistant", "content": "pong"}
    if body["model"].endswith("-tools"):
        message = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "get_weather", "arguments": {"city": "Rome"}}}],
        }
    done = {
        "model": body["model"],
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 11,
        "eval_count": 7,
    }
    if body.get("stream"):

        async def gen() -> Any:
            for piece in ["po", "ng"]:
                yield (
                    json.dumps(
                        {
                            "model": body["model"],
                            "message": {"role": "assistant", "content": piece},
                            "done": False,
                        }
                    )
                    + "\n"
                )
            yield json.dumps({**done, "message": {"role": "assistant", "content": ""}}) + "\n"

        return StreamingResponse(gen(), media_type="application/x-ndjson")
    return {**done, "message": message}


@app.post("/api/embed")
async def ollama_embed(request: Request) -> Any:
    body = await _record(request)
    inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
    return {
        "model": body["model"],
        "embeddings": [[0.1, 0.2, 0.3, 0.4] for _ in inputs],
        "prompt_eval_count": 5,
    }


# ---- Anthropic Messages API
@app.post("/v1/messages")
async def anthropic_messages(request: Request) -> Any:
    body = await _record(request)
    if err := await _behaviour(body["model"]):
        return err
    usage = {"input_tokens": 11, "output_tokens": 7}
    if body["model"].endswith("-tools"):
        content: list[dict[str, Any]] = [
            {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": "Rome"}}
        ]
        stop = "tool_use"
    else:
        content, stop = [{"type": "text", "text": "pong"}], "end_turn"
    if body.get("stream"):

        async def gen() -> Any:
            def ev(name: str, data: dict[str, Any]) -> str:
                return f"event: {name}\ndata: {json.dumps(data)}\n\n"

            yield ev(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": "m1",
                        "type": "message",
                        "role": "assistant",
                        "model": body["model"],
                        "content": [],
                        "stop_reason": None,
                        "usage": {"input_tokens": 11, "output_tokens": 0},
                    },
                },
            )
            yield ev(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            )
            for piece in ["po", "ng"]:
                yield ev(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": piece},
                    },
                )
            yield ev("content_block_stop", {"type": "content_block_stop", "index": 0})
            yield ev(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 7},
                },
            )
            yield ev("message_stop", {"type": "message_stop"})

        return StreamingResponse(gen(), media_type="text/event-stream")
    return {
        "id": "m1",
        "type": "message",
        "role": "assistant",
        "model": body["model"],
        "content": content,
        "stop_reason": stop,
        "usage": usage,
    }
