"""Super Admin console: users, projects, audit, settings."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import ValidationError

from forge.admin.service import UnknownSettingError, audit
from forge.api.deps import Principal, services, super_admin
from forge.api.schemas import (
    AdminProjectOut,
    AuditOut,
    ProjectCreatedOut,
    ProjectCreateIn,
    ProjectDeleteIn,
    ProjectOut,
    ProjectUpdateIn,
    SettingOut,
    TemporaryPasswordOut,
    UserCreatedOut,
    UserCreateIn,
    UserOut,
    UserUpdateIn,
)
from forge.core.db import admin_scope
from forge.core.errors import ProblemError

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
Admin = Annotated[Principal, Depends(super_admin)]


# ---- users
@router.get("/users", response_model=list[UserOut])
async def list_users(_p: Admin, request: Request, q: str | None = None) -> list[UserOut]:
    return [UserOut.model_validate(u) for u in await services(request).auth.list_users(q)]


@router.post("/users", response_model=UserCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreateIn, p: Admin, request: Request) -> UserCreatedOut:
    user, temp = await services(request).auth.create_user(
        p.actor,
        username=body.username,
        email=str(body.email),
        full_name=body.full_name,
        password=body.password,
        is_super_admin=body.is_super_admin,
    )
    return UserCreatedOut(user=UserOut.model_validate(user), temporary_password=temp)


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID, body: UserUpdateIn, p: Admin, request: Request
) -> UserOut:
    changes = body.model_dump(exclude_unset=True)
    if "email" in changes:
        changes["email"] = str(changes["email"])
    return UserOut.model_validate(
        await services(request).auth.update_user(p.actor, user_id, changes)
    )


@router.post("/users/{user_id}/unlock", status_code=status.HTTP_204_NO_CONTENT)
async def unlock_user(user_id: uuid.UUID, p: Admin, request: Request) -> None:
    await services(request).auth.unlock_user(p.actor, user_id)


@router.post("/users/{user_id}/reset-password", response_model=TemporaryPasswordOut)
async def reset_password(user_id: uuid.UUID, p: Admin, request: Request) -> TemporaryPasswordOut:
    return TemporaryPasswordOut(
        temporary_password=await services(request).auth.reset_password(p.actor, user_id)
    )


# ---- projects
@router.get("/projects", response_model=list[AdminProjectOut])
async def list_projects(
    _p: Admin, request: Request, include_deleted: bool = False
) -> list[AdminProjectOut]:
    rows = await services(request).tenancy.list_all_projects(include_deleted)
    return [
        AdminProjectOut(
            **{
                k: getattr(r.project, k)
                for k in (
                    "id",
                    "name",
                    "description",
                    "status",
                    "created_at",
                    "delete_reason",
                    "deleted_at",
                )
            },
            member_count=r.member_count,
        )
        for r in rows
    ]


@router.post("/projects", response_model=ProjectCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectCreateIn, p: Admin, request: Request) -> ProjectCreatedOut:
    project, token = await services(request).tenancy.create_project(
        p.actor, body.name, body.description, body.admin_user_id
    )
    return ProjectCreatedOut(project=ProjectOut.model_validate(project), api_key=token)


@router.patch("/projects/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: uuid.UUID, body: ProjectUpdateIn, p: Admin, request: Request
) -> ProjectOut:
    project = await services(request).tenancy.update_project(
        p.actor, project_id, body.name, body.description
    )
    return ProjectOut.model_validate(project)


@router.post("/projects/{project_id}/archive", response_model=ProjectOut)
async def archive_project(project_id: uuid.UUID, p: Admin, request: Request) -> ProjectOut:
    return ProjectOut.model_validate(
        await services(request).tenancy.set_status(p.actor, project_id, "archived")
    )


@router.post("/projects/{project_id}/restore", response_model=ProjectOut)
async def restore_project(project_id: uuid.UUID, p: Admin, request: Request) -> ProjectOut:
    return ProjectOut.model_validate(
        await services(request).tenancy.set_status(p.actor, project_id, "active")
    )


@router.post("/projects/{project_id}/delete", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID, body: ProjectDeleteIn, p: Admin, request: Request
) -> None:
    await services(request).tenancy.delete_project(
        p.actor, project_id, body.reason, body.confirm_name
    )


# ---- audit (platform-wide read: forge_admin path)
@router.get("/audit", response_model=list[AuditOut])
async def query_audit(
    p: Admin,
    request: Request,
    project_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    action: str | None = None,
    target_type: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 100,
) -> list[AuditOut]:
    async with admin_scope(request.app.state.sessions, p.actor.id) as s:
        rows = await audit.query(
            s,
            project_id=project_id,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            since=since,
            until=until,
            limit=limit,
        )
        return [AuditOut.model_validate(r) for r in rows]


# ---- runtime settings (ADR-007)
@router.get("/settings", response_model=list[SettingOut])
async def list_settings(_p: Admin, request: Request) -> list[SettingOut]:
    snap = services(request).store.snapshot()
    return [
        SettingOut(key=k, value=v["value"], version=v["version"]) for k, v in sorted(snap.items())
    ]


@router.put("/settings/{key}", response_model=SettingOut)
async def put_setting(key: str, body: dict[str, Any], p: Admin, request: Request) -> SettingOut:
    store = services(request).store
    try:
        async with admin_scope(request.app.state.sessions, p.actor.id) as s:  # platform rows
            model = await store.set(s, p.actor, key, body)
    except UnknownSettingError as exc:
        raise ProblemError(404, "unknown_setting", f"Unknown setting '{key}'") from exc
    except ValidationError as exc:
        raise ProblemError(
            422,
            "invalid_setting",
            "Invalid setting value",
            "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()),
        ) from exc
    await store.reload(key)  # this process applies it immediately; others via NOTIFY
    return SettingOut(key=key, value=model.model_dump(), version=store.snapshot()[key]["version"])
