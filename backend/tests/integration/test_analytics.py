"""M2.6 dashboards, Model Insights and the activity log (AC9, US-1.4.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tests.integration.test_gateway import (
    ADMIN,
    admin,
    calls,
    clean,
    client,
    mock,
    model,
    ok,
    project,
    provider,
    sdk,
    settings,
)

__all__ = ["admin", "clean", "client", "mock", "settings"]


@pytest.mark.ac("AC9")
def test_dashboard_totals_match_the_call_records(
    admin: TestClient, client: TestClient, mock: str
) -> None:
    p = project(admin, client)
    prov = provider(admin, mock)
    m = model(admin, prov["id"], "gpt-echo", enabled_project_ids=[p["id"]])
    api = sdk(client, p["key"])
    for i in range(3):
        api.chat.completions.create(
            model=m["name"], messages=[{"role": "user", "content": f"hello {i}"}]
        )
    rows = calls(client, p["id"])
    dash = ok(admin.get(f"/api/v1/projects/{p['id']}/analytics"))
    assert dash["totals"]["calls"] == len(rows) == 3
    assert dash["totals"]["tokens_in"] == sum(r["tokens_in"] for r in rows)
    assert dash["totals"]["tokens_out"] == sum(r["tokens_out"] for r in rows)
    assert dash["totals"]["latency_p95_ms"] is not None
    assert dash["by_model"][0]["calls"] == 3
    assert sum(pt["calls"] for pt in dash["series"]) == 3
    assert dash["by_user"][0]["calls"] == 3  # the key acts as its owner
    assert dash["guardrails"]["total"] == 0


def test_dashboard_is_per_project_and_range_is_validated(
    admin: TestClient, client: TestClient, mock: str
) -> None:
    a, b = project(admin, client), project(admin, client)
    prov = provider(admin, mock)
    m = model(admin, prov["id"], "gpt-echo", enabled_project_ids=[a["id"], b["id"]])
    sdk(client, a["key"]).chat.completions.create(
        model=m["name"], messages=[{"role": "user", "content": "x"}]
    )
    assert ok(admin.get(f"/api/v1/projects/{b['id']}/analytics"))["totals"]["calls"] == 0
    r = admin.get(
        f"/api/v1/projects/{a['id']}/analytics",
        params={"since": "2020-01-01T00:00:00Z", "until": "2026-01-01T00:00:00Z"},
    )
    assert r.status_code == 422


def test_model_insights_across_projects(admin: TestClient, client: TestClient, mock: str) -> None:
    p = project(admin, client)
    prov = provider(admin, mock)
    m = model(admin, prov["id"], "gpt-echo", enabled_project_ids=[p["id"]])
    sdk(client, p["key"]).chat.completions.create(
        model=m["name"], messages=[{"role": "user", "content": "x"}]
    )
    calls(client, p["id"])
    since = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
    out = ok(admin.get(f"{ADMIN}/model-insights", params={"since": since}))
    assert out["totals"]["calls"] >= 1
    assert any(r["model"] == m["name"] and r["calls"] >= 1 for r in out["models"])
    assert out["providers"]


def test_activity_log_lists_admin_actions(admin: TestClient, client: TestClient) -> None:
    p = project(admin, client)
    items = ok(admin.get(f"/api/v1/projects/{p['id']}/activity"))
    assert isinstance(items, list)
    ok(admin.get(f"/api/v1/projects/{p['id']}/activity", params={"q": "nothing-matches-this"}))
    assert isinstance(ok(admin.get("/api/v1/me/activity")), list)
