"""Who am I: what an API key (or a session) is, for the SDK and CLI."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from forge.api.deps import Principal, ready_principal, services

router = APIRouter(prefix="/api/v1", tags=["me"])


class WhoAmI(BaseModel):
    kind: str  # api_key | session
    username: str | None = None
    project_id: uuid.UUID | None = None
    project_name: str | None = None
    permissions: list[str] = []


@router.get("/whoami", response_model=WhoAmI)
async def whoami(p: Annotated[Principal, Depends(ready_principal)], request: Request) -> WhoAmI:
    """For an API key: its project and the permissions the key really holds (scopes limited to
    its creator's). For a session: the user."""
    if p.kind == "api_key" and p.actor.api_key_project is not None:
        access = await services(request).tenancy.access(p.actor, p.actor.api_key_project)
        return WhoAmI(
            kind="api_key",
            project_id=access.project.id,
            project_name=access.project.name,
            permissions=sorted(access.permissions),
        )
    return WhoAmI(kind="session", username=p.user.username if p.user else None)
