"""AC11: a fresh install with only the `.env` bootstrap values, everything else from the GUI's
API, no restart.

The database is empty and migrated by `forge migrate` (scripts/test-integration.sh creates it),
the app is created once with nothing but the database, cache, master key and admin password,
and every step below runs against that same running app.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from forge.api.app import create_app
from forge.core.settings import Settings
from tests.integration.test_gateway import alias, model, ok, provider, sdk
from tests.integration.test_identity import BIND_PASSWORD, LDAP_CONFIG, PASSWORDS
from tests.integration.test_platform_core import MASTER_KEY, csrf, new_client
from tests.support.mock_provider import MockProvider

FRESH_URL = os.environ.get("FORGE_TEST_FRESH_DATABASE_URL")
pytestmark = pytest.mark.skipif(not FRESH_URL, reason="FORGE_TEST_FRESH_DATABASE_URL not set")

BOOTSTRAP_PASSWORD = "Bootstrap-Admin-Pass-1"
NEW_PASSWORD = "A-Much-Better-Passphrase-2"


@pytest.fixture(scope="module")
def app_client() -> Iterator[TestClient]:
    assert FRESH_URL
    settings = Settings(  # the bootstrap values and nothing else
        database_url=FRESH_URL,
        redis_url=os.environ.get("FORGE_TEST_REDIS_URL", "redis://127.0.0.1:6379/13"),
        master_key=MASTER_KEY,
        admin_password=BOOTSTRAP_PASSWORD,
        db_pool_size=4,
    )
    app = create_app(settings, gateway_backoff_s=0.01)
    with TestClient(app) as c:
        deadline = time.time() + 30
        while not app.state.initialised and time.time() < deadline:
            time.sleep(0.1)
        assert app.state.initialised
        c.portal.call(app.state.redis.flushdb)  # type: ignore[attr-defined]
        yield c


def test_fresh_install_is_configured_from_the_gui_without_restart(app_client: TestClient) -> None:
    c = new_client(app_client)

    # 1. The bootstrap admin is the only user; it must set its own password and accept the terms.
    r = c.post("/api/v1/auth/login", json={"login": "admin", "password": BOOTSTRAP_PASSWORD})
    assert r.status_code == 200, r.text
    flags = r.json()["flags"]
    assert flags["password_change_required"] or flags.get("must_change_password")
    ok(
        c.post(
            "/api/v1/auth/password",
            headers=csrf(c),
            json={"current_password": BOOTSTRAP_PASSWORD, "new_password": NEW_PASSWORD},
        ),
        204,
    )
    eula = ok(c.get("/api/v1/auth/eula"))
    ok(c.post("/api/v1/auth/eula", headers=csrf(c), json={"version": eula["version"]}), 204)
    me = ok(c.get("/api/v1/me"))
    assert me["user"]["is_super_admin"] is True

    # 2. A model provider, a model and an alias, through the admin API (AC1).
    with MockProvider() as mock:
        prov = provider(c, mock)
        m = model(c, prov["id"], "gpt-echo", all_projects=True)
        tested = ok(c.post(f"/api/v1/admin/models/{m['id']}/test", headers=csrf(c)))
        assert tested["ok"] is True
        alias(c, "default-chat", [m["id"]])

        # 3. A project with its Project Admin and an API key; the key calls the model at once.
        owner = ok(
            c.post(
                "/api/v1/admin/users",
                headers=csrf(c),
                json={
                    "username": "fresh-owner",
                    "email": "owner@example.com",
                    "full_name": "Fresh Owner",
                },
            ),
            201,
        )
        created = ok(
            c.post(
                "/api/v1/admin/projects",
                headers=csrf(c),
                json={"name": "Fresh", "description": "", "admin_user_id": owner["user"]["id"]},
            ),
            201,
        )
        answer = sdk(app_client, created["api_key"]).chat.completions.create(
            model="default-chat", messages=[{"role": "user", "content": "hello"}]
        )
        assert answer.choices[0].message.content

        # 4. A runtime setting changes behaviour on the running app (ADR-007).
        ok(
            c.put(
                "/api/v1/admin/settings/gateway.limits",
                headers=csrf(c),
                json={"requests_per_minute": 1, "tokens_per_day": 0},
            )
        )
        key = new_client(app_client, headers={"Authorization": f"Bearer {created['api_key']}"})
        body = {"model": "default-chat", "messages": [{"role": "user", "content": "x"}]}
        codes = {key.post("/v1/chat/completions", json=body).status_code for _ in range(3)}
        assert 429 in codes  # the new limit applies without a restart (AC10)
        ok(
            c.put(
                "/api/v1/admin/settings/gateway.limits",
                headers=csrf(c),
                json={"requests_per_minute": 0, "tokens_per_day": 0},
            )
        )

    # 5. LDAP: configured in the GUI, usable by the next sign-in.
    ldap = ok(
        c.post(
            "/api/v1/admin/identity-providers",
            headers=csrf(c),
            json={
                "kind": "ldap",
                "name": "directory",
                "config": LDAP_CONFIG,
                "secret": BIND_PASSWORD,
                "enabled": True,
            },
        ),
        201,
    )
    assert ldap["id"]
    d = new_client(app_client)
    login = d.post("/api/v1/auth/login", json={"login": "alice", "password": PASSWORDS["alice"]})
    assert login.status_code == 200, login.text
