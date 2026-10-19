"""M2.1 external identity: LDAP against an OpenLDAP container, OIDC against a mock provider
(US-2.1.x, AC2)."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from forge.core.settings import Settings
from tests.integration.test_platform_core import (
    _project,
    client,
    create_user,
    csrf,
    login,
    new_client,
    reset_rate_limit,
    settings,
    sql,
)
from tests.support.mock_oidc import MockOidc

LDAP_URL = os.environ.get("FORGE_TEST_LDAP_URL")
pytestmark = [
    pytest.mark.skipif(not LDAP_URL, reason="FORGE_TEST_LDAP_URL not set"),
]

BASE = "dc=example,dc=org"
SETTING = "/api/v1/admin/settings/auth.local_login"
LDAP_CONFIG = {
    "url": LDAP_URL or "ldap://127.0.0.1:3890",
    "allow_insecure": True,
    "bind_dn": f"cn=admin,{BASE}",
    "user_base_dn": f"ou=people,{BASE}",
    "user_filter": "(|(uid={login})(mail={login}))",
    "username_attr": "uid",
    "email_attr": "mail",
    "name_attr": "cn",
    "group_base_dn": f"ou=groups,{BASE}",
    "group_filter": "(member={dn})",
    "group_name_attr": "cn",
}
BIND_PASSWORD = "admin-test-pass"
PASSWORDS = {
    "alice": "alice-pass-1",
    "bob": "bob-pass-1",
    "carol": "carol-pass-1",
    "dave": "dave-pass-1",
}


class World:
    """A Super Admin, two projects and an LDAP provider mapped to them."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.admin = login(client, create_user(client, super_admin=True))
        owner = create_user(client)
        self.p1 = _project(self.admin, owner["id"])["project"]
        self.p2 = _project(self.admin, owner["id"])["project"]
        self.roles = {
            (p["id"], r["role_type"]): r["id"]
            for p in (self.p1, self.p2)
            for r in self.admin.get(f"/api/v1/projects/{p['id']}/roles").json()
        }

    def role(self, project: dict[str, Any], role_type: str) -> str:
        return self.roles[(project["id"], role_type)]

    def provider(self, name: str | None = None, **config: Any) -> dict[str, Any]:
        r = self.admin.post(
            "/api/v1/admin/identity-providers",
            headers=csrf(self.admin),
            json={
                "kind": "ldap",
                "name": name or f"ldap-{uuid.uuid4().hex[:6]}",
                "config": {**LDAP_CONFIG, **config},
                "secret": BIND_PASSWORD,
                "enabled": True,
            },
        )
        assert r.status_code == 201, r.text
        return r.json()

    def map(self, provider: dict[str, Any], group: str, project: dict[str, Any], rt: str) -> str:
        r = self.admin.post(
            f"/api/v1/admin/identity-providers/{provider['id']}/mappings",
            headers=csrf(self.admin),
            json={
                "external_group": group,
                "project_id": project["id"],
                "role_name_id": self.role(project, rt),
            },
        )
        assert r.status_code == 201, r.text
        return str(r.json()["id"])

    def sign_in(self, username: str, password: str | None = None) -> TestClient:
        c = new_client(self.client)
        r = c.post(
            "/api/v1/auth/login",
            json={"login": username, "password": password or PASSWORDS[username]},
        )
        assert r.status_code == 200, r.text
        if r.json()["flags"]["eula_required"]:  # first login of a directory user
            self.accept_eula(c)
        return c

    def accept_eula(self, c: TestClient) -> None:
        version = self.client.app.state.services.store.snapshot()["system.eula"]["value"][  # type: ignore[attr-defined]
            "version"
        ]
        r = c.post("/api/v1/auth/eula", headers=csrf(c), json={"version": version})
        assert r.status_code == 204, r.text


@pytest.fixture
def world(client: TestClient, settings: Settings) -> Iterator[World]:
    sql(client, settings, "DELETE FROM group_mapping")
    sql(
        client,
        settings,
        "DELETE FROM app_user WHERE provider_id IS NOT NULL OR email LIKE '%@example.org'",
    )
    sql(client, settings, "DELETE FROM identity_provider")
    yield World(client)
    # Leave local login on for the next test even if one failed half way.
    sql(client, settings, "DELETE FROM setting WHERE key = 'auth.local_login'")


def project_ids(c: TestClient) -> set[str]:
    return {p["id"] for p in c.get("/api/v1/projects").json()}


@pytest.mark.ac("US-2.1.1/4")
@pytest.mark.ac("US-2.1.1/2")
def test_connection_and_test_user_and_secret_never_returned(world: World) -> None:
    p = world.provider()
    assert "secret" not in str(p).lower() or p["has_secret"] is True
    assert BIND_PASSWORD not in str(world.admin.get("/api/v1/admin/identity-providers").json())

    ok = world.admin.post(
        f"/api/v1/admin/identity-providers/{p['id']}/test", headers=csrf(world.admin)
    ).json()
    assert ok["ok"] is True, ok

    resp = world.admin.post(
        f"/api/v1/admin/identity-providers/{p['id']}/test-user",
        headers=csrf(world.admin),
        json={"login": "alice"},
    )
    assert resp.status_code == 200, resp.text
    found = resp.json()
    assert found["found"] is True
    assert found["email"] == "alice@example.org"
    assert {"forge-builders", "forge-viewers"} <= set(found["groups"])
    missing = world.admin.post(
        f"/api/v1/admin/identity-providers/{p['id']}/test-user",
        headers=csrf(world.admin),
        json={"login": "nobody"},
    ).json()
    assert missing["found"] is False
    # Test user creates nothing.
    rows = sql(
        world.client,
        world.client.app.state.settings,  # type: ignore[attr-defined]
        "SELECT count(*) FROM app_user WHERE username = 'alice'",
    )
    assert rows[0][0] == 0

    bad = world.admin.post(
        "/api/v1/admin/identity-providers",
        headers=csrf(world.admin),
        json={
            "kind": "ldap",
            "name": "insecure",
            "config": {**LDAP_CONFIG, "allow_insecure": False},
            "secret": "x",
        },
    )
    assert bad.status_code == 422


@pytest.mark.ac("US-2.1.2/3")
@pytest.mark.ac("US-2.1.2/4")
@pytest.mark.ac("US-2.1.5/1")
def test_ldap_user_sees_only_mapped_projects(world: World) -> None:  # AC2
    p = world.provider()
    world.map(p, "forge-builders", world.p1, "builder")
    world.map(p, "cn=forge-viewers,ou=groups," + BASE, world.p2, "viewer")

    alice = world.sign_in("alice")  # member of both groups
    assert project_ids(alice) == {world.p1["id"], world.p2["id"]}
    roles = {m["id"]: m["role_type"] for m in alice.get("/api/v1/projects").json()}
    assert roles[world.p1["id"]] == "builder"
    assert roles[world.p2["id"]] == "viewer"
    me = alice.get("/api/v1/me").json()
    assert me["user"]["auth_source"] == "ldap"

    carol = world.sign_in("carol")  # viewers only
    assert project_ids(carol) == {world.p2["id"]}
    assert carol.get(f"/api/v1/projects/{world.p1['id']}").status_code == 404

    bob = world.sign_in("bob")  # no mapped group: signs in, sees nothing
    assert project_ids(bob) == set()

    # No password change or TOTP for directory users.
    r = alice.post(
        "/api/v1/auth/password",
        headers=csrf(alice),
        json={"current_password": "x", "new_password": "Another-Strong-Pass-1"},
    )
    assert r.status_code == 409


@pytest.mark.ac("US-2.1.2/1")
@pytest.mark.ac("US-2.1.2/2")
def test_bad_credentials_are_generic_and_filters_are_injection_safe(world: World) -> None:
    world.provider()
    for login_name, password in (
        ("alice", "wrong"),
        ("alice", ""),
        ("nobody", "alice-pass-1"),
        ("*", "alice-pass-1"),
        ("*)(uid=*))(|(uid=*", "alice-pass-1"),
        ("alice)(uid=bob", "alice-pass-1"),
    ):
        r = new_client(world.client).post(
            "/api/v1/auth/login", json={"login": login_name, "password": password}
        )
        assert r.status_code in (401, 422), (login_name, r.text)
        if r.status_code == 401:
            assert r.json()["code"] == "invalid_credentials"
    # Unknown local user and unknown directory user give the same answer.
    a = new_client(world.client).post(
        "/api/v1/auth/login", json={"login": "nobody", "password": "x"}
    )
    b = new_client(world.client).post(
        "/api/v1/auth/login", json={"login": "alice", "password": "x"}
    )
    assert a.json()["code"] == b.json()["code"] == "invalid_credentials"


@pytest.mark.ac("US-2.1.2/5")
def test_directory_failures_are_counted_per_login(world: World) -> None:
    world.provider()
    for _ in range(5):
        r = new_client(world.client).post(
            "/api/v1/auth/login", json={"login": "dave", "password": "wrong"}
        )
        assert r.status_code == 401
    # Locked: even the right password is refused until the lock expires.
    r = new_client(world.client).post(
        "/api/v1/auth/login", json={"login": "dave", "password": PASSWORDS["dave"]}
    )
    assert r.status_code == 401


@pytest.mark.ac("US-2.1.2/6")
def test_unreachable_directory_does_not_affect_local_users(world: World) -> None:
    world.provider(url="ldap://127.0.0.1:1")
    local = create_user(world.client)
    assert login(world.client, local)
    r = new_client(world.client).post(
        "/api/v1/auth/login", json={"login": "alice", "password": PASSWORDS["alice"]}
    )
    assert r.status_code == 401


@pytest.mark.ac("US-2.1.5/2")
def test_highest_role_wins_when_mappings_disagree(world: World) -> None:
    p = world.provider()
    world.map(p, "forge-viewers", world.p1, "viewer")
    world.map(p, "forge-builders", world.p1, "builder")
    alice = world.sign_in("alice")
    roles = {m["id"]: m["role_type"] for m in alice.get("/api/v1/projects").json()}
    assert roles == {world.p1["id"]: "builder"}


@pytest.mark.ac("US-2.1.5/3")
@pytest.mark.ac("US-2.1.5/4")
def test_mapped_memberships_follow_groups_and_manual_ones_stay(world: World) -> None:
    p = world.provider()
    m1 = world.map(p, "forge-builders", world.p1, "builder")
    world.map(p, "forge-viewers", world.p2, "viewer")
    alice = world.sign_in("alice")
    assert project_ids(alice) == {world.p1["id"], world.p2["id"]}

    # A Project Admin adds alice to P2 by hand with another role.
    r = world.admin.post(
        f"/api/v1/projects/{world.p2['id']}/members",
        headers=csrf(world.admin),
        json={"identifiers": ["alice"], "role_name_id": world.role(world.p2, "operator")},
    )
    assert r.status_code == 200, r.text
    assert r.json()[0]["status"] == "already_member"  # mapped one exists: nothing to add

    # Remove the P1 mapping: the mapped membership goes at the next login.
    assert (
        world.admin.delete(
            f"/api/v1/admin/group-mappings/{m1}", headers=csrf(world.admin)
        ).status_code
        == 204
    )
    alice2 = world.sign_in("alice")
    assert project_ids(alice2) == {world.p2["id"]}
    # The session opened before keeps working but loses access on the next request.
    assert alice.get(f"/api/v1/projects/{world.p1['id']}").status_code == 404

    # A manual membership is never removed by a login or sync.
    added = world.admin.post(
        f"/api/v1/projects/{world.p1['id']}/members",
        headers=csrf(world.admin),
        json={"identifiers": ["alice"], "role_name_id": world.role(world.p1, "viewer")},
    )
    assert added.json()[0]["status"] == "added"
    world.sign_in("alice")
    assert project_ids(alice2) == {world.p1["id"], world.p2["id"]}


@pytest.mark.ac("US-2.1.5/5")
def test_mapping_to_inactive_role_or_foreign_project_is_refused(world: World) -> None:
    p = world.provider()
    r = world.admin.post(
        f"/api/v1/admin/identity-providers/{p['id']}/mappings",
        headers=csrf(world.admin),
        json={
            "external_group": "g",
            "project_id": world.p1["id"],
            "role_name_id": world.role(world.p2, "viewer"),  # belongs to the other project
        },
    )
    assert r.status_code == 422
    world.map(p, "g", world.p1, "viewer")
    dup = world.admin.post(
        f"/api/v1/admin/identity-providers/{p['id']}/mappings",
        headers=csrf(world.admin),
        json={
            "external_group": "G",
            "project_id": world.p1["id"],
            "role_name_id": world.role(world.p1, "viewer"),
        },
    )
    assert dup.status_code == 409


@pytest.mark.ac("US-2.1.6/2")
def test_sync_now_updates_memberships_without_a_login(world: World) -> None:
    p = world.provider()
    world.map(p, "forge-viewers", world.p2, "viewer")
    carol = world.sign_in("carol")
    assert project_ids(carol) == {world.p2["id"]}
    world.map(p, "forge-viewers", world.p1, "viewer")  # new mapping, carol not logged in again
    r = world.admin.post(
        f"/api/v1/admin/identity-providers/{p['id']}/sync", headers=csrf(world.admin)
    )
    assert r.status_code == 200, r.text
    totals = r.json()
    assert totals["seen"] >= 4
    assert totals["added"] >= 1
    assert project_ids(carol) == {world.p1["id"], world.p2["id"]}
    listed = world.admin.get("/api/v1/admin/identity-providers").json()
    assert next(x for x in listed if x["id"] == p["id"])["last_sync"]["seen"] >= 4


@pytest.mark.ac("US-2.1.6/1")
def test_user_gone_from_the_directory_is_disabled_by_sync(world: World) -> None:
    p = world.provider()
    world.map(p, "forge-viewers", world.p2, "viewer")
    carol = world.sign_in("carol")
    uid = carol.get("/api/v1/me").json()["user"]["id"]
    # Point the user at an id the directory no longer knows.
    sql(
        world.client,
        world.client.app.state.settings,  # type: ignore[attr-defined]
        f"UPDATE app_user SET external_id = 'uid=gone,ou=people,{BASE}' WHERE id = '{uid}'",
    )
    r = world.admin.post(
        f"/api/v1/admin/identity-providers/{p['id']}/sync", headers=csrf(world.admin)
    )
    assert r.json()["disabled"] >= 1
    assert carol.get("/api/v1/projects").status_code == 401  # sessions revoked
    again = new_client(world.client).post(
        "/api/v1/auth/login", json={"login": "carol", "password": PASSWORDS["carol"]}
    )
    assert again.status_code == 401


@pytest.mark.ac("US-2.1.4/2")
def test_directory_user_never_takes_over_an_existing_account(world: World) -> None:
    world.provider()
    name = f"u{uuid.uuid4().hex[:8]}"
    svc = world.client.app.state.services  # type: ignore[attr-defined]
    from forge.core.context import SYSTEM

    async def go() -> None:
        await svc.auth.create_user(
            SYSTEM,
            username=name,
            email="dave@example.org",  # same email as the directory entry
            full_name="Local Dave",
            password="Correct-Horse-Battery-42",
            is_super_admin=False,
        )

    world.client.portal.call(go)  # type: ignore[attr-defined]
    r = new_client(world.client).post(
        "/api/v1/auth/login", json={"login": "dave", "password": PASSWORDS["dave"]}
    )
    assert r.status_code == 401


@pytest.mark.ac("US-2.1.7/1")
@pytest.mark.ac("US-2.1.7/2")
def test_local_login_switch_keeps_super_admins_in(world: World) -> None:
    off = {"enabled": False}
    r = world.admin.put(
        "/api/v1/admin/settings/auth.local_login", headers=csrf(world.admin), json=off
    )
    assert r.status_code == 409  # no provider enabled yet

    world.provider()
    r = world.admin.put(
        "/api/v1/admin/settings/auth.local_login", headers=csrf(world.admin), json=off
    )
    assert r.status_code == 200, r.text
    regular = create_user(world.client)
    refused = new_client(world.client).post(
        "/api/v1/auth/login", json={"login": regular["username"], "password": regular["password"]}
    )
    assert refused.status_code == 401
    admin2 = create_user(world.client, super_admin=True)
    assert login(world.client, admin2)
    assert world.sign_in("alice")  # directory users still sign in

    options = new_client(world.client).get("/api/v1/auth/providers").json()
    assert options["local_login"] is True  # LDAP shares the password form

    on = world.admin.put(SETTING, headers=csrf(world.admin), json={"enabled": True})
    assert on.status_code == 200


@pytest.mark.ac("US-2.1.8/1")
def test_audit_covers_providers_logins_and_redacts_secrets(
    world: World, settings: Settings
) -> None:
    p = world.provider()
    world.map(p, "forge-builders", world.p1, "builder")
    world.sign_in("alice")
    rows = []
    for action in (
        "idp.create",
        "idp.mapping_create",
        "idp.memberships_synced",
        "user.provisioned",
    ):
        events = world.admin.get("/api/v1/admin/audit", params={"action": action}).json()
        assert events, action
        rows.extend(events)
    assert BIND_PASSWORD not in str(rows)


@pytest.mark.ac("US-2.1.1/2")
def test_secret_is_encrypted_at_rest(world: World, settings: Settings) -> None:
    world.provider()
    rows = sql(
        world.client,
        settings,
        "SELECT s.ciphertext FROM secret s JOIN identity_provider i ON i.secret_id = s.id",
    )
    assert rows
    assert all(BIND_PASSWORD.encode() not in bytes(r[0]) for r in rows)


# ---- OIDC
@pytest.fixture
def idp() -> Iterator[MockOidc]:
    with MockOidc() as server:
        yield server


def oidc_provider(world: World, idp: MockOidc, **config: Any) -> dict[str, Any]:
    r = world.admin.post(
        "/api/v1/admin/identity-providers",
        headers=csrf(world.admin),
        json={
            "kind": "oidc",
            "name": f"oidc-{uuid.uuid4().hex[:6]}",
            "config": {
                "issuer": idp.issuer,
                "client_id": idp.client_id,
                "allow_insecure": True,
                "button_label": "Sign in with Mock",
                **config,
            },
            "secret": "client-secret-xyz",
            "enabled": True,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def start(world: World, provider: dict[str, Any]) -> tuple[TestClient, dict[str, list[str]]]:
    c = new_client(world.client)
    r = c.get(f"/api/v1/auth/oidc/{provider['id']}/start", follow_redirects=False)
    assert r.status_code == 303, r.text
    target = urlparse(r.headers["location"])
    assert target.path == "/authorize"
    return c, parse_qs(target.query)


@pytest.mark.ac("US-2.1.3/1")
@pytest.mark.ac("US-2.1.3/5")
@pytest.mark.ac("US-2.1.4/1")
def test_oidc_provider_listing_and_discovery_test(world: World, idp: MockOidc) -> None:
    p = oidc_provider(world, idp)
    assert p["redirect_uri"].endswith("/api/v1/auth/oidc/callback")
    ok = world.admin.post(
        f"/api/v1/admin/identity-providers/{p['id']}/test", headers=csrf(world.admin)
    ).json()
    assert ok["ok"] is True
    assert ok["details"]["jwks_keys"] == 1
    public = new_client(world.client).get("/api/v1/auth/providers").json()
    labels = {x["label"] for x in public["providers"]}
    assert "Sign in with Mock" in labels
    assert "client-secret-xyz" not in str(public) + str(
        world.admin.get("/api/v1/admin/identity-providers").json()
    )


@pytest.mark.ac("US-2.1.3/2")
@pytest.mark.ac("US-2.1.4/2")
@pytest.mark.ac("US-2.1.4/3")
@pytest.mark.ac("US-2.1.5/1")
def test_oidc_login_end_to_end_with_pkce_and_group_mapping(world: World, idp: MockOidc) -> None:
    p = oidc_provider(world, idp)
    world.map(p, "team-a", world.p1, "operator")
    c, q = start(world, p)
    assert q["code_challenge_method"] == ["S256"]
    assert q["client_id"] == [idp.client_id]
    code = idp.code(
        q["nonce"][0],
        sub="oidc-user-1",
        preferred_username="olivia",
        email="olivia@example.org",
        name="Olivia",
        groups=["team-a", "other"],
    )
    r = c.get(
        "/api/v1/auth/oidc/callback",
        params={"state": q["state"][0], "code": code},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    # PKCE: the verifier sent to the token endpoint hashes to the challenge.
    import base64
    import hashlib

    digest = hashlib.sha256(idp.last_exchange["code_verifier"].encode()).digest()
    assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == q["code_challenge"][0]
    assert idp.last_exchange["client_secret"] == "client-secret-xyz"

    world.accept_eula(c)  # EULA applies to directory users too (US-2.1.4/3)
    me = c.get("/api/v1/me").json()
    assert me["user"]["username"] == "olivia"
    assert me["user"]["auth_source"] == "oidc"
    assert project_ids(c) == {world.p1["id"]}
    # Same subject again: same user, no duplicate.
    c2, q2 = start(world, p)
    code2 = idp.code(
        q2["nonce"][0],
        sub="oidc-user-1",
        preferred_username="olivia",
        email="olivia@example.org",
        groups=[],
    )
    c2.get("/api/v1/auth/oidc/callback", params={"state": q2["state"][0], "code": code2})
    assert c2.get("/api/v1/me").json()["user"]["id"] == me["user"]["id"]
    assert project_ids(c2) == set()  # group lost: mapped membership gone


@pytest.mark.ac("US-2.1.4/4")
def test_oidc_rejects_bad_tokens_replayed_state_and_foreign_email(
    world: World, idp: MockOidc, settings: Settings
) -> None:
    import time

    p = oidc_provider(world, idp)
    cases: list[dict[str, Any]] = [
        {"_nonce": "wrong"},
        {"_aud": "another-client"},
        {"_exp": int(time.time()) - 3600},
        {"_sign_with_other_key": True},
        {"_iss": "https://evil.example.org"},
    ]
    for bad in cases:
        c, q = start(world, p)
        claims = {"nonce": q["nonce"][0], "sub": "s-bad", "email": "bad@example.org", **bad}
        code = idp.code(claims.pop("nonce"), **claims)
        r = c.get(
            "/api/v1/auth/oidc/callback",
            params={"state": q["state"][0], "code": code},
            follow_redirects=False,
        )
        assert r.headers["location"] == "/login?error=oidc_failed", bad
        assert c.get("/api/v1/me").status_code == 401

    # A state is single use.
    c, q = start(world, p)
    code = idp.code(q["nonce"][0], sub="s-ok", preferred_username="x1", email="x1@example.org")
    ok = c.get(
        "/api/v1/auth/oidc/callback",
        params={"state": q["state"][0], "code": code},
        follow_redirects=False,
    )
    assert ok.headers["location"] == "/"
    again = new_client(world.client).get(
        "/api/v1/auth/oidc/callback",
        params={"state": q["state"][0], "code": idp.code(q["nonce"][0], sub="s-ok")},
        follow_redirects=False,
    )
    assert again.headers["location"] == "/login?error=oidc_failed"
    unknown = new_client(world.client).get(
        "/api/v1/auth/oidc/callback", params={"state": "nope", "code": "x"}, follow_redirects=False
    )
    assert unknown.headers["location"] == "/login?error=oidc_failed"

    # An email that belongs to a local user is never merged.
    local = create_user(world.client)
    c, q = start(world, p)
    code = idp.code(
        q["nonce"][0],
        sub="s-takeover",
        preferred_username="mallory",
        email=f"{local['username']}@example.com",
    )
    r = c.get(
        "/api/v1/auth/oidc/callback",
        params={"state": q["state"][0], "code": code},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/login?error=oidc_failed"
    assert c.get("/api/v1/me").status_code == 401

    failed = world.admin.get("/api/v1/admin/audit", params={"action": "auth.oidc_failed"}).json()
    clash = world.admin.get(
        "/api/v1/admin/audit", params={"action": "auth.external_conflict"}
    ).json()
    assert len(failed) >= len(cases) + 2  # bad tokens, replayed state, unknown state
    assert len(clash) >= 1
