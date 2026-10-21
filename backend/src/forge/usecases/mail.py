"""Plain-text email over SMTP for approval notifications (M2.4). Sending never raises: a mail
server that is down must not stop a run, and the Approvals page works without email.

Settings come from the console (Super Admin → Email, stored as the `notifications.smtp` setting
with the password in the secret store); when no host is set there, the `FORGE_SMTP_*`
environment values apply, so installs configured before the screen existed keep working."""

from __future__ import annotations

import asyncio
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from email.message import EmailMessage

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import SettingsStore, SmtpSettings, secrets
from forge.core.crypto import Envelope
from forge.core.db import project_scope
from forge.core.logging import get_logger
from forge.core.settings import Settings

log = get_logger(__name__)
TIMEOUT_S = 10


@dataclass
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    sender: str
    starttls: bool
    source: str  # "console" | "environment"


class Mailer:
    def __init__(
        self,
        settings: Settings,
        store: SettingsStore | None = None,
        sessions: async_sessionmaker[AsyncSession] | None = None,
        envelope: Envelope | None = None,
    ) -> None:
        self._s = settings
        self._store = store
        self._sessions = sessions
        self._envelope = envelope

    def _console(self) -> SmtpSettings | None:
        cfg = self._store.get(SmtpSettings) if self._store else None
        return cfg if cfg and cfg.host else None

    @property
    def enabled(self) -> bool:
        return bool(self._console() or self._s.smtp_host)

    def source(self) -> str:
        return "console" if self._console() else ("environment" if self._s.smtp_host else "none")

    async def config(self) -> SmtpConfig | None:
        if (c := self._console()) is not None:
            password = ""
            if c.password_secret_id and self._sessions and self._envelope:
                async with project_scope(self._sessions, None) as s:
                    password = await secrets.get(s, self._envelope, uuid.UUID(c.password_secret_id))
            return SmtpConfig(
                c.host, c.port, c.username, password, c.from_address, c.starttls, "console"
            )
        if self._s.smtp_host:
            env_password = self._s.smtp_password.get_secret_value() if self._s.smtp_password else ""
            return SmtpConfig(
                self._s.smtp_host,
                self._s.smtp_port,
                self._s.smtp_username,
                env_password,
                self._s.smtp_from,
                self._s.smtp_starttls,
                "environment",
            )
        return None

    @staticmethod
    def _send(cfg: SmtpConfig, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = cfg.sender, to, subject
        msg.set_content(body)
        with smtplib.SMTP(cfg.host, cfg.port, timeout=TIMEOUT_S) as smtp:
            if cfg.starttls:
                smtp.starttls(context=ssl.create_default_context())
            if cfg.username:
                smtp.login(cfg.username, cfg.password)
            smtp.send_message(msg)

    async def send_checked(self, to: str, subject: str, body: str) -> None:
        """Like `send` but raises, for the console's test button."""
        cfg = await self.config()
        if cfg is None:
            raise RuntimeError("No mail server is configured")
        await asyncio.to_thread(self._send, cfg, to, subject, body)

    async def send(self, to: str, subject: str, body: str) -> bool:
        if not self.enabled or not to:
            return False
        try:
            await self.send_checked(to, subject, body)
        except Exception as exc:
            log.warning("mail.failed", to=to, error=str(exc)[:200])
            return False
        return True
