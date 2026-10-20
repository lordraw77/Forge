"""Plain-text email over SMTP for approval notifications (M2.4). Sending never raises: a mail
server that is down must not stop a run, and the Approvals page works without email."""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage

from forge.core.logging import get_logger
from forge.core.settings import Settings

log = get_logger(__name__)
TIMEOUT_S = 10


class Mailer:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    @property
    def enabled(self) -> bool:
        return bool(self._s.smtp_host)

    def _send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self._s.smtp_from, to, subject
        msg.set_content(body)
        with smtplib.SMTP(self._s.smtp_host, self._s.smtp_port, timeout=TIMEOUT_S) as smtp:
            if self._s.smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if self._s.smtp_username:
                password = self._s.smtp_password.get_secret_value() if self._s.smtp_password else ""
                smtp.login(self._s.smtp_username, password)
            smtp.send_message(msg)

    async def send(self, to: str, subject: str, body: str) -> bool:
        if not self.enabled or not to:
            return False
        try:
            await asyncio.to_thread(self._send, to, subject, body)
        except Exception as exc:
            log.warning("mail.failed", to=to, error=str(exc)[:200])
            return False
        return True
