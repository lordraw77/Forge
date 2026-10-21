"""M2.7 performance baseline (NFR). Skipped unless FORGE_PERF=1:

    FORGE_PERF=1 scripts/test-integration.sh tests/integration/test_perf_baseline.py -s

Numbers come from a TestClient (no network hop to the API) against the compose database and the
mock provider, so they measure Forge's own work, not a model's. Results print as a table.
"""

from __future__ import annotations

import os
import statistics
import time
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.integration.test_gateway import (
    admin,
    clean,
    client,
    mock,
    model,
    ok,
    project,
    provider,
    settings,
)
from tests.integration.test_platform_core import new_client

__all__ = ["admin", "clean", "client", "mock", "settings"]

pytestmark = pytest.mark.skipif(not os.environ.get("FORGE_PERF"), reason="FORGE_PERF=1 to run")

CALLS = 300
ROWS = 200_000


def pct(values: list[float], q: float) -> float:
    return sorted(values)[min(len(values) - 1, int(len(values) * q))]


def timed(fn: Any, n: int) -> list[float]:
    out = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t) * 1000)
    return out


def row(name: str, target: str, values: list[float]) -> None:
    print(f"| {name} | {target} | {statistics.median(values):.1f} | {pct(values, 0.95):.1f} |")


def test_baseline(admin: TestClient, client: TestClient, mock: str) -> None:
    p = project(admin, client)
    prov = provider(admin, mock)
    m = model(admin, prov["id"], "gpt-echo", enabled_project_ids=[p["id"]])
    key = new_client(client, headers={"Authorization": f"Bearer {p['key']}"})
    body = {"model": m["name"], "messages": [{"role": "user", "content": "ping"}]}
    direct_body = {"model": "gpt-echo", "messages": body["messages"]}
    with httpx.Client() as direct:
        direct_ms = timed(
            lambda: direct.post(
                f"{mock}/chat/completions", json=direct_body, headers={"Authorization": "Bearer x"}
            ),
            CALLS,
        )
    timed(lambda: key.post("/v1/chat/completions", json=body), 20)  # warm-up
    gateway_ms = timed(lambda: key.post("/v1/chat/completions", json=body), CALLS)
    overhead = [max(0.0, g - statistics.median(direct_ms)) for g in gateway_ms]

    # Seed ROWS llm_call rows spread over 30 days, then time the dashboard queries.
    owner_sql = (
        "INSERT INTO llm_call (id, project_id, endpoint, requested, model_name, status, "
        "http_status, tokens_in, tokens_out, cost, latency_ms, created_at) "
        "SELECT gen_random_uuid(), :p, 'chat', 'bench', 'bench-' || (n % 8), "
        "CASE WHEN n % 20 = 0 THEN 'error' ELSE 'ok' END, 200, 100, 50, 0.0001, "
        "20 + (n % 900), now() - (n % 43200) * interval '1 minute'"
        f"FROM generate_series(1, {ROWS}) n"
    )
    from forge.core.db import admin_scope

    app = client.app  # type: ignore[attr-defined]

    async def seed() -> None:
        async with admin_scope(app.state.sessions) as s:
            await s.execute(text(owner_sql), {"p": p["id"]})
            await s.execute(text("ANALYZE llm_call"))

    client.portal.call(seed)  # type: ignore[attr-defined]
    base = f"/api/v1/projects/{p['id']}"
    dash_ms = timed(lambda: ok(admin.get(f"{base}/analytics", params={"since": _ago(30)})), 5)
    week_ms = timed(lambda: ok(admin.get(f"{base}/analytics")), 5)
    act_ms = timed(lambda: ok(admin.get(f"{base}/activity", params={"q": "bench"})), 10)
    ins_ms = timed(
        lambda: ok(admin.get("/api/v1/admin/model-insights", params={"since": _ago(30)})), 5
    )

    print("\n| Measure | Target | p50 ms | p95 ms |")
    print("|---|---|---|---|")
    row(f"/v1 chat, full pipeline ({CALLS} calls)", "", gateway_ms)
    row("mock provider called directly", "", direct_ms)
    row("Gateway overhead (pipeline - direct)", "< 50 p95", overhead)
    row(f"Dashboard, 30 days, {ROWS} calls", "", dash_ms)
    row(f"Dashboard, 7 days, {ROWS} calls", "", week_ms)
    row("Activity log search", "", act_ms)
    row("Model Insights, 30 days (all projects)", "", ins_ms)

    async def cleanup() -> None:
        async with admin_scope(app.state.sessions) as s:
            await s.execute(text("DELETE FROM llm_call WHERE project_id = :p"), {"p": p["id"]})

    client.portal.call(cleanup)  # type: ignore[attr-defined]
    assert pct(overhead, 0.95) < 50, "gateway overhead above the 50 ms p95 target"


def _ago(days: int) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(days=days)).isoformat()
