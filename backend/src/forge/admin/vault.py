"""Connects a process's `Envelope` to the external vault chosen in the console (E3.8).

Every process that decrypts secrets (api, workers) runs one `VaultLink`: it reads the
`secrets.vault` setting at start and again whenever it changes (the settings store notifies all
processes), attaches the vault KEK to the envelope and, when the vault is *active*, makes it the
KEK for new and re-wrapped secrets. It must run before anything decrypts a secret.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin import secrets
from forge.admin.models import Secret
from forge.admin.settings_store import SettingsStore, VaultSettings
from forge.core.crypto import BUILTIN_KEK_ID, Envelope
from forge.core.db import admin_scope
from forge.core.vault import OpenBaoKek, VaultConfig

log = structlog.get_logger()
CREDENTIAL_KIND = "secrets.vault"


def config_from(cfg: VaultSettings, credential: dict[str, str]) -> VaultConfig:
    return VaultConfig(
        url=cfg.url,
        mount=cfg.mount,
        key_name=cfg.key_name,
        namespace=cfg.namespace,
        auth_method=cfg.auth_method,
        token=credential.get("token", ""),
        role_id=cfg.role_id,
        secret_id=credential.get("secret_id", ""),
        ca_pem=cfg.ca_pem,
    )


class VaultLink:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        envelope: Envelope,
        store: SettingsStore,
    ) -> None:
        self._sessions = sessions
        self._envelope = envelope
        self._store = store
        self._kek: OpenBaoKek | None = None

    async def start(self) -> None:
        self._store.subscribe(self._changed)
        await self.apply()

    async def _changed(self, key: str) -> None:
        if key in ("secrets.vault", "*"):
            await self.apply()

    async def credential(self, cfg: VaultSettings) -> dict[str, str]:
        if not cfg.credential_secret_id:
            return {}
        async with admin_scope(self._sessions) as s:
            raw = await secrets.get(s, self._envelope, uuid.UUID(cfg.credential_secret_id))
        value: dict[str, str] = json.loads(raw)
        return value

    async def apply(self) -> None:
        cfg = self._store.get(VaultSettings)
        old = self._kek
        if not (cfg.enabled and cfg.url):
            if old:
                self._envelope.detach(old.kek_id)
                old.close()
                self._kek = None
            self._envelope.use_builtin()
            return
        try:
            kek = OpenBaoKek(config_from(cfg, await self.credential(cfg)))
        except Exception as exc:
            log.warning("vault.config_unusable", error=str(exc))
            return
        if old and old.kek_id != kek.kek_id:
            self._envelope.detach(old.kek_id)
        self._envelope.attach(kek, make_current=cfg.active)
        if not cfg.active and self._envelope.current != BUILTIN_KEK_ID:
            self._envelope.use_builtin()
        self._kek = kek
        if old:
            old.close()
        log.info("vault.attached", kek=kek.kek_id, active=cfg.active)

    async def usage(self) -> dict[str, int]:
        """How many secrets sit under each KEK."""
        async with admin_scope(self._sessions) as s:
            rows = (
                await s.execute(select(Secret.kek_id, func.count()).group_by(Secret.kek_id))
            ).all()
        return {str(k): int(n) for k, n in rows}

    @property
    def attached(self) -> bool:
        return self._kek is not None

    @property
    def envelope(self) -> Envelope:
        return self._envelope

    async def rewrap_all(self) -> int:
        async with admin_scope(self._sessions) as s:
            return await secrets.rotate_all(s, self._envelope)


def describe(cfg: VaultSettings) -> dict[str, Any]:
    return cfg.model_dump(exclude={"credential_secret_id", "ca_pem"}) | {
        "has_credential": cfg.credential_secret_id is not None,
        "has_ca": bool(cfg.ca_pem),
    }
