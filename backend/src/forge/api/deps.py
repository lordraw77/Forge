"""Request dependencies: services, principal resolution, CSRF, gates, permissions (ADR-011)."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Request

from forge.admin.service import SettingsStore
from forge.agents.service import AgentStudio, RunEvents
from forge.bundles.service import Bundles
from forge.core.context import Actor, RequestContext
from forge.core.errors import ProblemError
from forge.core.settings import Settings
from forge.gateway.service import Gateway
from forge.iam.service import AuthService, Flags, TokenError, User, actor_for
from forge.prompts.service import PromptStudio
from forge.rag.service import Rag
from forge.tenancy.service import ProjectAccess, TenancyService

ACCESS_COOKIE = "forge_access"
REFRESH_COOKIE = "forge_refresh"
CSRF_COOKIE = "forge_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

UNAUTHENTICATED = ProblemError(401, "unauthenticated", "Authentication required")


@dataclass
class Services:
    settings: Settings
    store: SettingsStore
    auth: AuthService
    tenancy: TenancyService
    gateway: Gateway
    prompts: PromptStudio
    rag: Rag
    agents: AgentStudio
    run_events: RunEvents
    bundles: Bundles


@dataclass
class Principal:
    actor: Actor
    kind: str  # "session" | "api_key"
    user: User | None = None
    session_id: uuid.UUID | None = None
    flags: Flags | None = None


def services(request: Request) -> Services:
    svc: Services = request.app.state.services
    return svc


def request_context(request: Request) -> RequestContext:
    return RequestContext(
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


async def principal(request: Request) -> Principal:
    """API key (Authorization: Bearer fk_…) or session cookie; cookie auth requires CSRF."""
    svc = services(request)
    ctx = request_context(request)
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        key = await svc.tenancy.resolve_key(auth_header[7:].strip())
        if key is None:
            raise ProblemError(401, "invalid_api_key", "Invalid or expired API key")
        actor = Actor(
            id=key.created_by,
            label=f"api_key:{key.key_id}",
            api_key_project=key.project_id,
            scopes=key.scopes,
            context=ctx,
        )
        return Principal(actor=actor, kind="api_key")

    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise UNAUTHENTICATED
    try:
        claims = svc.auth.read_access(token)
    except TokenError as exc:
        raise ProblemError(401, "session_expired", "Session expired") from exc
    if request.method not in SAFE_METHODS:
        csrf = request.cookies.get(CSRF_COOKIE)
        if not csrf or request.headers.get(CSRF_HEADER) != csrf:
            raise ProblemError(403, "csrf_failed", "Missing or invalid CSRF token")
    if not await svc.auth.session_valid(claims.session_id):
        raise ProblemError(401, "session_expired", "Session expired")
    user = await svc.auth.get_user(claims.user_id)
    if user is None or user.status != "active":
        raise UNAUTHENTICATED
    return Principal(
        actor=actor_for(user, ctx),
        kind="session",
        user=user,
        session_id=claims.session_id,
        flags=svc.auth.flags(user),
    )


async def ready_principal(request: Request) -> Principal:
    """Principal with no pending first-login step (password change, EULA, TOTP enrolment)."""
    p = await principal(request)
    if p.flags and (blocking := p.flags.blocking()):
        raise ProblemError(403, blocking, "Complete the required account step first")
    return p


async def super_admin(request: Request) -> Principal:
    p = await ready_principal(request)
    if p.kind != "session" or not p.actor.is_super_admin:
        raise ProblemError(403, "forbidden", "Super Admin role required")
    return p


@dataclass
class ProjectContext:
    principal: Principal
    access: ProjectAccess

    @property
    def project_id(self) -> uuid.UUID:
        return self.access.project.id

    @property
    def actor(self) -> Actor:
        return self.principal.actor


def project_permission(
    permission: str | None = None, *, writable: bool = False
) -> Callable[[Request, uuid.UUID], Awaitable[ProjectContext]]:
    """Dependency: the principal must hold `permission` in the path's project (ADR-003)."""

    async def dependency(request: Request, project_id: uuid.UUID) -> ProjectContext:
        p = await ready_principal(request)
        access = await services(request).tenancy.access(p.actor, project_id)
        if permission and permission not in access.permissions:
            raise ProblemError(403, "forbidden", f"Permission '{permission}' required")
        if writable and access.project.status == "archived":
            raise ProblemError(409, "project_archived", "Project is archived (read-only)")
        return ProjectContext(principal=p, access=access)

    return dependency
