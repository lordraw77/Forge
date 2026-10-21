"""SMTP settings from the console: stored with an encrypted password, applied without restart,
tested with a message (closes the SMTP gap of AC11)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from forge.admin.service import SmtpSettings
from tests.integration.test_gateway import ADMIN, admin, client, ok, settings
from tests.integration.test_platform_core import csrf, sql
from tests.support.smtp_sink import SmtpSink

__all__ = ["admin", "client", "settings"]

URL = f"{ADMIN}/notifications/smtp"


@pytest.fixture
def sink() -> Iterator[SmtpSink]:
    with SmtpSink() as s:
        yield s


def test_smtp_is_configured_tested_and_the_password_stays_secret(
    admin: TestClient, client: TestClient, sink: SmtpSink, settings
) -> None:  # type: ignore[no-untyped-def]
    before = ok(admin.get(URL))
    assert before["source"] in ("none", "environment")

    body = {
        "host": "127.0.0.1",
        "port": sink.port,
        "username": "",
        "from_address": "forge@corp.example",
        "starttls": False,
        "password": "s3cret-smtp-password",
    }
    saved = ok(admin.put(URL, headers=csrf(admin), json=body))
    assert saved["source"] == "console"
    assert saved["has_password"] is True
    assert "s3cret" not in str(saved)

    # The running app uses it at once, and the password is stored encrypted.
    sent = ok(admin.post(f"{URL}/test", headers=csrf(admin), json={"to": "qa@example.com"}))
    assert sent == {"ok": True, "error": None}
    assert sink.messages[-1]["From"] == "forge@corp.example"
    assert sink.messages[-1]["X-Rcpt"] == "qa@example.com"
    stored = sql(
        client, settings, "SELECT value::text FROM setting WHERE key = 'notifications.smtp'"
    )
    assert "s3cret" not in str(stored)
    cipher = sql(
        client,
        settings,
        "SELECT encode(ciphertext, 'escape') FROM secret WHERE kind = 'notifications.smtp'",
    )
    assert "s3cret" not in str(cipher)
    mailer = client.app.state.services.mailer  # type: ignore[attr-defined]
    cfg = client.portal.call(mailer.config)  # type: ignore[attr-defined]
    assert cfg.password == "s3cret-smtp-password"

    # Saving without a password keeps it; an empty one removes it.
    kept = ok(admin.put(URL, headers=csrf(admin), json=body | {"password": None}))
    assert kept["has_password"] is True
    cleared = ok(admin.put(URL, headers=csrf(admin), json=body | {"password": ""}))
    assert cleared["has_password"] is False

    # A dead server is reported, not raised.
    ok(admin.put(URL, headers=csrf(admin), json=body | {"port": 1, "password": None}))
    failed = ok(admin.post(f"{URL}/test", headers=csrf(admin), json={"to": "qa@example.com"}))
    assert failed["ok"] is False
    assert failed["error"]

    # The generic settings route can neither list nor write it.
    keys = {s["key"] for s in ok(admin.get(f"{ADMIN}/settings"))}
    assert "notifications.smtp" not in keys
    r = admin.put(
        f"{ADMIN}/settings/notifications.smtp",
        headers=csrf(admin),
        json={"host": "evil.example", "password_secret_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert r.status_code == 409

    # Back to "not configured" so other tests see the environment values.
    ok(admin.put(URL, headers=csrf(admin), json={"host": ""}))
    assert client.app.state.services.store.get(SmtpSettings).host == ""  # type: ignore[attr-defined]
