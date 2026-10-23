"""What a project exposes through its MCP server: the list, kept by Builders (E3.5)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import audit
from forge.agents.models import Agent
from forge.core.context import Actor
from forge.core.db import project_scope
from forge.core.errors import ProblemError
from forge.mcp_server.models import McpExposure
from forge.prompts.models import Prompt
from forge.rag.models import DataCollection

NOT_FOUND = ProblemError(404, "not_found", "Exposure not found")
NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
Kind = Literal["agent", "prompt", "collection"]


class ExposureIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agent_id: uuid.UUID | None = None
    prompt_id: uuid.UUID | None = None
    collection_id: uuid.UUID | None = None
    name: str | None = Field(
        default=None, max_length=64, description="Tool or prompt name; defaults to the asset's"
    )
    description: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _one(self) -> ExposureIn:
        if sum(x is not None for x in (self.agent_id, self.prompt_id, self.collection_id)) != 1:
            raise ValueError("give exactly one of agent_id, prompt_id, collection_id")
        if self.name is not None and not NAME.match(self.name):
            raise ValueError("the name has 1-64 letters, digits, _ or -")
        return self


class ExposureUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=1000)
    enabled: bool | None = None

    @model_validator(mode="after")
    def _name(self) -> ExposureUpdateIn:
        if self.name is not None and not NAME.match(self.name):
            raise ValueError("the name has 1-64 letters, digits, _ or -")
        return self


class ExposureOut(BaseModel):
    id: uuid.UUID
    kind: Kind
    asset_id: uuid.UUID
    asset_name: str
    name: str
    description: str
    enabled: bool
    created_at: datetime


def tool_name(text: str) -> str:
    """A name MCP clients accept, from an asset's display name."""
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", text.strip()).strip("_")
    return (name or "asset")[:64]


class McpExposures:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @staticmethod
    async def _asset(s: AsyncSession, body: ExposureIn) -> tuple[Kind, uuid.UUID, str, str]:
        if body.agent_id:
            a = await s.get(Agent, body.agent_id)
            if a is None:
                raise ProblemError(422, "unknown_asset", "Agent not found")
            return "agent", a.id, a.name, a.description
        if body.prompt_id:
            p = await s.get(Prompt, body.prompt_id)
            if p is None:
                raise ProblemError(422, "unknown_asset", "Prompt not found")
            return "prompt", p.id, p.name, p.description
        assert body.collection_id
        c = await s.get(DataCollection, body.collection_id)
        if c is None:
            raise ProblemError(422, "unknown_asset", "Data collection not found")
        return "collection", c.id, c.name, c.description

    @staticmethod
    async def _out(s: AsyncSession, e: McpExposure) -> ExposureOut:
        kind: Kind
        asset: Agent | Prompt | DataCollection | None
        if e.agent_id:
            kind, asset = "agent", await s.get(Agent, e.agent_id)
        elif e.prompt_id:
            kind, asset = "prompt", await s.get(Prompt, e.prompt_id)
        else:
            assert e.collection_id
            kind, asset = "collection", await s.get(DataCollection, e.collection_id)
        assert asset is not None
        return ExposureOut(
            id=e.id,
            kind=kind,
            asset_id=asset.id,
            asset_name=asset.name,
            name=e.name,
            description=e.description,
            enabled=e.enabled,
            created_at=e.created_at,
        )

    async def list(self, actor: Actor, project_id: uuid.UUID) -> list[ExposureOut]:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            rows = (await s.execute(select(McpExposure).order_by(McpExposure.name))).scalars().all()
            return [await self._out(s, e) for e in rows]

    async def add(self, actor: Actor, project_id: uuid.UUID, body: ExposureIn) -> ExposureOut:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            kind, asset_id, asset_name, asset_description = await self._asset(s, body)
            row = McpExposure(
                project_id=project_id,
                name=body.name or tool_name(asset_name),
                description=(
                    body.description if body.description is not None else asset_description
                )[:1000],
                agent_id=asset_id if kind == "agent" else None,
                prompt_id=asset_id if kind == "prompt" else None,
                collection_id=asset_id if kind == "collection" else None,
                created_by=actor.id,
            )
            s.add(row)
            try:
                await s.flush()
            except IntegrityError as exc:
                raise ProblemError(
                    409, "already_exposed", "This asset is exposed already, or the name is taken"
                ) from exc
            await audit.record(
                s,
                actor,
                "mcp.expose",
                "mcp_exposure",
                row.id,
                {"kind": kind, "asset": str(asset_id), "name": row.name},
                project_id=project_id,
            )
            return await self._out(s, row)

    async def update(
        self, actor: Actor, project_id: uuid.UUID, exposure_id: uuid.UUID, body: ExposureUpdateIn
    ) -> ExposureOut:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            row = await s.get(McpExposure, exposure_id)
            if row is None:
                raise NOT_FOUND
            if body.name is not None:
                row.name = body.name
            if body.description is not None:
                row.description = body.description
            if body.enabled is not None:
                row.enabled = body.enabled
            try:
                await s.flush()
            except IntegrityError as exc:
                raise ProblemError(409, "name_taken", "Another exposure has this name") from exc
            await audit.record(
                s,
                actor,
                "mcp.update",
                "mcp_exposure",
                row.id,
                body.model_dump(exclude_none=True),
                project_id=project_id,
            )
            return await self._out(s, row)

    async def remove(self, actor: Actor, project_id: uuid.UUID, exposure_id: uuid.UUID) -> None:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            row = await s.get(McpExposure, exposure_id)
            if row is None:
                raise NOT_FOUND
            await audit.record(
                s,
                actor,
                "mcp.unexpose",
                "mcp_exposure",
                row.id,
                {"name": row.name},
                project_id=project_id,
            )
            await s.delete(row)
