"""Model registry screens (E1.9): Admin Console → Model providers (platform scope) and
Projects Studio → Model Configuration (project scope). Same handlers, two scopes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from forge.api.deps import Principal, ProjectContext, project_permission, services, super_admin
from forge.core.context import Actor
from forge.core.errors import ProblemError
from forge.gateway.service import (
    AliasIn,
    AliasOut,
    ModelIn,
    ModelOut,
    ProjectGatewayConfigIn,
    ProjectGatewayConfigOut,
    ProviderIn,
    ProviderOut,
    TestConnectionIn,
    TestResult,
    UsageOut,
)

admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin-models"])
project_router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["project-models"])

Admin = Annotated[Principal, Depends(super_admin)]
Member = Annotated[ProjectContext, Depends(project_permission())]
Manager = Annotated[ProjectContext, Depends(project_permission("project.manage", writable=True))]
Reader = Annotated[ProjectContext, Depends(project_permission("project.manage"))]
Scope = uuid.UUID | None


async def _test(request: Request, scope: Scope, body: TestConnectionIn) -> TestResult:
    gw = services(request).gateway
    provider_row = gw.catalog.saved_provider(scope, body.provider_id) if body.provider_id else None
    model = gw.catalog.draft_model(
        scope,
        body.model,
        body.provider,
        provider_row,
        body.api_key.get_secret_value() if body.api_key else None,
    )
    return await gw.service.test_connection(model)


# ---- shared handlers
async def _providers(request: Request, actor: Actor, scope: Scope) -> list[ProviderOut]:
    return await services(request).gateway.catalog.list_providers(actor, scope)


async def _models(request: Request, actor: Actor, scope: Scope) -> list[ModelOut]:
    return await services(request).gateway.catalog.list_models(actor, scope)


async def _aliases(request: Request, actor: Actor, scope: Scope) -> list[AliasOut]:
    return await services(request).gateway.catalog.list_aliases(actor, scope)


# ======================================================== platform (Super Admin)
@admin_router.get("/providers", response_model=list[ProviderOut])
async def admin_list_providers(p: Admin, request: Request) -> list[ProviderOut]:
    return await _providers(request, p.actor, None)


@admin_router.post("/providers", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
async def admin_create_provider(body: ProviderIn, p: Admin, request: Request) -> ProviderOut:
    return await services(request).gateway.catalog.create_provider(p.actor, None, body)


@admin_router.put("/providers/{provider_id}", response_model=ProviderOut)
async def admin_update_provider(
    provider_id: uuid.UUID, body: ProviderIn, p: Admin, request: Request
) -> ProviderOut:
    return await services(request).gateway.catalog.update_provider(p.actor, None, provider_id, body)


@admin_router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_provider(provider_id: uuid.UUID, p: Admin, request: Request) -> None:
    await services(request).gateway.catalog.delete_provider(p.actor, None, provider_id)


@admin_router.get("/models", response_model=list[ModelOut])
async def admin_list_models(p: Admin, request: Request) -> list[ModelOut]:
    return await _models(request, p.actor, None)


@admin_router.post("/models", response_model=ModelOut, status_code=status.HTTP_201_CREATED)
async def admin_create_model(body: ModelIn, p: Admin, request: Request) -> ModelOut:
    return await services(request).gateway.catalog.create_model(p.actor, None, body)


@admin_router.put("/models/{model_id}", response_model=ModelOut)
async def admin_update_model(
    model_id: uuid.UUID, body: ModelIn, p: Admin, request: Request
) -> ModelOut:
    return await services(request).gateway.catalog.update_model(p.actor, None, model_id, body)


@admin_router.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_model(model_id: uuid.UUID, p: Admin, request: Request) -> None:
    await services(request).gateway.catalog.delete_model(p.actor, None, model_id)


@admin_router.post("/models/test", response_model=TestResult)
async def admin_test_draft(body: TestConnectionIn, _p: Admin, request: Request) -> TestResult:
    return await _test(request, None, body)


@admin_router.post("/models/{model_id}/test", response_model=TestResult)
async def admin_test_model(model_id: uuid.UUID, _p: Admin, request: Request) -> TestResult:
    gw = services(request).gateway
    return await gw.service.test_connection(gw.catalog.saved_model(None, model_id))


@admin_router.get("/aliases", response_model=list[AliasOut])
async def admin_list_aliases(p: Admin, request: Request) -> list[AliasOut]:
    return await _aliases(request, p.actor, None)


@admin_router.put("/aliases/{name:path}", response_model=AliasOut)
async def admin_set_alias(name: str, body: AliasIn, p: Admin, request: Request) -> AliasOut:
    return await services(request).gateway.catalog.set_alias(p.actor, None, name, body.model_ids)


@admin_router.delete("/aliases/{name:path}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_alias(name: str, p: Admin, request: Request) -> None:
    await services(request).gateway.catalog.delete_alias(p.actor, None, name)


# ============================================================ project (Project Admin)
@project_router.get("/providers", response_model=list[ProviderOut])
async def project_list_providers(ctx: Reader, request: Request) -> list[ProviderOut]:
    return await _providers(request, ctx.actor, ctx.project_id)


@project_router.post("/providers", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
async def project_create_provider(body: ProviderIn, ctx: Manager, request: Request) -> ProviderOut:
    return await services(request).gateway.catalog.create_provider(ctx.actor, ctx.project_id, body)


@project_router.put("/providers/{provider_id}", response_model=ProviderOut)
async def project_update_provider(
    provider_id: uuid.UUID, body: ProviderIn, ctx: Manager, request: Request
) -> ProviderOut:
    return await services(request).gateway.catalog.update_provider(
        ctx.actor, ctx.project_id, provider_id, body
    )


@project_router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def project_delete_provider(provider_id: uuid.UUID, ctx: Manager, request: Request) -> None:
    await services(request).gateway.catalog.delete_provider(ctx.actor, ctx.project_id, provider_id)


@project_router.get("/models", response_model=list[ModelOut])
async def project_list_models(ctx: Member, request: Request) -> list[ModelOut]:
    """Every member sees the models the project can use (builders pick them in prompts)."""
    return await _models(request, ctx.actor, ctx.project_id)


@project_router.post("/models", response_model=ModelOut, status_code=status.HTTP_201_CREATED)
async def project_create_model(body: ModelIn, ctx: Manager, request: Request) -> ModelOut:
    return await services(request).gateway.catalog.create_model(ctx.actor, ctx.project_id, body)


@project_router.put("/models/{model_id}", response_model=ModelOut)
async def project_update_model(
    model_id: uuid.UUID, body: ModelIn, ctx: Manager, request: Request
) -> ModelOut:
    return await services(request).gateway.catalog.update_model(
        ctx.actor, ctx.project_id, model_id, body
    )


@project_router.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def project_delete_model(model_id: uuid.UUID, ctx: Manager, request: Request) -> None:
    await services(request).gateway.catalog.delete_model(ctx.actor, ctx.project_id, model_id)


@project_router.post("/models/test", response_model=TestResult)
async def project_test_draft(body: TestConnectionIn, ctx: Manager, request: Request) -> TestResult:
    return await _test(request, ctx.project_id, body)


@project_router.post("/models/{model_id}/test", response_model=TestResult)
async def project_test_model(model_id: uuid.UUID, ctx: Manager, request: Request) -> TestResult:
    gw = services(request).gateway
    model = gw.registry.snapshot.models.get(model_id)
    if model is None or not model.usable_by(ctx.project_id):
        raise ProblemError(404, "not_found", "Model not found")
    return await gw.service.test_connection(model)


@project_router.get("/aliases", response_model=list[AliasOut])
async def project_list_aliases(ctx: Member, request: Request) -> list[AliasOut]:
    return await _aliases(request, ctx.actor, ctx.project_id)


@project_router.put("/aliases/{name:path}", response_model=AliasOut)
async def project_set_alias(name: str, body: AliasIn, ctx: Manager, request: Request) -> AliasOut:
    return await services(request).gateway.catalog.set_alias(
        ctx.actor, ctx.project_id, name, body.model_ids
    )


@project_router.delete("/aliases/{name:path}", status_code=status.HTTP_204_NO_CONTENT)
async def project_delete_alias(name: str, ctx: Manager, request: Request) -> None:
    """Removes the project override; the platform default applies again."""
    await services(request).gateway.catalog.delete_alias(ctx.actor, ctx.project_id, name)


@project_router.get("/gateway/config", response_model=ProjectGatewayConfigOut)
async def project_gateway_config(ctx: Reader, request: Request) -> ProjectGatewayConfigOut:
    return await services(request).gateway.catalog.get_project_config(ctx.actor, ctx.project_id)


@project_router.put("/gateway/config", response_model=ProjectGatewayConfigOut)
async def project_set_gateway_config(
    body: ProjectGatewayConfigIn, ctx: Manager, request: Request
) -> ProjectGatewayConfigOut:
    return await services(request).gateway.catalog.set_project_config(
        ctx.actor, ctx.project_id, body
    )


@project_router.get("/gateway/usage", response_model=UsageOut)
async def project_gateway_usage(ctx: Member, request: Request) -> UsageOut:
    return await services(request).gateway.service.usage(ctx.project_id, ctx.actor.id)
