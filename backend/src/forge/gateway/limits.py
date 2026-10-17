"""Quotas, rate limits and the exact-match response cache, on Valkey/Redis (F1.6, F1.7).

Token quotas use reserve/reconcile: a request is admitted while the day's counter (used plus
in-flight reservations) is below the limit, then reserves its prompt estimate plus its
`max_tokens`; when it ends the counter is corrected to the real usage. Concurrent requests
therefore overshoot the daily quota by at most one request's reservation (US-1.7.1 #3).
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import redis.asyncio as redis

from forge.core.errors import ProblemError

# Check every counter first, then increment them all: nothing is reserved when one is full.
# KEYS: counters; ARGV: n, then per key (limit, amount, ttl, kind) with kind "tokens"|"requests".
_ADMIT = """
local n = tonumber(ARGV[1])
for i = 1, n do
  local limit = tonumber(ARGV[2 + (i - 1) * 4])
  local amount = tonumber(ARGV[3 + (i - 1) * 4])
  local kind = ARGV[5 + (i - 1) * 4]
  if limit > 0 then
    local current = tonumber(redis.call('GET', KEYS[i]) or '0')
    if kind == 'requests' and current + amount > limit then return i end
    if kind == 'tokens' and current >= limit then return i end
  end
end
for i = 1, n do
  local amount = tonumber(ARGV[3 + (i - 1) * 4])
  local ttl = tonumber(ARGV[4 + (i - 1) * 4])
  if amount ~= 0 then
    redis.call('INCRBY', KEYS[i], amount)
    redis.call('EXPIRE', KEYS[i], ttl)
  end
end
return 0
"""


@dataclass(frozen=True)
class Counter:
    key: str
    limit: int
    amount: int
    ttl: int
    kind: str  # "tokens" | "requests"
    code: str
    retry_after: int


@dataclass
class Reservation:
    """What a call holds; `reconcile` replaces the reserved tokens with the real usage."""

    token_keys: tuple[str, ...]
    reserved: int
    day: str
    settled: bool = False


def _day(now: datetime) -> str:
    return now.strftime("%Y%m%d")


def _seconds_to_midnight(now: datetime) -> int:
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))


class Limiter:
    def __init__(self, client: redis.Redis) -> None:
        self._redis = client
        self._admit = client.register_script(_ADMIT)

    @staticmethod
    def keys(project_id: uuid.UUID, user_id: uuid.UUID | None, now: datetime) -> dict[str, str]:
        day, minute = _day(now), int(now.timestamp()) // 60
        user = str(user_id) if user_id else "-"
        return {
            "project_tokens": f"gw:t:{project_id}:{day}",
            "user_tokens": f"gw:t:{project_id}:{user}:{day}",
            "project_requests": f"gw:r:{project_id}:{minute}",
            "user_requests": f"gw:r:{project_id}:{user}:{minute}",
        }

    async def admit(
        self,
        project_id: uuid.UUID,
        user_id: uuid.UUID | None,
        *,
        tokens_per_day: int,
        user_tokens_per_day: int,
        requests_per_minute: int,
        user_requests_per_minute: int,
        reserve: int,
    ) -> Reservation:
        now = datetime.now(UTC)
        k = self.keys(project_id, user_id, now)
        to_midnight = _seconds_to_midnight(now)
        to_minute = 60 - now.second
        counters = [
            Counter(k["project_requests"], requests_per_minute, 1, 120, "requests",
                    "rate_limited", to_minute),
            Counter(k["user_requests"], user_requests_per_minute if user_id else 0, 1, 120,
                    "requests", "user_rate_limited", to_minute),
            Counter(k["project_tokens"], tokens_per_day, reserve, 2 * 86400, "tokens",
                    "quota_exceeded", to_midnight),
            Counter(k["user_tokens"], user_tokens_per_day if user_id else 0, reserve,
                    2 * 86400, "tokens", "user_quota_exceeded", to_midnight),
        ]  # fmt: skip
        args: list[Any] = [len(counters)]
        for c in counters:
            args += [c.limit, c.amount, c.ttl, c.kind]
        failed = int(await self._admit(keys=[c.key for c in counters], args=args))
        if failed:
            c = counters[failed - 1]
            what = "requests per minute" if c.kind == "requests" else "tokens per day"
            scope = "user" if c.code.startswith("user_") else "project"
            raise ProblemError(
                429,
                c.code,
                "Rate limit exceeded" if c.kind == "requests" else "Token quota exceeded",
                f"The {scope} limit of {c.limit} {what} has been reached",
                headers={"Retry-After": str(c.retry_after)},
            )
        return Reservation((k["project_tokens"], k["user_tokens"]), reserve, _day(now))

    async def reconcile(self, r: Reservation, actual: int) -> None:
        """Replace the reservation with the real token count (idempotent)."""
        if r.settled:
            return
        r.settled = True
        delta = actual - r.reserved
        if delta:
            pipe = self._redis.pipeline(transaction=False)
            for key in r.token_keys:
                pipe.incrby(key, delta)
            await pipe.execute()

    async def model_slot(self, model_id: uuid.UUID, rpm_limit: int | None) -> bool:
        """Per-model requests/minute (F1.2); a full model is skipped for the next in chain."""
        if not rpm_limit:
            return True
        key = f"gw:m:{model_id}:{int(time.time()) // 60}"
        count = int(await self._redis.incr(key))
        if count == 1:
            await self._redis.expire(key, 120)
        return count <= rpm_limit

    async def usage(self, project_id: uuid.UUID) -> tuple[str, int, int]:
        now = datetime.now(UTC)
        k = self.keys(project_id, None, now)
        tokens, requests = await self._redis.mget(k["project_tokens"], k["project_requests"])
        return _day(now), int(tokens or 0), int(requests or 0)


class ResponseCache:
    """Exact match on (project, requested model, messages, parameters); off by default."""

    def __init__(self, client: redis.Redis) -> None:
        self._redis = client

    @staticmethod
    def key(project_id: uuid.UUID, requested: str, params: dict[str, Any]) -> str:
        material = json.dumps(
            {"model": requested.lower(), "params": params}, sort_keys=True, default=str
        )
        return f"gw:c:{project_id}:{hashlib.sha256(material.encode()).hexdigest()}"

    async def get(self, key: str) -> dict[str, Any] | None:
        raw = await self._redis.get(key)
        return json.loads(raw) if raw else None

    async def put(self, key: str, value: dict[str, Any], ttl_s: int) -> None:
        await self._redis.set(key, json.dumps(value), ex=ttl_s)
