"""Project-scoped administration: members, role names, API keys (US-1.1.2, US-1.1.4)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from forge.api.deps import Principal, ProjectContext, project_permission, ready_principal, services
from forge.api.schemas import (
    AclPrincipal,
    AclPrincipalsOut,
    ApiKeyCreatedOut,
    ApiKeyCreateIn,
    ApiKeyOut,
    GroupIn,
    GroupMembersIn,
    GroupOut,
    InviteIn,
    InviteResultOut,
    MemberOut,
    MemberUpdateIn,
    MyProjectOut,
    ProjectDetailOut,
    RoleCreateIn,
    RoleOut,
    RoleUpdateIn,
)

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])
Member = Annotated[ProjectContext, Depends(project_permission())]
Manager = Annotated[ProjectContext, Depends(project_permission("project.manage"))]
Builder = Annotated[ProjectContext, Depends(project_permission("asset.write"))]
Ready = Annotated[Principal, Depends(ready_principal)]


@router.get("", response_model=list[MyProjectOut])
async def my_projects(p: Ready, request: Request) -> list[MyProjectOut]:
    if p.user is None:
        return []
    rows = await services(request).tenancy.my_projects(p.user.id)
    return [
        MyProjectOut(
            **{
                k: getattr(r.project, k)
                for k in ("id", "name", "description", "status", "created_at")
            },
            role_name=r.role_name,
            role_type=r.role_type,
            permissions=r.permissions,
        )
        for r in rows
    ]


@router.get("/{project_id}", response_model=ProjectDetailOut)
async def project_detail(ctx: Member) -> ProjectDetailOut:
    p = ctx.access.project
    return ProjectDetailOut(
        id=p.id,
        name=p.name,
        description=p.description,
        status=p.status,
        created_at=p.created_at,
        permissions=sorted(ctx.access.permissions),
        role_name=ctx.access.role_name,
        role_type=ctx.access.role_type,
    )


@router.get("/{project_id}/members", response_model=list[MemberOut])
async def list_members(ctx: Manager, request: Request, q: str | None = None) -> list[MemberOut]:
    rows = await services(request).tenancy.list_members(ctx.project_id, ctx.actor.id, q)  # type: ignore[arg-type]
    return [MemberOut.model_validate(r) for r in rows]


@router.post("/{project_id}/members", response_model=list[InviteResultOut])
async def invite_members(body: InviteIn, ctx: Manager, request: Request) -> list[InviteResultOut]:
    results = await services(request).tenancy.invite(
        ctx.actor, ctx.project_id, body.identifiers, body.role_name_id
    )
    return [InviteResultOut.model_validate(r) for r in results]


@router.patch("/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_member(
    user_id: uuid.UUID, body: MemberUpdateIn, ctx: Manager, request: Request
) -> None:
    await services(request).tenancy.update_member(
        ctx.actor, ctx.project_id, user_id, body.role_name_id, body.group_ids
    )


@router.delete("/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(user_id: uuid.UUID, ctx: Manager, request: Request) -> None:
    await services(request).tenancy.remove_member(ctx.actor, ctx.project_id, user_id)


@router.get("/{project_id}/roles", response_model=list[RoleOut])
async def list_roles(ctx: Manager, request: Request) -> list[RoleOut]:
    rows = await services(request).tenancy.list_roles(ctx.project_id, ctx.actor.id)  # type: ignore[arg-type]
    return [RoleOut(id=r.id, name=r.name, role_type=k, status=r.status) for r, k in rows]


@router.post("/{project_id}/roles", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
async def create_role(body: RoleCreateIn, ctx: Manager, request: Request) -> RoleOut:
    r = await services(request).tenancy.create_role(
        ctx.actor, ctx.project_id, body.name, body.role_type
    )
    return RoleOut(id=r.id, name=r.name, role_type=body.role_type, status=r.status)


@router.patch("/{project_id}/roles/{role_id}", response_model=RoleOut)
async def update_role(
    role_id: uuid.UUID, body: RoleUpdateIn, ctx: Manager, request: Request
) -> RoleOut:
    svc = services(request).tenancy
    r = await svc.update_role(ctx.actor, ctx.project_id, role_id, body.name, body.status)
    kinds = {role.id: k for role, k in await svc.list_roles(ctx.project_id, ctx.actor.id)}  # type: ignore[arg-type]
    return RoleOut(id=r.id, name=r.name, role_type=kinds[r.id], status=r.status)


@router.get("/{project_id}/api-keys", response_model=list[ApiKeyOut])
async def list_keys(ctx: Manager, request: Request) -> list[ApiKeyOut]:
    keys = await services(request).tenancy.list_keys(ctx.project_id, ctx.actor.id)  # type: ignore[arg-type]
    return [ApiKeyOut.model_validate(k) for k in keys]


@router.post(
    "/{project_id}/api-keys", response_model=ApiKeyCreatedOut, status_code=status.HTTP_201_CREATED
)
async def create_key(body: ApiKeyCreateIn, ctx: Manager, request: Request) -> ApiKeyCreatedOut:
    token, key = await services(request).tenancy.create_key(
        ctx.actor, ctx.project_id, body.name, body.scopes, body.expires_at, ctx.access.permissions
    )
    return ApiKeyCreatedOut(key=ApiKeyOut.model_validate(key), token=token)


@router.delete("/{project_id}/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(key_id: uuid.UUID, ctx: Manager, request: Request) -> None:
    await services(request).tenancy.revoke_key(ctx.actor, ctx.project_id, key_id)


# ---- user groups (document access, US-1.16.2)
@router.get("/{project_id}/groups", response_model=list[GroupOut])
async def list_groups(ctx: Manager, request: Request) -> list[GroupOut]:
    rows = await services(request).tenancy.list_groups(ctx.project_id, ctx.actor.id)
    return [GroupOut.model_validate(r) for r in rows]


@router.post("/{project_id}/groups", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
async def create_group(body: GroupIn, ctx: Manager, request: Request) -> GroupOut:
    tenancy = services(request).tenancy
    gid = await tenancy.save_group(ctx.actor, ctx.project_id, body.name, body.description)
    return next(
        GroupOut.model_validate(g)
        for g in await tenancy.list_groups(ctx.project_id, ctx.actor.id)
        if g.id == gid
    )


@router.put("/{project_id}/groups/{group_id}", response_model=GroupOut)
async def update_group(
    group_id: uuid.UUID, body: GroupIn, ctx: Manager, request: Request
) -> GroupOut:
    tenancy = services(request).tenancy
    await tenancy.save_group(ctx.actor, ctx.project_id, body.name, body.description, group_id)
    return next(
        GroupOut.model_validate(g)
        for g in await tenancy.list_groups(ctx.project_id, ctx.actor.id)
        if g.id == group_id
    )


@router.put("/{project_id}/groups/{group_id}/members", status_code=status.HTTP_204_NO_CONTENT)
async def set_group_members(
    group_id: uuid.UUID, body: GroupMembersIn, ctx: Manager, request: Request
) -> None:
    await services(request).tenancy.set_group_members(
        ctx.actor, ctx.project_id, group_id, body.user_ids
    )


@router.delete("/{project_id}/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(group_id: uuid.UUID, ctx: Manager, request: Request) -> None:
    await services(request).tenancy.delete_group(ctx.actor, ctx.project_id, group_id)


@router.get("/{project_id}/acl-principals", response_model=AclPrincipalsOut)
async def acl_principals(ctx: Builder, request: Request) -> AclPrincipalsOut:
    """Groups and roles that document ACLs can name (Builders choose them when uploading)."""
    p = await services(request).tenancy.acl_principals(ctx.project_id)
    return AclPrincipalsOut(
        groups=[
            AclPrincipal(id=i, name=n)
            for i, n in sorted(p.groups.items(), key=lambda x: x[1].lower())
        ],
        roles=[
            AclPrincipal(id=i, name=n)
            for i, n in sorted(p.roles.items(), key=lambda x: x[1].lower())
        ],
    )
