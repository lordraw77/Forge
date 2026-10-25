"""E3.9 triggers of use cases: schedules and inbound tokens, against the real stack
(PostgreSQL, Temporal, the mock model)."""

from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.integration.test_agents import admin_rows
from tests.integration.test_gateway import ok
from tests.integration.test_platform_core import csrf, new_client
from tests.integration.test_usecase_end_user import granted, published
from tests.integration.test_usecase_runs import (
    admin,
    client,
    env,
    finished,
    http,
    mcp,
    mock,
    reset_mock,
    rt,
    settings,
    tools_env,
    workers,
)

__all__ = [
    "admin",
    "client",
    "env",
    "granted",
    "http",
    "mcp",
    "mock",
    "reset_mock",
    "rt",
    "settings",
    "tools_env",
    "workers",
]

pytestmark = pytest.mark.skipif(
    not os.environ.get("FORGE_TEST_S3_ENDPOINT"), reason="needs S3, OpenSearch and Temporal"
)


def fire_due(client: TestClient) -> int:
    async def go() -> int:
        return await client.app.state.services.triggers.fire_due()  # type: ignore[attr-defined,no-any-return]

    return client.portal.call(go)  # type: ignore[union-attr]


def post(ctx: dict[str, Any], uc_id: str, body: dict[str, Any], expect: int = 201) -> Any:
    c = ctx["builder"]
    return ok(
        c.post(f"{ctx['base']}/use-cases/{uc_id}/triggers", headers=csrf(c), json=body), expect
    )


def run_of(ctx: dict[str, Any], run_id: str) -> Any:
    return finished(ctx, run_id)


def test_interval_schedule_fires_once_and_moves_on(
    granted: dict[str, Any], client: TestClient
) -> None:
    ctx, b = granted, granted["builder"]
    uc = published(ctx)
    t = post(
        ctx,
        uc["id"],
        {"name": "hourly", "kind": "interval", "interval_minutes": 60, "inputs": {"topic": "cron"}},
    )
    assert t["next_run_at"]
    assert t["token"] is None
    admin_rows(
        client,
        f"UPDATE use_case_trigger SET next_run_at = now() - interval '5 minutes' "
        f"WHERE id = '{t['id']}' RETURNING 1",
    )
    fired = fire_due(client)
    assert fired >= 1
    after = next(
        x for x in ok(b.get(f"{ctx['base']}/triggers?use_case_id={uc['id']}")) if x["id"] == t["id"]
    )
    assert after["last_status"] == "started"
    assert after["failures"] == 0
    assert after["next_run_at"] > t["next_run_at"] or after["next_run_at"] != t["next_run_at"]
    run = run_of(ctx, after["last_run_id"])
    assert run["status"] == "succeeded", run
    assert run["inputs"] == {"topic": "cron"}
    assert run["mode"] == "published"


def test_cron_is_validated(granted: dict[str, Any]) -> None:
    uc = published(granted)
    c = granted["builder"]
    for body in (
        {"name": "a", "kind": "cron"},
        {"name": "b", "kind": "cron", "cron": "61 * * * *"},
        {"name": "c", "kind": "cron", "cron": "0 8 * * *", "timezone": "Mars/Base"},
        {"name": "d", "kind": "interval"},
        {"name": "e", "kind": "interval", "interval_minutes": 5, "cron": "* * * * *"},
    ):
        r = c.post(f"{granted['base']}/use-cases/{uc['id']}/triggers", headers=csrf(c), json=body)
        assert r.status_code == 422, (body, r.text)
    good = post(granted, uc["id"], {"name": "weekdays", "kind": "cron", "cron": "0 8 * * 1-5"})
    assert good["next_run_at"]


def test_webhook_token_starts_a_run_and_can_be_rotated(
    granted: dict[str, Any], client: TestClient
) -> None:
    ctx, b = granted, granted["builder"]
    uc = published(ctx)
    t = post(ctx, uc["id"], {"name": "hook", "kind": "webhook", "inputs": {"topic": "base"}})
    token = t["token"]
    assert token.startswith("ft_")
    assert t["next_run_at"] is None
    # No session and no CSRF header: the token is the credential
    anon = new_client(client)
    r = ok(anon.post(f"/api/v1/triggers/{token}", json={"inputs": {"topic": "from hook"}}), 202)
    run = run_of(ctx, r["run_id"])
    assert run["status"] == "succeeded"
    assert run["inputs"] == {"topic": "from hook"}
    assert anon.post("/api/v1/triggers/ft_nope").status_code == 404
    new = ok(b.post(f"{ctx['base']}/triggers/{t['id']}/rotate-token", headers=csrf(b)))
    assert new["token"] != token
    assert anon.post(f"/api/v1/triggers/{token}").status_code == 404
    ok(anon.post(f"/api/v1/triggers/{new['token']}"), 202)
    listed = ok(b.get(f"{ctx['base']}/triggers"))
    assert all(x["token"] is None for x in listed)  # the token is shown once
    ok(
        b.patch(f"{ctx['base']}/triggers/{t['id']}", headers=csrf(b), json={"enabled": False}),
    )
    assert anon.post(f"/api/v1/triggers/{new['token']}").status_code == 404


def test_end_user_cannot_manage_triggers_and_fire_now_works(granted: dict[str, Any]) -> None:
    uc = published(granted)
    t = post(granted, uc["id"], {"name": "x", "kind": "interval", "interval_minutes": 5})
    e = granted["end_user"]
    assert e.get(f"{granted['base']}/triggers").status_code == 403
    r = e.post(f"{granted['base']}/use-cases/{uc['id']}/triggers", headers=csrf(e), json={})
    assert r.status_code == 403
    b = granted["builder"]
    fired = ok(
        b.post(
            f"{granted['base']}/triggers/{t['id']}/fire",
            headers=csrf(b),
            json={"inputs": {"topic": "now"}},
        ),
        202,
    )
    assert run_of(granted, fired["run_id"])["status"] == "succeeded"
    assert b.delete(f"{granted['base']}/triggers/{t['id']}", headers=csrf(b)).status_code == 204


def test_schedule_is_disabled_after_repeated_failures(
    granted: dict[str, Any], client: TestClient
) -> None:
    ctx, b = granted, granted["builder"]
    uc = published(ctx)
    t = post(ctx, uc["id"], {"name": "broken", "kind": "interval", "interval_minutes": 5})
    # Without the required `topic` input every start is refused with 422
    for _ in range(5):
        admin_rows(
            client,
            f"UPDATE use_case_trigger SET next_run_at = now() - interval '1 minute' "
            f"WHERE id = '{t['id']}' RETURNING 1",
        )
        fire_due(client)
    row = next(x for x in ok(b.get(f"{ctx['base']}/triggers")) if x["id"] == t["id"])
    assert row["enabled"] is False
    assert row["failures"] == 5
    assert row["next_run_at"] is None
    assert row["last_status"] == "failed"
    assert "topic" in row["last_error"]
    # Enabling it again resets the counter
    back = ok(b.patch(f"{ctx['base']}/triggers/{t['id']}", headers=csrf(b), json={"enabled": True}))
    assert back["enabled"]
    assert back["failures"] == 0
    assert back["next_run_at"]


def test_inbound_url_is_rate_limited(
    granted: dict[str, Any], client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("forge.usecases.triggers.WEBHOOK_PER_MINUTE", 2)
    uc = published(granted)
    t = post(granted, uc["id"], {"name": "limited", "kind": "webhook", "inputs": {"topic": "x"}})
    anon = new_client(client)
    assert anon.post(f"/api/v1/triggers/{t['token']}").status_code == 202
    assert anon.post(f"/api/v1/triggers/{t['token']}").status_code == 202
    third = anon.post(f"/api/v1/triggers/{t['token']}")
    assert third.status_code == 429
    assert third.json()["code"] == "rate_limited"


def test_trigger_stops_when_its_owner_loses_the_right_to_build(
    granted: dict[str, Any], client: TestClient
) -> None:
    ctx, b = granted, granted["builder"]
    uc = published(ctx)
    schedule = post(ctx, uc["id"], {"name": "s", "kind": "interval", "interval_minutes": 5})
    hook = post(ctx, uc["id"], {"name": "h", "kind": "webhook", "inputs": {"topic": "x"}})
    # Hand both to a user who may only run use cases, as if the Builder had been demoted
    admin_rows(
        client,
        f"UPDATE use_case_trigger SET created_by = '{ctx['end_user_id']}' "
        f"WHERE use_case_id = '{uc['id']}' RETURNING 1",
    )
    admin_rows(
        client,
        f"UPDATE use_case_trigger SET next_run_at = now() - interval '1 minute' "
        f"WHERE id = '{schedule['id']}' RETURNING 1",
    )
    fire_due(client)
    rows = {x["id"]: x for x in ok(b.get(f"{ctx['base']}/triggers"))}
    assert rows[schedule["id"]]["enabled"] is False  # stopped at once, not after five failures
    assert "no longer has access" in rows[schedule["id"]]["last_error"]
    anon = new_client(client)
    refused = anon.post(f"/api/v1/triggers/{hook['token']}")
    assert refused.status_code == 409
    assert refused.json()["code"] == "trigger_owner_lost_access"
    # A Builder who turns it on takes it over and it runs again
    back = ok(
        b.patch(f"{ctx['base']}/triggers/{schedule['id']}", headers=csrf(b), json={"enabled": True})
    )
    assert back["enabled"]
    assert back["next_run_at"]
