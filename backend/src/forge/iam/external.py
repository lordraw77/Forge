"""External sign-in: LDAP password login, OIDC redirect flow, user provisioning, group sync
(E2.1, US-2.1.2, US-2.1.4, US-2.1.6)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets as pysecrets
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

import redis.asyncio as redis
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import LockoutPolicy, SettingsStore, audit
from forge.core.context import Actor, RequestContext
from forge.core.db import admin_scope, project_scope
from forge.core.errors import ProblemError
from forge.core.logging import get_logger
from forge.core.models import utcnow
from forge.iam import directory, oidc
from forge.iam.models import AuthSession, IdentityProvider, User
from forge.iam.providers import IdentityProviders
from forge.tenancy.service import GroupMappings

if TYPE_CHECKING:
    from forge.iam.auth import AuthService, IssuedSession

log = get_logger(__name__)

STATE_TTL_S = 600
INVALID = ProblemError(401, "invalid_credentials", "Invalid credentials")
OIDC_FAILED = ProblemError(401, "oidc_failed", "Sign-in with the identity provider failed")


def _external_actor(label: str, user: User | None, ctx: RequestContext) -> Actor:
    return Actor(id=user.id if user else None, label=user.username if user else label, context=ctx)


class ExternalAuth:
    def __init__(
        self,
        auth: AuthService,
        sessions: async_sessionmaker[AsyncSession],
        settings: SettingsStore,
        providers: IdentityProviders,
        mappings: GroupMappings,
        redis_client: redis.Redis,
        public_url: str,
    ) -> None:
        self._auth = auth
        self._sessions = sessions
        self._settings = settings
        self._providers = providers
        self._mappings = mappings
        self._redis = redis_client
        self._public_url = public_url.rstrip("/")

    @property
    def redirect_uri(self) -> str:
        return f"{self._public_url}/api/v1/auth/oidc/callback"

    # ---- failure counter shared by all providers (US-2.1.2 #5)
    async def _blocked(self, login: str) -> bool:
        policy = self._settings.get(LockoutPolicy)
        try:
            raw = await self._redis.get(f"extfail:{login.lower()}")
        except redis.RedisError:
            return False
        return raw is not None and int(raw) >= policy.threshold

    async def _count_failure(self, login: str) -> None:
        policy = self._settings.get(LockoutPolicy)
        key = f"extfail:{login.lower()}"
        try:
            n = await self._redis.incr(key)
            if n == 1:
                await self._redis.expire(key, policy.window_min * 60)
            if n >= policy.threshold:
                await self._redis.expire(key, policy.lock_min * 60)
        except redis.RedisError as exc:
            log.warning("auth.extfail_unavailable", error=str(exc))

    # ---- LDAP
    async def ldap_login(
        self, login: str, password: str, ctx: RequestContext
    ) -> IssuedSession | None:
        """None when no LDAP provider accepts the credentials."""
        providers = await self._providers.enabled("ldap")
        if not providers or await self._blocked(login):
            return None
        for provider in providers:
            cfg = directory.LdapConfig.model_validate(provider.config)
            try:
                secret = await self._providers.secret(provider)
                identity = await asyncio.to_thread(
                    directory.authenticate, cfg, secret, login, password
                )
            except directory.DirectoryError as exc:
                log.warning("auth.ldap_unavailable", provider=provider.name, error=str(exc))
                continue
            if identity is not None:
                await self._redis.delete(f"extfail:{login.lower()}")
                return await self.complete(provider, identity, ctx)
        await self._count_failure(login)
        return None

    # ---- OIDC
    async def oidc_start(self, provider_id: uuid.UUID) -> str:
        provider = await self._providers.get(provider_id)
        if provider.kind != "oidc" or not provider.enabled:
            raise ProblemError(404, "not_found", "Identity provider not found")
        cfg = oidc.OidcConfig.model_validate(provider.config)
        try:
            meta = await oidc.discover(cfg)
        except oidc.OidcError as exc:
            log.warning("auth.oidc_discovery_failed", provider=provider.name, error=str(exc))
            raise ProblemError(502, "oidc_unavailable", "Identity provider unreachable") from exc
        state, nonce = pysecrets.token_urlsafe(24), pysecrets.token_urlsafe(24)
        verifier, challenge = oidc.new_pkce()
        await self._redis.set(
            f"oidc:{state}",
            json.dumps({"provider": str(provider.id), "nonce": nonce, "verifier": verifier}),
            ex=STATE_TTL_S,
        )
        return oidc.authorize_url(meta, cfg, self.redirect_uri, state, nonce, challenge)

    async def oidc_callback(self, state: str, code: str, ctx: RequestContext) -> IssuedSession:
        raw = await self._redis.getdel(f"oidc:{state}")  # one use
        if raw is None:
            await self._audit_failure("state", "unknown or expired state", ctx)
            raise OIDC_FAILED
        saved = json.loads(raw)
        provider = await self._providers.get(uuid.UUID(saved["provider"]))
        if not provider.enabled:
            raise OIDC_FAILED
        cfg = oidc.OidcConfig.model_validate(provider.config)
        try:
            meta = await oidc.discover(cfg)
            token = await oidc.exchange_code(
                meta,
                cfg,
                await self._providers.secret(provider),
                code,
                self.redirect_uri,
                saved["verifier"],
            )
            claims = oidc.verify_id_token(
                cfg, meta, await oidc.fetch_jwks(meta), token, saved["nonce"]
            )
        except oidc.OidcError as exc:
            await self._audit_failure(provider.name, str(exc), ctx)
            raise OIDC_FAILED from exc
        identity = oidc.identity_from_claims(cfg, claims)
        if not identity.groups:
            log.info("auth.oidc_no_groups", provider=provider.name, claim=cfg.groups_claim)
        return await self.complete(provider, identity, ctx)

    async def _audit_failure(self, target: str, reason: str, ctx: RequestContext) -> None:
        async with admin_scope(self._sessions) as s:
            await audit.record(
                s,
                _external_actor("oidc", None, ctx),
                "auth.oidc_failed",
                "identity_provider",
                target,
                {"reason": reason[:300]},
            )

    # ---- provisioning
    async def complete(
        self, provider: IdentityProvider, identity: directory.ExternalIdentity, ctx: RequestContext
    ) -> IssuedSession:
        user = await self._upsert_user(provider, identity, ctx)
        await self._mappings.apply(
            _external_actor(identity.username, user, ctx), user.id, provider.id, identity.groups
        )
        async with project_scope(self._sessions, None, user.id) as s:
            row = await s.get(User, user.id)
            assert row is not None
            issued = await self._auth.issue(s, row, ctx)
            await audit.record(
                s,
                _external_actor(row.username, row, ctx),
                "auth.login",
                "user",
                row.id,
                {"provider": provider.name, "kind": provider.kind},
            )
        return issued

    async def _upsert_user(
        self, provider: IdentityProvider, identity: directory.ExternalIdentity, ctx: RequestContext
    ) -> User:
        kind = provider.kind
        email = identity.email or (
            hashlib.sha256(identity.external_id.encode()).hexdigest()[:16] + "@external.invalid"
        )
        async with project_scope(self._sessions, None) as s:
            user = (
                await s.execute(
                    select(User)
                    .where(
                        User.provider_id == provider.id, User.external_id == identity.external_id
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if user is None:
                clash = (
                    await s.execute(
                        select(User.id).where(
                            or_(
                                User.email.ilike(email),
                                User.username.ilike(identity.username),
                            )
                        )
                    )
                ).first()
                if clash:
                    # Never merge on email or username: that would let a directory entry take
                    # over a local or foreign account.
                    await audit.record(
                        s,
                        _external_actor(identity.username, None, ctx),
                        "auth.external_conflict",
                        "identity_provider",
                        provider.id,
                        {"username": identity.username, "email": email},
                    )
                    failed = True
                else:
                    failed = False
                    user = User(
                        username=identity.username,
                        email=email,
                        full_name=identity.full_name,
                        auth_source=kind,
                        provider_id=provider.id,
                        external_id=identity.external_id,
                        password_hash=None,
                    )
                    s.add(user)
                    await s.flush()
                    await audit.record(
                        s,
                        _external_actor(identity.username, user, ctx),
                        "user.provisioned",
                        "user",
                        user.id,
                        {"provider": provider.name},
                    )
            else:
                failed = False
                if user.status != "active":
                    failed = True
                else:
                    user.full_name = identity.full_name or user.full_name
                    if identity.email and not user.email.endswith("@external.invalid"):
                        user.email = identity.email
            if not failed and user is not None:
                user.external_groups = identity.groups
                return user
        raise INVALID if kind == "ldap" else OIDC_FAILED

    # ---- sync (US-2.1.6)
    async def sync(self, actor: Actor, provider: IdentityProvider) -> dict[str, int]:
        if provider.kind != "ldap":
            raise ProblemError(422, "invalid_provider", "Only LDAP providers sync")
        cfg = directory.LdapConfig.model_validate(provider.config)
        try:
            entries = await asyncio.to_thread(
                directory.all_users, cfg, await self._providers.secret(provider)
            )
        except directory.DirectoryError as exc:
            raise ProblemError(502, "directory_error", "Directory error", str(exc)) from exc
        seen = {e.external_id: e for e in entries}
        totals = {"seen": len(seen), "added": 0, "removed": 0, "changed": 0, "disabled": 0}
        async with project_scope(self._sessions, None) as s:
            known = list(
                (await s.execute(select(User).where(User.provider_id == provider.id))).scalars()
            )
        for user in known:
            entry = seen.get(user.external_id or "")
            if entry is None or entry.disabled:
                if user.status == "active":
                    await self._disable(actor, user, provider)
                    totals["disabled"] += 1
                continue
            if user.status != "active":
                continue
            result = await self._mappings.apply(actor, user.id, provider.id, entry.groups)
            totals["added"] += result.added
            totals["removed"] += result.removed
            totals["changed"] += result.changed
        async with admin_scope(self._sessions, actor.id) as s:
            row = await s.get(IdentityProvider, provider.id)
            if row is not None:
                row.last_sync_at, row.last_sync = utcnow(), totals
            await audit.record(s, actor, "idp.sync", "identity_provider", provider.id, totals)
        return totals

    async def _disable(self, actor: Actor, user: User, provider: IdentityProvider) -> None:
        async with project_scope(self._sessions, None, user.id) as s:
            row = await s.get(User, user.id)
            if row is None:
                return
            row.status = "disabled"
            for sess in (
                await s.execute(
                    select(AuthSession).where(
                        AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)
                    )
                )
            ).scalars():
                sess.revoked_at = utcnow()
            await audit.record(
                s, actor, "idp.user_disabled", "user", user.id, {"provider": provider.name}
            )
        await self._mappings.drop_user(user.id)

    async def sync_due(self, actor: Actor) -> None:
        """Run the providers whose interval has elapsed (called by a periodic worker)."""
        now = utcnow()
        for provider in await self._providers.enabled("ldap"):
            minutes = int(provider.config.get("sync_interval_min") or 0)
            last = provider.last_sync_at
            if minutes and (last is None or now - last >= timedelta(minutes=minutes)):
                try:
                    await self.sync(actor, provider)
                except ProblemError as exc:
                    log.warning("idp.sync_failed", provider=provider.name, error=exc.title)
