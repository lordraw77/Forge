"""PostgreSQL helpers: execution journal (to count re-executions) and idempotent tool log."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

SCHEMA = "spike_e05a"
CHECKPOINT_SCHEMA = "langgraph"


def dsn() -> str:
    return os.environ["FORGE_SPIKE_DSN"]


async def setup() -> None:
    async with await psycopg.AsyncConnection.connect(dsn(), autocommit=True) as conn:
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {CHECKPOINT_SCHEMA}")
        await conn.execute(
            f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.event (
                    id bigserial PRIMARY KEY,
                    thread_id text NOT NULL,
                    kind text NOT NULL,
                    name text NOT NULL,
                    pid int NOT NULL,
                    at timestamptz NOT NULL DEFAULT clock_timestamp())"""
        )
        await conn.execute(
            f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.tool_call_log (
                    idempotency_key text PRIMARY KEY,
                    result text NOT NULL,
                    at timestamptz NOT NULL DEFAULT clock_timestamp())"""
        )


def checkpoint_pool() -> AsyncConnectionPool:
    """Pool for AsyncPostgresSaver: needs autocommit and dict rows; tables live in `langgraph`."""
    return AsyncConnectionPool(
        dsn(),
        min_size=1,
        max_size=10,
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": f"-c search_path={CHECKPOINT_SCHEMA}",
        },
    )


async def record(thread_id: str, kind: str, name: str) -> None:
    async with await psycopg.AsyncConnection.connect(dsn(), autocommit=True) as conn:
        await conn.execute(
            f"INSERT INTO {SCHEMA}.event (thread_id, kind, name, pid) VALUES (%s, %s, %s, %s)",
            (thread_id, kind, name, os.getpid()),
        )


async def events(thread_id: str) -> list[dict[str, object]]:
    async with await psycopg.AsyncConnection.connect(dsn(), row_factory=dict_row) as conn:
        cur = await conn.execute(
            f"SELECT kind, name, pid, at FROM {SCHEMA}.event WHERE thread_id = %s ORDER BY id",
            (thread_id,),
        )
        return list(await cur.fetchall())


async def idempotent(key: str, effect: Callable[[], Awaitable[str]]) -> tuple[str, bool]:
    """Run `effect` at most once per key. Returns (result, executed_now).

    ADR-004: LangGraph re-runs an interrupted node from its start and Temporal retries
    activities, so side-effecting tools must be keyed by `{thread_id}:{tool_call_id}`.
    """
    async with await psycopg.AsyncConnection.connect(dsn(), autocommit=True) as conn:
        cur = await conn.execute(
            f"SELECT result FROM {SCHEMA}.tool_call_log WHERE idempotency_key = %s", (key,)
        )
        row = await cur.fetchone()
        if row:
            return str(row[0]), False
        result = await effect()
        await conn.execute(
            f"INSERT INTO {SCHEMA}.tool_call_log (idempotency_key, result) VALUES (%s, %s)"
            " ON CONFLICT (idempotency_key) DO NOTHING",
            (key, result),
        )
        return result, True
