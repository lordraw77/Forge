# ruff: noqa: F811  (module fixtures imported from test_gateway are redefined as parameters)
"""E3.8 external vault (OpenBao Transit) as the key-encryption key, against a real OpenBao in
dev mode, PostgreSQL and the console API (US-3.8.x). Needs docker."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from forge.admin import secrets
from forge.admin.models import Secret
from forge.core.crypto import BUILTIN_KEK_ID, Envelope, KekUnavailable
from forge.core.db import admin_scope
from forge.core.vault import OpenBaoKek, VaultConfig
from tests.integration.test_gateway import (  # noqa: F401 - module fixtures
    admin,
    clean,
    client,
    mock,
    ok,
    provider,
    settings,
)
from tests.integration.test_platform_core import MASTER_KEY, csrf
from tests.support.openbao import Bao, openbao

pytestmark = pytest.mark.skipif(
    not Path("/var/run/docker.sock").exists(), reason="needs docker for OpenBao"
)
ADMIN = "/api/v1/admin"


@pytest.fixture(scope="module")
def bao() -> Iterator[Bao]:
    with openbao() as b:
        yield b


def kek(bao: Bao, **kw: Any) -> OpenBaoKek:
    cfg = (
        VaultConfig(url=bao.url, token=bao.token)
        if "auth_method" not in kw
        else VaultConfig(url=bao.url, **kw)
    )
    return OpenBaoKek(cfg)


@pytest.mark.ac("US-3.8.1/1")
@pytest.mark.ac("US-3.8.1/3")
def test_envelope_wraps_data_keys_in_the_vault_and_survives_key_rotation(bao: Bao) -> None:
    env = Envelope.from_master_key(MASTER_KEY)
    remote = kek(bao)
    env.attach(remote, make_current=True)
    assert env.current == "openbao:forge"
    sealed = env.seal(b"provider-key", b"aad")
    assert sealed.kek_id == "openbao:forge"
    assert sealed.wrapped_dek.startswith(b"vault:v1:")  # the data key never sits here in clear
    assert env.open(sealed, b"aad") == b"provider-key"
    # The vault's key is rotated: old data keys still open, new ones use version 2.
    bao.api("POST", "transit/keys/forge/rotate").raise_for_status()
    assert env.open(sealed, b"aad") == b"provider-key"
    fresh = env.seal(b"second", b"aad")
    assert fresh.wrapped_dek.startswith(b"vault:v2:")
    # Re-wrap moves a secret between KEKs without touching its ciphertext.
    builtin = Envelope.from_master_key(MASTER_KEY)
    legacy = builtin.seal(b"old", b"aad")
    assert legacy.kek_id == BUILTIN_KEK_ID
    moved = env.rewrap(legacy)
    assert moved.kek_id == "openbao:forge"
    assert moved.ciphertext == legacy.ciphertext
    assert env.open(moved, b"aad") == b"old"
    env.use_builtin()
    assert env.rewrap(moved).kek_id == BUILTIN_KEK_ID
    remote.close()


@pytest.mark.ac("US-3.8.1/2")
def test_approle_login_renews_and_a_bad_token_is_refused(bao: Bao) -> None:
    env = Envelope.from_master_key(MASTER_KEY)
    role = kek(bao, auth_method="approle", role_id=bao.role_id, secret_id=bao.secret_id)
    env.attach(role, make_current=True)
    sealed = env.seal(b"x", b"a")
    time.sleep(3)  # the AppRole token lives 2 s in this test: a new login happens by itself
    assert env.open(sealed, b"a") == b"x"
    role.close()
    bad = OpenBaoKek(VaultConfig(url=bao.url, token="not-a-token"))
    with pytest.raises(KekUnavailable):
        bad.wrap(b"k" * 32)
    bad.close()


@pytest.mark.ac("US-3.8.1/4")
def test_unreachable_vault_fails_closed_but_cached_keys_keep_working() -> None:
    with openbao() as local:
        env = Envelope.from_master_key(MASTER_KEY)
        env.attach(kek(local), make_current=True)
        sealed = env.seal(b"value", b"a")
        assert env.open(sealed, b"a") == b"value"  # now cached
        local.stop()
        assert env.open(sealed, b"a") == b"value"  # a few minutes of grace from the cache
        with pytest.raises(KekUnavailable):
            env.seal(b"new", b"a")
        other = Envelope.from_master_key(MASTER_KEY)  # a process without the vault attached
        other_sealed = sealed
        with pytest.raises(KekUnavailable):
            other.open(other_sealed, b"a")


def _all_secrets(client: TestClient) -> dict[str, str]:
    app = client.app
    svc = app.state.services  # type: ignore[attr-defined]

    async def go() -> dict[str, str]:
        out: dict[str, str] = {}
        async with admin_scope(app.state.sessions) as s:  # type: ignore[attr-defined]
            for sid in (await s.execute(select(Secret.id))).scalars().all():
                out[str(sid)] = await secrets.get(s, svc.envelope, sid)
        return out

    return client.portal.call(go)  # type: ignore[attr-defined, no-any-return]


@pytest.mark.ac("US-3.8.2/1")
@pytest.mark.ac("US-3.8.2/2")
@pytest.mark.ac("US-3.8.2/3")
def test_console_enables_the_vault_rewraps_and_switches_back(
    admin: TestClient,
    client: TestClient,
    mock: str,
    bao: Bao,
) -> None:
    provider(admin, mock)  # a provider API key is a secret
    before = _all_secrets(client)
    assert before
    status = ok(admin.get(f"{ADMIN}/vault"))
    assert status["config"]["enabled"] is False
    assert set(status["secrets_by_kek"]) <= {BUILTIN_KEK_ID}

    body = {
        "enabled": True,
        "active": True,
        "url": bao.url,
        "mount": "transit",
        "key_name": "forge",
        "auth_method": "token",
        "credential": bao.token,
    }
    try:
        # The settings are checked with a real round trip before anything is saved.
        bad = admin.put(f"{ADMIN}/vault", headers=csrf(admin), json=body | {"credential": "nope"})
        assert bad.status_code == 422
        assert bad.json()["code"] == "vault_check_failed"
        assert ok(admin.get(f"{ADMIN}/vault"))["config"]["enabled"] is False
        on = ok(admin.put(f"{ADMIN}/vault", headers=csrf(admin), json=body))
        assert on["config"]["enabled"] is True
        assert on["config"]["has_credential"] is True
        assert bao.token not in str(on)  # the credential is never returned
        assert on["current_kek"] == "openbao:forge"
        check = ok(admin.post(f"{ADMIN}/vault/test", headers=csrf(admin)))
        assert check["ok"] is True
        assert check["detail"]["type"] == "aes256-gcm96"

        done = ok(admin.post(f"{ADMIN}/vault/rewrap", headers=csrf(admin)))
        # Every secret but the vault's own credential moved; that one stays under the built-in key.
        assert done["secrets_by_kek"]["openbao:forge"] == len(before)
        assert done["secrets_by_kek"][BUILTIN_KEK_ID] == 1
        after = _all_secrets(client)
        for sid, value in before.items():
            assert after[sid] == value  # every value reads back the same through the vault

        # Cannot be switched off while secrets depend on it.
        off = admin.put(
            f"{ADMIN}/vault",
            headers=csrf(admin),
            json=body | {"enabled": False, "credential": None},
        )
        assert off.status_code == 409
        assert off.json()["code"] == "secrets_use_vault"
        # Back: stop writing to the vault, re-wrap to the built-in key, then disable.
        ok(
            admin.put(
                f"{ADMIN}/vault",
                headers=csrf(admin),
                json=body | {"active": False, "credential": None},
            )
        )
        back = ok(admin.post(f"{ADMIN}/vault/rewrap", headers=csrf(admin)))
        assert set(back["secrets_by_kek"]) == {BUILTIN_KEK_ID}
        final = ok(
            admin.put(
                f"{ADMIN}/vault",
                headers=csrf(admin),
                json={"enabled": False, "credential": ""},
            )
        )
        assert final["config"]["enabled"] is False
        assert final["current_kek"] == BUILTIN_KEK_ID
        assert set(before) <= set(_all_secrets(client))
    finally:
        # Whatever happened, leave the shared test database on the built-in key.
        admin.put(
            f"{ADMIN}/vault", headers=csrf(admin), json=body | {"active": False, "credential": None}
        )
        admin.post(f"{ADMIN}/vault/rewrap", headers=csrf(admin))
        admin.put(f"{ADMIN}/vault", headers=csrf(admin), json={"enabled": False, "credential": ""})
