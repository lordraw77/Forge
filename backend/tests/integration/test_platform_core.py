"""M1.1 platform core against PostgreSQL and Valkey (US-1.1.x, US-1.2.x, US-1.3.x)."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from forge.api.app import create_app
from forge.core.settings import Settings

MASTER_KEY = "a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2U="
STRONG = "Correct-Horse-Battery-42"


@pytest.fixture(scope="module")
def settings() -> Settings:  # type: ignore[override]
    return Settings(
        database_url=os.environ["FORGE_TEST_DATABASE_URL"],
        redis_url=os.environ.get("FORGE_TEST_REDIS_URL", "redis://127.0.0.1:6379/15"),
        master_key=MASTER_KEY,
        admin_password="Bootstrap-Admin-Pass-1",
        db_pool_size=4,
    )


@pytest.fixture(scope="module")
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    with TestClient(app) as c:
        deadline = time.time() + 20
        while not app.state.initialised and time.time() < deadline:
            time.sleep(0.1)
        assert app.state.initialised, "app did not initialise"
        yield c


@pytest.fixture(autouse=True)
def reset_rate_limit(client: TestClient) -> None:
    client.portal.call(client.app.state.redis.flushdb)  # type: ignore[attr-defined]


def new_client(client: TestClient, headers: dict[str, str] | None = None) -> TestClient:
    """Separate cookie jar, same app and event loop (portal) as the module client."""
    c = TestClient(client.app, headers=headers)
    c.portal = client.portal  # type: ignore[attr-defined]
    return c


def sql(client: TestClient, settings: Settings, statement: str, *, app_role: bool = False) -> Any:
    """Run SQL on a short-lived engine in the app's event loop (owner role unless app_role)."""
    from forge.core.db import create_engine

    async def go() -> Any:
        engine = create_engine(settings, app_role=app_role)
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text(statement))
                return result.all() if result.returns_rows else None
        finally:
            await engine.dispose()

    return client.portal.call(go)  # type: ignore[attr-defined]


def csrf(c: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": c.cookies.get("forge_csrf") or ""}


def create_user(
    client: TestClient, *, super_admin: bool = False, ready: bool = True
) -> dict[str, str]:
    """Create a user directly through the service and optionally clear first-login steps."""
    from forge.core.context import SYSTEM

    name = f"u{uuid.uuid4().hex[:10]}"
    svc = client.app.state.services  # type: ignore[attr-defined]

    async def go() -> uuid.UUID:
        user, _ = await svc.auth.create_user(
            SYSTEM,
            username=name,
            email=f"{name}@example.com",
            full_name=name.title(),
            password=STRONG,
            is_super_admin=super_admin,
        )
        return user.id

    uid = client.portal.call(go)  # type: ignore[attr-defined]
    if ready:
        version = svc.store.snapshot()["system.eula"]["value"]["version"]
        sql(
            client,
            client.app.state.settings,  # type: ignore[attr-defined]
            f"UPDATE app_user SET must_change_password = false, eula_version = '{version}' "
            f"WHERE id = '{uid}'",
        )
    return {"id": str(uid), "username": name, "password": STRONG}


def login(app_client: TestClient, user: dict[str, str]) -> TestClient:
    c = new_client(app_client)
    r = c.post("/api/v1/auth/login", json={"login": user["username"], "password": user["password"]})
    assert r.status_code == 200, r.text
    return c


# ---- authentication (US-1.2.2)
@pytest.mark.ac("US-1.2.2/2")
def test_generic_error_for_wrong_password_and_unknown_user(client: TestClient) -> None:
    u = create_user(client)
    bad = client.post(
        "/api/v1/auth/login", json={"login": u["username"], "password": "wrong-password-x"}
    )
    unknown = client.post(
        "/api/v1/auth/login", json={"login": "nobody-" + u["username"], "password": "x"}
    )
    assert bad.status_code == unknown.status_code == 401
    assert bad.json()["code"] == unknown.json()["code"] == "invalid_credentials"
    assert bad.headers["content-type"] == "application/problem+json"


@pytest.mark.ac("US-1.2.2/3")
def test_lockout_after_five_failures_and_admin_unlock(client: TestClient) -> None:
    u = create_user(client)
    for _ in range(5):
        client.post(
            "/api/v1/auth/login", json={"login": u["username"], "password": "wrong-password-x"}
        )
    locked = client.post(
        "/api/v1/auth/login", json={"login": u["username"], "password": u["password"]}
    )
    assert locked.status_code == 401
    admin = login(client, create_user(client, super_admin=True))
    assert (
        admin.post(f"/api/v1/admin/users/{u['id']}/unlock", headers=csrf(admin)).status_code == 204
    )
    ok = client.post("/api/v1/auth/login", json={"login": u["username"], "password": u["password"]})
    assert ok.status_code == 200


def test_login_rate_limit_per_ip(client: TestClient) -> None:
    codes = [
        client.post("/api/v1/auth/login", json={"login": "rate-test", "password": "x"}).status_code
        for _ in range(21)
    ]
    assert codes[:20] == [401] * 20
    assert codes[20] == 429


@pytest.mark.ac("US-1.2.2/5")
def test_first_login_requires_password_change_then_eula(client: TestClient) -> None:
    u = create_user(client, ready=False)
    c = login(client, u)
    assert c.get("/api/v1/projects").json()["code"] == "password_change_required"
    weak = c.post(
        "/api/v1/auth/password",
        headers=csrf(c),
        json={"current_password": u["password"], "new_password": "unbelievable"},
    )
    assert weak.status_code == 422
    assert weak.json()["code"] == "password_policy"
    reuse = c.post(
        "/api/v1/auth/password",
        headers=csrf(c),
        json={"current_password": u["password"], "new_password": u["password"]},
    )
    assert reuse.status_code == 422
    changed = c.post(
        "/api/v1/auth/password",
        headers=csrf(c),
        json={"current_password": u["password"], "new_password": "Another-Strong-Pass-77"},
    )
    assert changed.status_code == 204
    assert c.get("/api/v1/projects").json()["code"] == "eula_required"
    eula = c.get("/api/v1/auth/eula").json()
    assert (
        c.post("/api/v1/auth/eula", headers=csrf(c), json={"version": eula["version"]}).status_code
        == 204
    )
    assert c.get("/api/v1/projects").status_code == 200
    me = c.get("/api/v1/me").json()
    assert me["flags"] == {
        "password_change_required": False,
        "eula_required": False,
        "totp_enrollment_required": False,
    }


def test_csrf_required_for_cookie_authenticated_writes(client: TestClient) -> None:
    c = login(client, create_user(client))
    r = c.post("/api/v1/auth/eula", json={"version": "1"})
    assert r.status_code == 403
    assert r.json()["code"] == "csrf_failed"


# ---- TOTP (US-1.2.3)
@pytest.mark.ac("US-1.2.3/1")
@pytest.mark.ac("US-1.2.3/2")
def test_totp_enrolment_login_replay_and_recovery_code(client: TestClient) -> None:
    u = create_user(client)
    c = login(client, u)
    secret = c.post("/api/v1/auth/totp/enroll", headers=csrf(c)).json()["secret"]
    totp = pyotp.TOTP(secret)
    codes = c.post("/api/v1/auth/totp/confirm", headers=csrf(c), json={"code": totp.now()}).json()
    assert len(codes["recovery_codes"]) == 10

    fresh = new_client(client)
    first = fresh.post(
        "/api/v1/auth/login", json={"login": u["username"], "password": u["password"]}
    ).json()
    assert first["status"] == "totp_required"
    assert "forge_access" not in fresh.cookies
    # The code used for confirmation is already consumed: replay is rejected.
    replay = fresh.post(
        "/api/v1/auth/login/totp", json={"challenge": first["challenge"], "code": totp.now()}
    )
    assert replay.status_code == 401
    rc = codes["recovery_codes"][0]
    ok = fresh.post(
        "/api/v1/auth/login/totp", json={"challenge": first["challenge"], "recovery_code": rc}
    )
    assert ok.status_code == 200
    assert "forge_access" in fresh.cookies
    again = new_client(client)
    ch = again.post(
        "/api/v1/auth/login", json={"login": u["username"], "password": u["password"]}
    ).json()
    used = again.post(
        "/api/v1/auth/login/totp", json={"challenge": ch["challenge"], "recovery_code": rc}
    )
    assert used.status_code == 401


@pytest.mark.ac("US-1.3.1/1")
def test_totp_secret_is_encrypted_at_rest(client: TestClient, settings: Settings) -> None:
    c = login(client, create_user(client))
    secret = c.post("/api/v1/auth/totp/enroll", headers=csrf(c)).json()["secret"]
    rows = sql(client, settings, "SELECT ciphertext, wrapped_dek FROM secret WHERE kind = 'totp'")
    blobs = [bytes(r[0]) + bytes(r[1]) for r in rows]
    assert blobs
    assert all(secret.encode() not in b for b in blobs)


# ---- sessions
def test_refresh_rotation_and_reuse_detection(client: TestClient) -> None:
    c = login(client, create_user(client))
    old_refresh = c.cookies.get("forge_refresh")
    r = c.post("/api/v1/auth/refresh", headers=csrf(c))
    assert r.status_code == 204
    assert c.cookies.get("forge_refresh") != old_refresh
    assert c.get("/api/v1/me").status_code == 200
    thief = new_client(client)
    thief.cookies.set("forge_refresh", old_refresh or "", path="/api/v1/auth")
    thief.cookies.set("forge_csrf", "t")
    assert thief.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": "t"}).status_code == 401
    # The whole family is revoked: the legitimate session is gone too.
    assert c.get("/api/v1/me").json()["code"] == "session_expired"


def test_logout_revokes_session(client: TestClient) -> None:
    c = login(client, create_user(client))
    access = c.cookies.get("forge_access")
    assert c.post("/api/v1/auth/logout", headers=csrf(c)).status_code == 204
    replay = new_client(client)
    replay.cookies.set("forge_access", access or "")
    assert replay.get("/api/v1/me").status_code == 401


# ---- projects, isolation, API keys (US-1.1.x)
def _project(admin: TestClient, owner_id: str) -> dict[str, Any]:
    r = admin.post(
        "/api/v1/admin/projects",
        headers=csrf(admin),
        json={
            "name": f"Project {uuid.uuid4().hex[:6]}",
            "description": "test",
            "admin_user_id": owner_id,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.ac("US-1.1.1/2")
@pytest.mark.ac("US-1.1.2/2")
def test_project_isolation_and_api_keys(client: TestClient) -> None:
    admin = login(client, create_user(client, super_admin=True))
    alice, bob = create_user(client), create_user(client)
    a = _project(admin, alice["id"])
    b = _project(admin, bob["id"])
    ca, cb = login(client, alice), login(client, bob)

    assert [p["id"] for p in ca.get("/api/v1/projects").json()] == [a["project"]["id"]]
    assert ca.get(f"/api/v1/projects/{b['project']['id']}").status_code == 404
    assert ca.get(f"/api/v1/projects/{b['project']['id']}/members").status_code == 404
    assert cb.get(f"/api/v1/projects/{a['project']['id']}").status_code == 404

    key = a["api_key"]
    as_key = new_client(client, headers={"Authorization": f"Bearer {key}"})
    detail = as_key.get(f"/api/v1/projects/{a['project']['id']}")
    assert detail.status_code == 200
    assert set(detail.json()["permissions"]) == {
        "llm.invoke",
        "usecase.run",
        "run.read",
    }
    assert as_key.get(f"/api/v1/projects/{b['project']['id']}").status_code == 404
    assert as_key.get(f"/api/v1/projects/{a['project']['id']}/members").status_code == 403

    keys = ca.get(f"/api/v1/projects/{a['project']['id']}/api-keys").json()
    assert (
        ca.delete(
            f"/api/v1/projects/{a['project']['id']}/api-keys/{keys[0]['id']}", headers=csrf(ca)
        ).status_code
        == 204
    )
    assert as_key.get(f"/api/v1/projects/{a['project']['id']}").status_code == 401


@pytest.mark.ac("US-1.1.4/1")
@pytest.mark.ac("US-1.1.4/3")
def test_invite_members_and_roles(client: TestClient) -> None:
    admin = login(client, create_user(client, super_admin=True))
    owner, guest = create_user(client), create_user(client)
    p = _project(admin, owner["id"])["project"]["id"]
    co = login(client, owner)
    roles = {r["role_type"]: r["id"] for r in co.get(f"/api/v1/projects/{p}/roles").json()}
    assert set(roles) == {"project_admin", "builder", "operator", "end_user", "viewer"}
    res = co.post(
        f"/api/v1/projects/{p}/members",
        headers=csrf(co),
        json={"identifiers": [guest["username"], "ghost-user"], "role_name_id": roles["end_user"]},
    ).json()
    assert {r["identifier"]: r["status"] for r in res} == {
        guest["username"]: "added",
        "ghost-user": "unknown_user",
    }
    cg = login(client, guest)
    assert cg.get(f"/api/v1/projects/{p}/members").status_code == 403  # end users cannot manage
    detail = cg.get(f"/api/v1/projects/{p}").json()
    assert detail["role_type"] == "end_user"
    assert "project.manage" not in detail["permissions"]
    # The last Project Admin cannot be demoted.
    demote = co.patch(
        f"/api/v1/projects/{p}/members/{owner['id']}",
        headers=csrf(co),
        json={"role_name_id": roles["viewer"]},
    )
    assert demote.status_code == 409
    assert demote.json()["code"] == "last_admin"


@pytest.mark.ac("US-1.1.2/4")
def test_delete_project_requires_reason_and_name(client: TestClient) -> None:
    admin = login(client, create_user(client, super_admin=True))
    owner = create_user(client)
    created = _project(admin, owner["id"])["project"]
    pid, name = created["id"], created["name"]
    bad = admin.post(
        f"/api/v1/admin/projects/{pid}/delete",
        headers=csrf(admin),
        json={"reason": "cleanup old", "confirm_name": "wrong"},
    )
    assert bad.status_code == 422
    ok = admin.post(
        f"/api/v1/admin/projects/{pid}/delete",
        headers=csrf(admin),
        json={"reason": "cleanup old", "confirm_name": name},
    )
    assert ok.status_code == 204
    assert login(client, owner).get("/api/v1/projects").json() == []


# ---- audit (US-1.1.3)
@pytest.mark.ac("US-1.1.3/1")
@pytest.mark.ac("US-1.1.3/2")
def test_audit_records_admin_actions_and_is_append_only(
    client: TestClient, settings: Settings
) -> None:
    admin_user = create_user(client, super_admin=True)
    admin = login(client, admin_user)
    owner = create_user(client)
    pid = _project(admin, owner["id"])["project"]["id"]
    events = admin.get("/api/v1/admin/audit", params={"project_id": pid}).json()
    assert any(
        e["action"] == "project.create" and e["actor_id"] == admin_user["id"] for e in events
    )

    with pytest.raises(Exception, match="permission denied"):
        sql(client, settings, "UPDATE audit_event SET action = 'x'", app_role=True)


# ---- runtime settings (US-1.3.2)
@pytest.mark.ac("US-1.3.2/1")
@pytest.mark.ac("US-1.3.2/2")
def test_setting_change_propagates_to_another_process_via_notify(
    client: TestClient, settings: Settings
) -> None:
    from forge.admin.service import PasswordPolicy, SettingsStore, listen_dsn
    from forge.core.db import create_engine, session_factory

    async def other_process_sees(min_length: int) -> bool:
        engine = create_engine(settings)
        store = SettingsStore(
            session_factory(engine), listen_dsn(settings.database_url.get_secret_value())
        )
        await store.start()
        try:
            for _ in range(40):
                if store.get(PasswordPolicy).min_length == min_length:
                    return True
                await asyncio.sleep(0.05)
            return False
        finally:
            await store.stop()
            await engine.dispose()

    admin = login(client, create_user(client, super_admin=True))
    current = admin.get("/api/v1/admin/settings").json()
    policy = next(s for s in current if s["key"] == "auth.password_policy")
    try:
        r = admin.put(
            "/api/v1/admin/settings/auth.password_policy",
            headers=csrf(admin),
            json={**policy["value"], "min_length": 20},
        )
        assert r.status_code == 200
        assert r.json()["version"] == policy["version"] + 1
        assert client.portal.call(other_process_sees, 20)  # type: ignore[attr-defined]
        bad = admin.put(
            "/api/v1/admin/settings/auth.password_policy",
            headers=csrf(admin),
            json={"min_length": 2},
        )
        assert bad.status_code == 422
        assert bad.json()["code"] == "invalid_setting"
    finally:
        admin.put(
            "/api/v1/admin/settings/auth.password_policy", headers=csrf(admin), json=policy["value"]
        )
