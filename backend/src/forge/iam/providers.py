"""Identity provider administration: LDAP/AD and OIDC definitions, secrets, tests (E2.1)."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import LocalLogin, SettingsStore, audit, secrets
from forge.core.context import Actor
from forge.core.crypto import Envelope
from forge.core.db import admin_scope, project_scope
from forge.core.errors import ProblemError
from forge.iam import directory, oidc
from forge.iam.models import IdentityProvider, User

NOT_FOUND = ProblemError(404, "not_found", "Identity provider not found")
CONFIGS: dict[str, type[directory.LdapConfig] | type[oidc.OidcConfig]] = {
    "ldap": directory.LdapConfig,
    "oidc": oidc.OidcConfig,
}


@dataclass
class ProviderView:
    provider: IdentityProvider
    has_secret: bool
    user_count: int


@dataclass
class PublicProvider:
    id: uuid.UUID
    name: str
    label: str


@dataclass
class TestResult:
    ok: bool
    message: str
    details: dict[str, Any]


def _validated(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    model = CONFIGS.get(kind)
    if model is None:
        raise ProblemError(422, "invalid_provider", f"Unknown provider kind {kind!r}")
    try:
        return model.model_validate(config).model_dump()
    except ValidationError as exc:
        errors = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise ProblemError(
            422, "invalid_provider", "Invalid provider configuration", errors
        ) from exc


class IdentityProviders:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        envelope: Envelope,
        settings: SettingsStore,
    ) -> None:
        self._sessions = sessions
        self._envelope = envelope
        self._settings = settings

    # ---- reads
    async def list_providers(self) -> list[ProviderView]:
        async with admin_scope(self._sessions) as s:
            rows = (
                (
                    await s.execute(
                        select(IdentityProvider).order_by(
                            IdentityProvider.position, IdentityProvider.name
                        )
                    )
                )
                .scalars()
                .all()
            )
            counts = dict(
                (
                    await s.execute(
                        select(User.provider_id, func.count())
                        .where(User.provider_id.is_not(None))
                        .group_by(User.provider_id)
                    )
                ).all()
            )
            return [ProviderView(p, p.secret_id is not None, counts.get(p.id, 0)) for p in rows]

    async def get(self, provider_id: uuid.UUID) -> IdentityProvider:
        async with admin_scope(self._sessions) as s:
            row = await s.get(IdentityProvider, provider_id)
            if row is None:
                raise NOT_FOUND
            return row

    async def enabled(self, kind: str) -> list[IdentityProvider]:
        """Enabled providers in login order (used before any user is known: no tenant scope)."""
        async with project_scope(self._sessions, None) as s:
            return list(
                (
                    await s.execute(
                        select(IdentityProvider)
                        .where(IdentityProvider.kind == kind, IdentityProvider.enabled.is_(True))
                        .order_by(IdentityProvider.position, IdentityProvider.name)
                    )
                ).scalars()
            )

    async def public(self) -> list[PublicProvider]:
        out = []
        for p in await self.enabled("oidc"):
            label = str(p.config.get("button_label") or "") or p.name
            out.append(PublicProvider(p.id, p.name, label))
        return out

    async def secret(self, provider: IdentityProvider) -> str:
        if provider.secret_id is None:
            return ""
        async with project_scope(self._sessions, None) as s:
            return await secrets.get(s, self._envelope, provider.secret_id)

    # ---- writes
    async def create(
        self,
        actor: Actor,
        kind: str,
        name: str,
        config: dict[str, Any],
        secret: str | None,
        enabled: bool,
    ) -> IdentityProvider:
        clean = _validated(kind, config)
        async with admin_scope(self._sessions, actor.id) as s:
            row = IdentityProvider(kind=kind, name=name.strip(), config=clean, created_by=actor.id)
            if secret:
                row.secret_id = await secrets.put(s, self._envelope, f"idp.{kind}", secret)
            s.add(row)
            try:
                await s.flush()
            except IntegrityError as exc:
                raise ProblemError(409, "name_taken", "A provider with this name exists") from exc
            await audit.record(
                s,
                actor,
                "idp.create",
                "identity_provider",
                row.id,
                {"kind": kind, "name": row.name, "config": clean, "secret_set": bool(secret)},
            )
            if enabled:
                await self._set_enabled(s, actor, row, True)
            return row

    async def update(
        self,
        actor: Actor,
        provider_id: uuid.UUID,
        name: str | None,
        config: dict[str, Any] | None,
        secret: str | None,
        enabled: bool | None,
    ) -> IdentityProvider:
        async with admin_scope(self._sessions, actor.id) as s:
            row = await s.get(IdentityProvider, provider_id)
            if row is None:
                raise NOT_FOUND
            diff: dict[str, Any] = {}
            if name is not None and name.strip() != row.name:
                diff["name"] = name.strip()
                row.name = name.strip()
            if config is not None:
                row.config = _validated(row.kind, config)
                diff["config"] = row.config
            if secret:
                if row.secret_id:
                    await secrets.delete(s, row.secret_id)
                row.secret_id = await secrets.put(s, self._envelope, f"idp.{row.kind}", secret)
                diff["secret_set"] = True
            try:
                await s.flush()
            except IntegrityError as exc:
                raise ProblemError(409, "name_taken", "A provider with this name exists") from exc
            if diff:
                await audit.record(s, actor, "idp.update", "identity_provider", row.id, diff)
            if enabled is not None and enabled != row.enabled:
                await self._set_enabled(s, actor, row, enabled)
            return row

    async def _set_enabled(
        self, s: AsyncSession, actor: Actor, row: IdentityProvider, enabled: bool
    ) -> None:
        if enabled and row.secret_id is None:
            raise ProblemError(422, "invalid_provider", "Set the secret before enabling")
        row.enabled = enabled
        await s.flush()
        if not enabled and not self._settings.get(LocalLogin).enabled:
            others = (
                await s.execute(
                    select(func.count()).where(
                        IdentityProvider.enabled.is_(True), IdentityProvider.id != row.id
                    )
                )
            ).scalar_one()
            if not others:
                raise ProblemError(
                    409, "local_login_off", "Turn local login on before disabling the last provider"
                )
        await audit.record(
            s,
            actor,
            "idp.enable" if enabled else "idp.disable",
            "identity_provider",
            row.id,
            {"name": row.name},
        )

    async def delete(self, actor: Actor, provider_id: uuid.UUID) -> None:
        async with admin_scope(self._sessions, actor.id) as s:
            row = await s.get(IdentityProvider, provider_id)
            if row is None:
                raise NOT_FOUND
            used = (
                await s.execute(select(func.count()).where(User.provider_id == provider_id))
            ).scalar_one()
            if used:
                raise ProblemError(
                    409,
                    "provider_in_use",
                    "Users belong to this provider: disable it instead",
                    f"{used} users",
                )
            await audit.record(
                s, actor, "idp.delete", "identity_provider", row.id, {"name": row.name}
            )
            await s.delete(row)
            if row.secret_id:
                await s.flush()
                await secrets.delete(s, row.secret_id)

    # ---- tests
    async def test(
        self, provider: IdentityProvider, secret_override: str | None = None
    ) -> TestResult:
        secret = secret_override or await self.secret(provider)
        try:
            if provider.kind == "ldap":
                cfg = directory.LdapConfig.model_validate(provider.config)
                message = await asyncio.to_thread(directory.check_connection, cfg, secret)
                return TestResult(True, message, {})
            ocfg = oidc.OidcConfig.model_validate(provider.config)
            meta = await oidc.discover(ocfg)
            keys = await oidc.fetch_jwks(meta)
            return TestResult(
                True,
                "discovery and JWKS fetched",
                {
                    "authorization_endpoint": meta.authorization_endpoint,
                    "token_endpoint": meta.token_endpoint,
                    "jwks_keys": len(keys.get("keys", [])),
                },
            )
        except (directory.DirectoryError, oidc.OidcError) as exc:
            return TestResult(False, str(exc), {})

    async def test_user(
        self, provider: IdentityProvider, login: str
    ) -> directory.ExternalIdentity | None:
        if provider.kind != "ldap":
            raise ProblemError(422, "invalid_provider", "Test user applies to LDAP providers")
        cfg = directory.LdapConfig.model_validate(provider.config)
        secret = await self.secret(provider)
        try:
            return await asyncio.to_thread(directory.lookup, cfg, secret, login)
        except directory.DirectoryError as exc:
            raise ProblemError(502, "directory_error", "Directory error", str(exc)) from exc
