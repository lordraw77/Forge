"""Local authentication, sessions, TOTP, user administration (E1.2)."""

from __future__ import annotations

import secrets as pysecrets
import string
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import redis.asyncio as redis
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import (
    Eula,
    LockoutPolicy,
    PasswordPolicy,
    SessionPolicy,
    SettingsStore,
    TotpPolicy,
    audit,
    secrets,
)
from forge.core.context import Actor, RequestContext
from forge.core.crypto import Envelope, sha256
from forge.core.db import admin_scope, project_scope
from forge.core.errors import ProblemError
from forge.core.logging import get_logger
from forge.core.models import utcnow
from forge.core.settings import Settings
from forge.iam import passwords, tokens
from forge.iam.models import AuthSession, PasswordHistory, RecoveryCode, User

log = get_logger(__name__)

INVALID = ProblemError(401, "invalid_credentials", "Invalid credentials")
LOGIN_RATE_PER_MIN = 20
RECOVERY_CODES = 10


@dataclass
class Flags:
    password_change_required: bool = False
    eula_required: bool = False
    totp_enrollment_required: bool = False

    def blocking(self) -> str | None:
        for name in ("password_change_required", "eula_required", "totp_enrollment_required"):
            if getattr(self, name):
                return name
        return None


@dataclass
class IssuedSession:
    user: User
    session_id: uuid.UUID
    access_token: str
    refresh_token: str
    csrf_token: str
    idle_seconds: int


@dataclass
class LoginOutcome:
    session: IssuedSession | None = None
    challenge: str | None = None


@dataclass
class UserView:
    user: User
    flags: Flags = field(default_factory=Flags)


def actor_for(user: User, ctx: RequestContext) -> Actor:
    return Actor(id=user.id, label=user.username, is_super_admin=user.is_super_admin, context=ctx)


def _temp_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(pysecrets.choice(alphabet) for _ in range(20))


def _recovery_code() -> str:
    raw = "".join(pysecrets.choice(string.ascii_lowercase + string.digits) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


class AuthService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: SettingsStore,
        envelope: Envelope,
        codec: tokens.TokenCodec,
        redis_client: redis.Redis,
    ) -> None:
        self._sessions = sessions
        self._settings = settings
        self._envelope = envelope
        self._codec = codec
        self._redis = redis_client

    # ---- helpers
    def flags(self, user: User) -> Flags:
        eula = self._settings.get(Eula)
        totp = self._settings.get(TotpPolicy)
        return Flags(
            password_change_required=user.must_change_password,
            eula_required=user.eula_version != eula.version,
            totp_enrollment_required=totp.required
            and not user.totp_enabled
            and user.auth_source == "local",
        )

    async def _rate_limit(self, ctx: RequestContext) -> None:
        key = f"ratelimit:login:{ctx.ip or 'unknown'}"
        try:
            count = await self._redis.incr(key)
            if count == 1:
                await self._redis.expire(key, 60)
        except redis.RedisError as exc:  # fail open on the limiter, never on authentication
            log.warning("auth.ratelimit_unavailable", error=str(exc))
            return
        if count > LOGIN_RATE_PER_MIN:
            raise ProblemError(429, "rate_limited", "Too many login attempts, retry later")

    async def get_user(self, user_id: uuid.UUID) -> User | None:
        async with project_scope(self._sessions, None, user_id) as s:
            return await s.get(User, user_id)

    # ---- login
    async def login(self, login: str, password: str, ctx: RequestContext) -> LoginOutcome:
        await self._rate_limit(ctx)
        lockout = self._settings.get(LockoutPolicy)
        failed = False
        async with project_scope(self._sessions, None) as s:
            user = (
                await s.execute(
                    select(User)
                    .where(
                        or_(
                            func.lower(User.username) == login.lower(),
                            func.lower(User.email) == login.lower(),
                        )
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if user is None or user.status != "active" or user.auth_source != "local":
                passwords.verify_password(None, password)  # same cost as a real check
                raise INVALID
            now = utcnow()
            locked = user.locked_until is not None and user.locked_until > now
            ok = passwords.verify_password(user.password_hash, password)
            if locked or not ok:
                if not locked:
                    window = timedelta(minutes=lockout.window_min)
                    if user.first_failed_at is None or now - user.first_failed_at > window:
                        user.first_failed_at, user.failed_logins = now, 0
                    user.failed_logins += 1
                    if user.failed_logins >= lockout.threshold:
                        user.locked_until = now + timedelta(minutes=lockout.lock_min)
                        await audit.record(s, actor_for(user, ctx), "auth.locked", "user", user.id)
                await audit.record(
                    s,
                    actor_for(user, ctx),
                    "auth.login_failed",
                    "user",
                    user.id,
                    {"locked": locked},
                )
                failed = True  # commit the counters and audit, then fail outside the transaction
            else:
                outcome = await self._login_succeeded(s, user, password, ctx)
        if failed:
            raise INVALID
        return outcome

    async def _login_succeeded(
        self, s: AsyncSession, user: User, password: str, ctx: RequestContext
    ) -> LoginOutcome:
        user.failed_logins, user.first_failed_at, user.locked_until = 0, None, None
        if user.password_hash and passwords.needs_rehash(user.password_hash):
            user.password_hash = passwords.hash_password(password)
        if user.totp_enabled:
            return LoginOutcome(challenge=self._codec.challenge(user.id))
        issued = await self._issue(s, user, ctx, family_id=None)
        await audit.record(s, actor_for(user, ctx), "auth.login", "user", user.id)
        return LoginOutcome(session=issued)

    async def login_totp(
        self, challenge: str, code: str | None, recovery_code: str | None, ctx: RequestContext
    ) -> IssuedSession:
        await self._rate_limit(ctx)
        try:
            user_id = self._codec.read_challenge(challenge)
        except tokens.TokenError as exc:
            raise ProblemError(
                401, "challenge_expired", "Login challenge expired, sign in again"
            ) from exc
        issued: IssuedSession | None = None
        async with project_scope(self._sessions, None, user_id) as s:
            user = (
                await s.execute(select(User).where(User.id == user_id).with_for_update())
            ).scalar_one()
            if not user.totp_enabled or user.totp_secret_id is None or user.status != "active":
                raise INVALID
            ok = False
            if recovery_code:
                ok = await self._use_recovery_code(s, user, recovery_code)
                if ok:
                    await audit.record(
                        s, actor_for(user, ctx), "auth.recovery_code_used", "user", user.id
                    )
            else:
                secret = await secrets.get(s, self._envelope, user.totp_secret_id)
                step = tokens.match_totp(secret, code or "", user.totp_last_step)
                if step is not None:
                    user.totp_last_step, ok = step, True
            if ok:
                issued = await self._issue(s, user, ctx, family_id=None)
                await audit.record(
                    s, actor_for(user, ctx), "auth.login", "user", user.id, {"mfa": True}
                )
            else:
                await audit.record(s, actor_for(user, ctx), "auth.totp_failed", "user", user.id)
        if issued is None:
            raise INVALID
        return issued

    async def _use_recovery_code(self, s: AsyncSession, user: User, code: str) -> bool:
        digest = sha256(code.strip().lower())
        row = (
            await s.execute(
                select(RecoveryCode).where(
                    RecoveryCode.user_id == user.id,
                    RecoveryCode.code_hash == digest,
                    RecoveryCode.used_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        row.used_at = utcnow()
        return True

    # ---- sessions
    async def _issue(
        self, s: AsyncSession, user: User, ctx: RequestContext, family_id: uuid.UUID | None
    ) -> IssuedSession:
        policy = self._settings.get(SessionPolicy)
        now = utcnow()
        refresh = tokens.new_refresh_token()
        row = AuthSession(
            user_id=user.id,
            family_id=family_id or uuid.uuid4(),
            refresh_hash=sha256(refresh),
            idle_expires_at=now + timedelta(minutes=policy.idle_min),
            expires_at=now + timedelta(hours=policy.absolute_h),
            ip=ctx.ip,
            user_agent=(ctx.user_agent or "")[:300],
        )
        if family_id is not None:  # rotation keeps the family's absolute expiry
            first = (
                await s.execute(
                    select(func.min(AuthSession.expires_at)).where(
                        AuthSession.family_id == family_id
                    )
                )
            ).scalar_one()
            row.expires_at = first or row.expires_at
        s.add(row)
        user.last_active_at = now
        await s.flush()
        return IssuedSession(
            user=user,
            session_id=row.id,
            access_token=self._codec.access(user.id, row.id),
            refresh_token=refresh,
            csrf_token=tokens.new_csrf_token(),
            idle_seconds=policy.idle_min * 60,
        )

    async def refresh(self, refresh_token: str, ctx: RequestContext) -> IssuedSession:
        expired = ProblemError(401, "session_expired", "Session expired, sign in again")
        reused = False
        async with project_scope(self._sessions, None) as s:
            row = (
                await s.execute(
                    select(AuthSession)
                    .where(AuthSession.refresh_hash == sha256(refresh_token))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                raise expired
            now = utcnow()
            if row.rotated_at is not None or row.revoked_at is not None:
                reused = True
                # A rotated token presented again: assume theft, revoke the whole family.
                await s.execute(
                    update(AuthSession)
                    .where(AuthSession.family_id == row.family_id, AuthSession.revoked_at.is_(None))
                    .values(revoked_at=now)
                )
                user = await s.get(User, row.user_id)
                if user and row.revoked_at is None:
                    await audit.record(
                        s,
                        actor_for(user, ctx),
                        "auth.refresh_reuse_detected",
                        "session",
                        row.family_id,
                    )
            else:
                return await self._rotate(s, row, now, ctx)
        assert reused
        raise expired

    async def _rotate(
        self, s: AsyncSession, row: AuthSession, now: Any, ctx: RequestContext
    ) -> IssuedSession:
        expired = ProblemError(401, "session_expired", "Session expired, sign in again")
        if row.idle_expires_at <= now or row.expires_at <= now:
            raise expired
        user = await s.get(User, row.user_id)
        if user is None or user.status != "active":
            raise expired
        row.rotated_at, row.last_used_at = now, now
        return await self._issue(s, user, ctx, family_id=row.family_id)

    async def session_valid(self, session_id: uuid.UUID) -> bool:
        async with project_scope(self._sessions, None) as s:
            row = await s.get(AuthSession, session_id)
            now = utcnow()
            return bool(row and row.revoked_at is None and row.expires_at > now)

    async def logout(self, session_id: uuid.UUID, actor: Actor) -> None:
        async with project_scope(self._sessions, None) as s:
            row = await s.get(AuthSession, session_id)
            if row is not None:
                await s.execute(
                    update(AuthSession)
                    .where(AuthSession.family_id == row.family_id, AuthSession.revoked_at.is_(None))
                    .values(revoked_at=utcnow())
                )
                await audit.record(s, actor, "auth.logout", "session", row.family_id)

    def read_access(self, token: str) -> tokens.AccessClaims:
        return self._codec.read_access(token)

    # ---- self-service
    async def change_password(
        self, user_id: uuid.UUID, current: str, new: str, ctx: RequestContext
    ) -> None:
        policy = self._settings.get(PasswordPolicy)
        async with project_scope(self._sessions, None, user_id) as s:
            user = (
                await s.execute(select(User).where(User.id == user_id).with_for_update())
            ).scalar_one()
            if user.auth_source != "local":
                raise ProblemError(
                    409, "not_local_user", "Password is managed by the identity provider"
                )
            if not passwords.verify_password(user.password_hash, current):
                raise ProblemError(400, "wrong_password", "Current password is incorrect")
            await self._set_password(s, user, new, policy)
            user.must_change_password = False
            await audit.record(s, actor_for(user, ctx), "user.password_changed", "user", user.id)

    async def _set_password(
        self, s: AsyncSession, user: User, new: str, policy: PasswordPolicy
    ) -> None:
        errors = passwords.policy_violations(
            new,
            min_length=policy.min_length,
            block_common=policy.block_common,
            username=user.username,
            email=user.email,
        )
        if policy.history and user.password_hash:
            recent = (
                await s.execute(
                    select(PasswordHistory.hash)
                    .where(PasswordHistory.user_id == user.id)
                    .order_by(PasswordHistory.created_at.desc())
                    .limit(policy.history)
                )
            ).scalars()
            if any(passwords.verify_password(h, new) for h in [user.password_hash, *recent]):
                errors.append(f"Password must differ from the last {policy.history} passwords")
        if errors:
            raise ProblemError(
                422, "password_policy", "Password does not meet the policy", "; ".join(errors)
            )
        if user.password_hash:
            s.add(PasswordHistory(user_id=user.id, hash=user.password_hash))
        user.password_hash = passwords.hash_password(new)

    async def accept_eula(self, user_id: uuid.UUID, version: str, ctx: RequestContext) -> None:
        current = self._settings.get(Eula)
        if version != current.version:
            raise ProblemError(409, "eula_version_mismatch", "EULA has changed, reload it")
        async with project_scope(self._sessions, None, user_id) as s:
            user = (
                await s.execute(select(User).where(User.id == user_id).with_for_update())
            ).scalar_one()
            user.eula_version = version
            await audit.record(
                s, actor_for(user, ctx), "user.eula_accepted", "user", user.id, {"version": version}
            )

    def eula(self) -> Eula:
        return self._settings.get(Eula)

    # ---- TOTP enrolment
    async def totp_enroll(self, user_id: uuid.UUID, ctx: RequestContext) -> tuple[str, str]:
        secret = tokens.new_totp_secret()
        async with admin_scope(self._sessions, user_id) as s:  # platform secret: forge_admin path
            user = (
                await s.execute(select(User).where(User.id == user_id).with_for_update())
            ).scalar_one()
            if user.totp_enabled:
                raise ProblemError(409, "totp_already_enabled", "TOTP is already enabled")
            old = user.totp_secret_id
            user.totp_secret_id = await secrets.put(s, self._envelope, "totp", secret)
            if old:
                await secrets.delete(s, old)
            await audit.record(
                s, actor_for(user, ctx), "user.totp_enrollment_started", "user", user.id
            )
            return secret, tokens.totp_uri(secret, user.email or user.username)

    async def totp_confirm(self, user_id: uuid.UUID, code: str, ctx: RequestContext) -> list[str]:
        async with admin_scope(self._sessions, user_id) as s:
            user = (
                await s.execute(select(User).where(User.id == user_id).with_for_update())
            ).scalar_one()
            if user.totp_enabled or user.totp_secret_id is None:
                raise ProblemError(409, "totp_not_pending", "Start TOTP enrolment first")
            secret = await secrets.get(s, self._envelope, user.totp_secret_id)
            step = tokens.match_totp(secret, code, user.totp_last_step)
            if step is None:
                raise ProblemError(400, "invalid_code", "Invalid code")
            user.totp_enabled, user.totp_last_step = True, step
            await s.execute(text("DELETE FROM recovery_code WHERE user_id = :u"), {"u": user.id})
            codes = [_recovery_code() for _ in range(RECOVERY_CODES)]
            for c in codes:
                s.add(RecoveryCode(user_id=user.id, code_hash=sha256(c)))
            await audit.record(s, actor_for(user, ctx), "user.totp_enabled", "user", user.id)
            return codes

    async def totp_disable(self, user_id: uuid.UUID, code: str, ctx: RequestContext) -> None:
        if self._settings.get(TotpPolicy).required:
            raise ProblemError(409, "totp_required", "TOTP is required by the platform policy")
        async with admin_scope(self._sessions, user_id) as s:
            user = (
                await s.execute(select(User).where(User.id == user_id).with_for_update())
            ).scalar_one()
            if not user.totp_enabled or user.totp_secret_id is None:
                raise ProblemError(409, "totp_not_enabled", "TOTP is not enabled")
            secret = await secrets.get(s, self._envelope, user.totp_secret_id)
            if tokens.match_totp(secret, code, user.totp_last_step) is None:
                raise ProblemError(400, "invalid_code", "Invalid code")
            old = user.totp_secret_id
            user.totp_enabled, user.totp_secret_id = False, None
            await s.flush()
            await secrets.delete(s, old)
            await s.execute(text("DELETE FROM recovery_code WHERE user_id = :u"), {"u": user.id})
            await audit.record(s, actor_for(user, ctx), "user.totp_disabled", "user", user.id)

    # ---- administration (Super Admin)
    async def list_users(self, search: str | None = None, limit: int = 200) -> list[User]:
        async with project_scope(self._sessions, None) as s:
            stmt = select(User).order_by(func.lower(User.username)).limit(min(limit, 500))
            if search:
                like = f"%{search.lower()}%"
                stmt = stmt.where(
                    or_(
                        func.lower(User.username).like(like),
                        func.lower(User.email).like(like),
                        func.lower(User.full_name).like(like),
                    )
                )
            return list((await s.execute(stmt)).scalars())

    async def create_user(
        self,
        actor: Actor,
        *,
        username: str,
        email: str,
        full_name: str = "",
        password: str | None = None,
        is_super_admin: bool = False,
    ) -> tuple[User, str | None]:
        """Create a local user. Without a password, a temporary one is generated and returned."""
        policy = self._settings.get(PasswordPolicy)
        temp = None if password else _temp_password()
        async with project_scope(self._sessions, None, actor.id) as s:
            exists = (
                await s.execute(
                    select(User.id).where(
                        or_(
                            func.lower(User.username) == username.lower(),
                            func.lower(User.email) == email.lower(),
                        )
                    )
                )
            ).first()
            if exists:
                raise ProblemError(409, "user_exists", "Username or email already in use")
            user = User(
                username=username,
                email=email,
                full_name=full_name,
                is_super_admin=is_super_admin,
                must_change_password=True,
                created_by=actor.id,
            )
            if password:
                await self._set_password(s, user, password, policy)
            else:
                user.password_hash = passwords.hash_password(temp or "")
            s.add(user)
            await s.flush()
            await audit.record(
                s,
                actor,
                "user.create",
                "user",
                user.id,
                {"username": username, "email": email, "is_super_admin": is_super_admin},
            )
            return user, temp

    async def update_user(self, actor: Actor, user_id: uuid.UUID, changes: dict[str, Any]) -> User:
        allowed = {"full_name", "email", "status", "is_super_admin"}
        unknown = set(changes) - allowed
        if unknown:
            raise ProblemError(422, "invalid_fields", "Unknown fields", ", ".join(sorted(unknown)))
        if actor.id == user_id and changes.get("is_super_admin") is False:
            raise ProblemError(409, "self_demotion", "You cannot remove your own Super Admin role")
        if actor.id == user_id and changes.get("status") == "disabled":
            raise ProblemError(409, "self_disable", "You cannot disable your own account")
        async with project_scope(self._sessions, None, actor.id) as s:
            user = (
                await s.execute(select(User).where(User.id == user_id).with_for_update())
            ).scalar_one_or_none()
            if user is None:
                raise ProblemError(404, "not_found", "User not found")
            before = {k: getattr(user, k) for k in changes}
            for k, v in changes.items():
                setattr(user, k, v)
            if changes.get("status") == "disabled":
                await s.execute(
                    update(AuthSession)
                    .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
                    .values(revoked_at=utcnow())
                )
            await audit.record(
                s, actor, "user.update", "user", user.id, {"before": before, "after": changes}
            )
            return user

    async def unlock_user(self, actor: Actor, user_id: uuid.UUID) -> None:
        async with project_scope(self._sessions, None, actor.id) as s:
            user = await s.get(User, user_id)
            if user is None:
                raise ProblemError(404, "not_found", "User not found")
            user.locked_until, user.failed_logins, user.first_failed_at = None, 0, None
            await audit.record(s, actor, "user.unlock", "user", user.id)

    async def reset_password(self, actor: Actor, user_id: uuid.UUID) -> str:
        temp = _temp_password()
        async with project_scope(self._sessions, None, actor.id) as s:
            user = await s.get(User, user_id)
            if user is None:
                raise ProblemError(404, "not_found", "User not found")
            if user.auth_source != "local":
                raise ProblemError(
                    409, "not_local_user", "Password is managed by the identity provider"
                )
            if user.password_hash:
                s.add(PasswordHistory(user_id=user.id, hash=user.password_hash))
            user.password_hash, user.must_change_password = passwords.hash_password(temp), True
            await s.execute(
                update(AuthSession)
                .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=utcnow())
            )
            await audit.record(s, actor, "user.password_reset", "user", user.id)
            return temp

    # ---- bootstrap (US-1.2.1)
    async def ensure_bootstrap_admin(self, settings: Settings) -> None:
        async with project_scope(self._sessions, None) as s:
            await s.execute(text("SELECT pg_advisory_xact_lock(hashtext('forge.bootstrap_admin'))"))
            count = (await s.execute(select(func.count()).select_from(User))).scalar_one()
            if count == 0:
                if settings.admin_password is None:
                    log.error(
                        "bootstrap.no_admin_password", hint="set FORGE_ADMIN_PASSWORD in .env"
                    )
                    return
                user = User(
                    username=settings.admin_username,
                    email=settings.admin_email,
                    full_name="Administrator",
                    is_super_admin=True,
                    must_change_password=True,
                    password_hash=passwords.hash_password(
                        settings.admin_password.get_secret_value()
                    ),
                )
                s.add(user)
                await s.flush()
                await audit.record(
                    s,
                    Actor(id=None, label="bootstrap"),
                    "user.bootstrap_admin",
                    "user",
                    user.id,
                    {"username": user.username},
                )
                log.info("bootstrap.admin_created", username=user.username)
                return
            if settings.admin_password is not None:
                admin = (
                    await s.execute(
                        select(User).where(
                            func.lower(User.username) == settings.admin_username.lower()
                        )
                    )
                ).scalar_one_or_none()
                if admin is not None and not admin.must_change_password:
                    log.warning(
                        "bootstrap.admin_password_still_set",
                        hint="remove FORGE_ADMIN_PASSWORD from .env, it is ignored now",
                    )
