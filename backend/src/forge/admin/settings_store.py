"""Runtime application settings: typed, versioned, audited, live-reloaded (ADR-007)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypeVar

import asyncpg
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin import audit
from forge.admin.models import Setting, SettingHistory
from forge.core.context import Actor
from forge.core.db import project_scope
from forge.core.logging import get_logger

log = get_logger(__name__)
CHANNEL = "forge_config"


class PasswordPolicy(BaseModel):
    min_length: int = Field(default=12, ge=8, le=128)
    history: int = Field(default=5, ge=0, le=24)
    block_common: bool = True


class LockoutPolicy(BaseModel):
    threshold: int = Field(default=5, ge=3, le=50)
    window_min: int = Field(default=15, ge=1, le=1440)
    lock_min: int = Field(default=15, ge=1, le=1440)


class SessionPolicy(BaseModel):
    idle_min: int = Field(default=30, ge=5, le=1440)
    absolute_h: int = Field(default=12, ge=1, le=168)


class TotpPolicy(BaseModel):
    required: bool = False


class LocalLogin(BaseModel):
    """When off, only Super Admins sign in with a local password (US-2.1.7)."""

    enabled: bool = True


class Eula(BaseModel):
    version: str = "1"
    text: str = (
        "Forge is provided for authorised use within your organisation. Activity is logged. "
        "Do not upload data you are not permitted to process."
    )


class GatewayLimits(BaseModel):
    """Platform defaults and ceilings for project LLM usage (F1.6, US-1.7.1). 0 = unlimited."""

    tokens_per_day: int = Field(default=0, ge=0)
    requests_per_minute: int = Field(default=0, ge=0)
    user_tokens_per_day: int = Field(default=0, ge=0)
    user_requests_per_minute: int = Field(default=0, ge=0)
    max_tokens_per_call: int = Field(default=0, ge=0)
    max_tokens_policy: Literal["clamp", "reject"] = "clamp"
    cache_ttl_max_s: int = Field(default=86400, ge=1, le=2_592_000)


class ActivityRetention(BaseModel):
    """How far back dashboards and the activity log read (E2.14). Nothing is deleted."""

    days: int = Field(default=180, ge=1, le=3650)


class SmtpSettings(BaseModel):
    """Outgoing mail for approval notifications, set from the console (Super Admin → Email).
    The password lives in the encrypted secret store; this row only points at it. Empty host =
    use the FORGE_SMTP_* environment values, if any. Not editable through the generic
    settings route (it could point at any secret)."""

    host: str = Field(default="", max_length=255)
    port: int = Field(default=587, ge=1, le=65535)
    username: str = Field(default="", max_length=255)
    from_address: str = Field(default="forge@localhost", max_length=255)
    starttls: bool = True
    password_secret_id: str | None = None


class VaultSettings(BaseModel):
    """External vault for the key-encryption key (ADR-006, E3.8). The credential (token or
    AppRole secret id) is in the secret store, under the built-in KEK, never here."""

    enabled: bool = False
    # New and re-wrapped secrets use the vault; false keeps it readable but writes built-in.
    active: bool = False
    url: str = ""
    mount: str = "transit"
    key_name: str = "forge"
    namespace: str = ""
    auth_method: str = "token"  # token | approle
    role_id: str = ""
    ca_pem: str = ""
    credential_secret_id: str | None = None


REGISTRY: dict[str, type[BaseModel]] = {
    "auth.password_policy": PasswordPolicy,
    "auth.lockout": LockoutPolicy,
    "auth.session": SessionPolicy,
    "auth.totp": TotpPolicy,
    "auth.local_login": LocalLogin,
    "system.eula": Eula,
    "gateway.limits": GatewayLimits,
    "activity.retention": ActivityRetention,
    "notifications.smtp": SmtpSettings,
    "secrets.vault": VaultSettings,
}
# Settings with their own endpoints: absent from the generic list and not writable through it.
DEDICATED = frozenset({"notifications.smtp", "secrets.vault"})

M = TypeVar("M", bound=BaseModel)


class UnknownSettingError(KeyError):
    pass


class SettingsStore:
    """Process-wide cache of platform settings; reloads on NOTIFY from any process."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], listen_dsn: str | None) -> None:
        self._sessions = sessions
        self._dsn = listen_dsn
        self._values: dict[str, BaseModel] = {k: cls() for k, cls in REGISTRY.items()}
        self._versions: dict[str, int] = {}
        self._task: asyncio.Task[None] | None = None
        self._subscribers: list[Callable[[str], Awaitable[None]]] = []

    def subscribe(self, callback: Callable[[str], Awaitable[None]]) -> None:
        """Call `callback(key)` for every notification on the config channel, including keys
        that are not settings (e.g. `gateway`), and once more after a listener reconnect."""
        self._subscribers.append(callback)

    def get(self, cls: type[M]) -> M:
        for key, model in REGISTRY.items():
            if model is cls:
                value = self._values[key]
                assert isinstance(value, cls)
                return value
        raise UnknownSettingError(cls.__name__)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {
            k: {"value": v.model_dump(), "version": self._versions.get(k, 0)}
            for k, v in self._values.items()
        }

    async def reload(self, key: str | None = None) -> None:
        async with project_scope(self._sessions, None) as session:
            stmt = select(Setting).where(Setting.project_id.is_(None))
            if key:
                stmt = stmt.where(Setting.key == key)
            for row in (await session.execute(stmt)).scalars():
                cls = REGISTRY.get(row.key)
                if cls is None:
                    continue
                self._values[row.key] = cls.model_validate(row.value)
                self._versions[row.key] = row.version

    async def set(
        self, session: AsyncSession, actor: Actor, key: str, value: dict[str, Any]
    ) -> BaseModel:
        cls = REGISTRY.get(key)
        if cls is None:
            raise UnknownSettingError(key)
        model = cls.model_validate(value)  # raises ValidationError with field details
        row = (
            await session.execute(
                select(Setting)
                .where(Setting.key == key, Setting.project_id.is_(None))
                .with_for_update()
            )
        ).scalar_one_or_none()
        before = row.value if row else cls().model_dump()
        version = (row.version if row else 0) + 1
        data = model.model_dump()
        if row is None:
            session.add(Setting(key=key, value=data, version=version, updated_by=actor.id))
        else:
            row.value, row.version, row.updated_by = data, version, actor.id
        session.add(SettingHistory(key=key, value=data, version=version, updated_by=actor.id))
        await audit.record(
            session, actor, "setting.update", "setting", key, {"before": before, "after": data}
        )
        await session.execute(
            text("SELECT pg_notify(:channel, :key)"), {"channel": CHANNEL, "key": key}
        )
        return model

    # ---- live reload
    async def start(self) -> None:
        await self.reload()
        if self._dsn:
            self._task = asyncio.create_task(self._listen_forever())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _listen_forever(self) -> None:
        assert self._dsn
        while True:
            try:
                conn = await asyncpg.connect(self._dsn)
                try:
                    await self.reload()  # catch up on anything missed while disconnected
                    await self._notify_subscribers("*")
                    closed = asyncio.Event()
                    conn.add_termination_listener(lambda _c, ev=closed: ev.set())
                    await conn.add_listener(CHANNEL, self._on_notify)
                    await closed.wait()
                finally:
                    with contextlib.suppress(Exception):
                        await conn.close()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("settings.listener_error", error=str(exc))
            await asyncio.sleep(2)

    def _on_notify(self, _conn: object, _pid: int, _channel: str, key: str) -> None:
        asyncio.get_running_loop().create_task(self._reload_logged(key))

    async def _reload_logged(self, key: str) -> None:
        if key in REGISTRY:
            try:
                await self.reload(key)
                log.info("settings.reloaded", key=key, version=self._versions.get(key))
            except Exception as exc:
                log.warning("settings.reload_failed", key=key, error=str(exc))
        await self._notify_subscribers(key)

    async def _notify_subscribers(self, key: str) -> None:
        for callback in self._subscribers:
            try:
                await callback(key)
            except Exception as exc:
                log.warning("settings.subscriber_failed", key=key, error=str(exc))


async def notify(session: AsyncSession, key: str) -> None:
    """Publish a change on the config channel; delivered when the transaction commits."""
    await session.execute(
        text("SELECT pg_notify(:channel, :key)"), {"channel": CHANNEL, "key": key}
    )


def listen_dsn(database_url: str) -> str:
    """asyncpg wants a plain postgresql:// URL (no SQLAlchemy driver suffix)."""
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
