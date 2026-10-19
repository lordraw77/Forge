"""External identity: sign-in endpoints and the Authentication section of the Admin Console
(E2.1)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from forge.admin.service import LocalLogin
from forge.api.deps import Principal, request_context, services, super_admin
from forge.api.routes_auth import set_cookies
from forge.core.errors import ProblemError
from forge.iam.models import IdentityProvider

public_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
admin_router = APIRouter(prefix="/api/v1/admin", tags=["identity"])
Admin = Annotated[Principal, Depends(super_admin)]


# ---- public
class PublicProviderOut(BaseModel):
    id: uuid.UUID
    name: str
    label: str


class SignInOptionsOut(BaseModel):
    local_login: bool
    providers: list[PublicProviderOut]


@public_router.get("/providers", response_model=SignInOptionsOut)
async def sign_in_options(request: Request) -> SignInOptionsOut:
    svc = services(request)
    providers = [
        PublicProviderOut(id=p.id, name=p.name, label=p.label) for p in await svc.providers.public()
    ]
    ldap = bool(await svc.providers.enabled("ldap"))
    local = svc.store.get(LocalLogin).enabled
    return SignInOptionsOut(local_login=local or ldap, providers=providers)


@public_router.get("/oidc/callback", include_in_schema=False)
async def oidc_callback(
    request: Request, state: str = "", code: str = "", error: str = ""
) -> Response:
    ext = services(request).auth.external
    assert ext is not None
    failure = RedirectResponse("/login?error=oidc_failed", status_code=status.HTTP_303_SEE_OTHER)
    if error or not state or not code:
        return failure
    try:
        issued = await ext.oidc_callback(state, code, request_context(request))
    except ProblemError:
        return failure
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    set_cookies(request, response, issued)
    return response


@public_router.get("/oidc/{provider_id}/start", include_in_schema=False)
async def oidc_start(provider_id: uuid.UUID, request: Request) -> Response:
    ext = services(request).auth.external
    assert ext is not None
    try:
        url = await ext.oidc_start(provider_id)
    except ProblemError:
        return RedirectResponse("/login?error=oidc_unavailable", status_code=303)
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


# ---- admin: providers
class IdpIn(BaseModel):
    kind: str = Field(pattern="^(ldap|oidc)$")
    name: str = Field(min_length=1, max_length=80)
    config: dict[str, Any]
    secret: str | None = Field(default=None, max_length=2000)
    enabled: bool = False


class IdpUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    config: dict[str, Any] | None = None
    secret: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None


class IdpOut(BaseModel):
    id: uuid.UUID
    kind: str
    name: str
    enabled: bool
    config: dict[str, Any]
    has_secret: bool
    user_count: int = 0
    last_sync_at: datetime | None = None
    last_sync: dict[str, Any] | None = None
    redirect_uri: str | None = None


class IdpTestOut(BaseModel):
    ok: bool
    message: str
    details: dict[str, Any]


class IdpTestUserIn(BaseModel):
    login: str = Field(min_length=1, max_length=200)


class IdpTestUserOut(BaseModel):
    found: bool
    username: str = ""
    email: str = ""
    full_name: str = ""
    external_id: str = ""
    groups: list[str] = []
    disabled: bool = False


class IdpMappingIn(BaseModel):
    external_group: str = Field(min_length=1, max_length=500)
    project_id: uuid.UUID
    role_name_id: uuid.UUID


class IdpMappingOut(BaseModel):
    id: uuid.UUID
    provider_id: uuid.UUID
    external_group: str
    project_id: uuid.UUID
    project_name: str
    role_name_id: uuid.UUID
    role_name: str
    role_type: str


def _out(request: Request, p: IdentityProvider, has_secret: bool, users: int = 0) -> IdpOut:
    ext = services(request).auth.external
    return IdpOut(
        id=p.id,
        kind=p.kind,
        name=p.name,
        enabled=p.enabled,
        config=p.config,
        has_secret=has_secret,
        user_count=users,
        last_sync_at=p.last_sync_at,
        last_sync=p.last_sync,
        redirect_uri=ext.redirect_uri if ext and p.kind == "oidc" else None,
    )


@admin_router.get("/identity-providers", response_model=list[IdpOut])
async def list_identity_providers(_p: Admin, request: Request) -> list[IdpOut]:
    rows = await services(request).providers.list_providers()
    return [_out(request, v.provider, v.has_secret, v.user_count) for v in rows]


@admin_router.post(
    "/identity-providers", response_model=IdpOut, status_code=status.HTTP_201_CREATED
)
async def create_identity_provider(body: IdpIn, p: Admin, request: Request) -> IdpOut:
    row = await services(request).providers.create(
        p.actor, body.kind, body.name, body.config, body.secret, body.enabled
    )
    return _out(request, row, bool(row.secret_id))


@admin_router.patch("/identity-providers/{provider_id}", response_model=IdpOut)
async def update_identity_provider(
    provider_id: uuid.UUID, body: IdpUpdateIn, p: Admin, request: Request
) -> IdpOut:
    row = await services(request).providers.update(
        p.actor, provider_id, body.name, body.config, body.secret, body.enabled
    )
    return _out(request, row, bool(row.secret_id))


@admin_router.delete("/identity-providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_identity_provider(provider_id: uuid.UUID, p: Admin, request: Request) -> None:
    await services(request).providers.delete(p.actor, provider_id)


@admin_router.post("/identity-providers/{provider_id}/test", response_model=IdpTestOut)
async def check_identity_provider(
    provider_id: uuid.UUID, _p: Admin, request: Request
) -> IdpTestOut:
    svc = services(request)
    result = await svc.providers.test(await svc.providers.get(provider_id))
    return IdpTestOut(ok=result.ok, message=result.message, details=result.details)


@admin_router.post("/identity-providers/{provider_id}/test-user", response_model=IdpTestUserOut)
async def check_identity_provider_user(
    provider_id: uuid.UUID, body: IdpTestUserIn, _p: Admin, request: Request
) -> IdpTestUserOut:
    svc = services(request)
    found = await svc.providers.test_user(await svc.providers.get(provider_id), body.login)
    if found is None:
        return IdpTestUserOut(found=False)
    return IdpTestUserOut(
        found=True,
        username=found.username,
        email=found.email,
        full_name=found.full_name,
        external_id=found.external_id,
        groups=found.groups,
        disabled=found.disabled,
    )


@admin_router.post("/identity-providers/{provider_id}/sync", response_model=dict[str, int])
async def sync_identity_provider(
    provider_id: uuid.UUID, p: Admin, request: Request
) -> dict[str, int]:
    svc = services(request)
    ext = svc.auth.external
    assert ext is not None
    return await ext.sync(p.actor, await svc.providers.get(provider_id))


# ---- admin: group mappings
@admin_router.get("/identity-providers/{provider_id}/mappings", response_model=list[IdpMappingOut])
async def list_mappings(provider_id: uuid.UUID, _p: Admin, request: Request) -> list[IdpMappingOut]:
    rows = await services(request).mappings.list_mappings(provider_id)
    return [
        IdpMappingOut(
            id=r.mapping.id,
            provider_id=r.mapping.provider_id,
            external_group=r.mapping.external_group,
            project_id=r.mapping.project_id,
            project_name=r.project_name,
            role_name_id=r.mapping.role_name_id,
            role_name=r.role_name,
            role_type=r.role_type,
        )
        for r in rows
    ]


@admin_router.post(
    "/identity-providers/{provider_id}/mappings",
    response_model=IdpMappingOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_mapping(
    provider_id: uuid.UUID, body: IdpMappingIn, p: Admin, request: Request
) -> IdpMappingOut:
    svc = services(request)
    await svc.providers.get(provider_id)
    created = await svc.mappings.create(
        p.actor, provider_id, body.project_id, body.role_name_id, body.external_group
    )
    for r in await svc.mappings.list_mappings(provider_id):
        if r.mapping.id == created.id:
            return IdpMappingOut(
                id=r.mapping.id,
                provider_id=provider_id,
                external_group=r.mapping.external_group,
                project_id=r.mapping.project_id,
                project_name=r.project_name,
                role_name_id=r.mapping.role_name_id,
                role_name=r.role_name,
                role_type=r.role_type,
            )
    raise ProblemError(500, "internal", "Mapping vanished")


@admin_router.delete("/group-mappings/{mapping_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mapping(mapping_id: uuid.UUID, p: Admin, request: Request) -> None:
    await services(request).mappings.delete(p.actor, mapping_id)
