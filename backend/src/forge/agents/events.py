"""Run events on Redis streams (ADR-004): the worker publishes, the API relays over SSE.

One stream per run, `forge:run:<run_id>`. Entries are JSON `{"type": ..., ...}`. A late or
reconnecting client reads from the start (or from `Last-Event-ID`); when the stream has expired
the API rebuilds the history from `run_step` and `agent_run`.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import redis.asyncio as redis

STREAM_TTL_S = 3600
STREAM_MAXLEN = 5000
TERMINAL_EVENTS = frozenset({"done", "error"})


def stream_key(run_id: uuid.UUID) -> str:
    return f"forge:run:{run_id}"


class RunEvents:
    def __init__(self, client: redis.Redis) -> None:
        self._redis = client

    async def publish(self, run_id: uuid.UUID, event_type: str, data: dict[str, Any]) -> str:
        key = stream_key(run_id)
        payload = json.dumps({"type": event_type, **data}, default=str, separators=(",", ":"))
        entry_id: bytes | str = await self._redis.xadd(
            key, {"d": payload}, maxlen=STREAM_MAXLEN, approximate=True
        )
        await self._redis.expire(key, STREAM_TTL_S)
        return entry_id.decode() if isinstance(entry_id, bytes) else entry_id

    async def read(
        self, run_id: uuid.UUID, last_id: str = "0-0", block_ms: int = 1000
    ) -> list[tuple[str, dict[str, Any]]]:
        """Entries after `last_id`; waits up to `block_ms` for new ones."""
        rows: Any = await self._redis.xread(
            {stream_key(run_id): last_id}, block=block_ms, count=200
        )
        out: list[tuple[str, dict[str, Any]]] = []
        for _key, entries in rows or []:
            for entry_id, fields in entries:
                raw = fields.get(b"d") or fields.get("d")
                eid = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
                text = raw.decode() if isinstance(raw, bytes) else raw
                out.append((eid, json.loads(text)))
        return out

    async def exists(self, run_id: uuid.UUID) -> bool:
        return bool(await self._redis.exists(stream_key(run_id)))
