"""Agentic AI Studio (E1.18, E1.19, E1.20): agents with immutable versions and tags,
conversations, runs and their steps, feedback and usage totals.

Every query runs in the caller's project scope (RLS, ADR-002). Running an agent is the job of
`forge.agents.runtime` inside a Temporal activity (ADR-004); this module creates the run and
starts the workflow.
"""

from __future__ import annotations

import base64
import binascii
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, cast, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import DATE
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import audit
from forge.agents.models import (
    Agent,
    AgentRun,
    AgentTag,
    AgentVersion,
    Conversation,
    RunFeedback,
    RunStep,
)
from forge.agents.schemas import (
    AgentConfig,
    AgentCreateIn,
    AgentDetail,
    AgentOut,
    AgentPage,
    AgentRunDetail,
    AgentRunOut,
    AgentRunPage,
    AgentStepOut,
    AgentUpdateIn,
    AgentUsageDay,
    AgentUsageOut,
    AgentVersionIn,
    AgentVersionOut,
    AvailableAgent,
    ConversationDetail,
    ConversationIn,
    ConversationOut,
    FeedbackIn,
    FeedbackOut,
    LibraryTool,
    MessageOut,
    RagTool,
)
from forge.agents.starter import RunStarter
from forge.core.context import Actor
from forge.core.db import project_scope
from forge.core.errors import ProblemError
from forge.core.models import utcnow
from forge.gateway.service import Gateway
from forge.observability.service import current_trace_id
from forge.prompts.service import PromptStudio, ReferenceOut
from forge.rag.service import Rag
from forge.tools.service import ToolReferenceOut, ToolStudio

CHAT_KINDS = frozenset({"chat"})
TAG_ORDER = ("draft", "staging", "prod")
NOT_FOUND = ProblemError(404, "not_found", "Agent not found")
RUN_NOT_FOUND = ProblemError(404, "run_not_found", "Run not found")
TERMINAL = ("succeeded", "failed", "cancelled")
_SLUG = re.compile(r"[^a-z0-9]+")


def _cursor(at: datetime, row_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{at.isoformat()}|{row_id}".encode()).decode().rstrip("=")


def _uncursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        stamp, row_id = raw.split("|", 1)
        return datetime.fromisoformat(stamp), uuid.UUID(row_id)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise ProblemError(400, "invalid_cursor", "Invalid pagination cursor") from exc


def tool_name_for(pipeline_name: str) -> str:
    slug = _SLUG.sub("_", pipeline_name.lower()).strip("_")[:40] or "documents"
    if not slug[0].isalpha():
        slug = f"kb_{slug}"
    return f"search_{slug}"[:48]


class AgentStudio:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        gateway: Gateway,
        prompts: PromptStudio,
        rag: Rag,
        starter: RunStarter,
        tools: ToolStudio,
    ) -> None:
        self._sessions = sessions
        self._gateway = gateway
        self._prompts = prompts
        self._rag = rag
        self._starter = starter
        self._tools = tools
        self._reference_checks: list[
            Callable[[AsyncSession, uuid.UUID], Awaitable[list[ReferenceOut]]]
        ] = []
        tools.add_reference_check(self.tool_references)
        prompts.add_reference_check(self.prompt_references)
        rag.studio.add_reference_check(self.pipeline_references)

    @staticmethod
    async def name_in(s: AsyncSession, item_id: uuid.UUID) -> str | None:
        """Name of one item inside an open session (used by other modules' reference checks)."""
        return (await s.execute(select(Agent.name).where(Agent.id == item_id))).scalar_one_or_none()

    def add_reference_check(
        self, check: Callable[[AsyncSession, uuid.UUID], Awaitable[list[ReferenceOut]]]
    ) -> None:
        """Modules that use agents (use cases) register who references one (blocks delete)."""
        self._reference_checks.append(check)

    @asynccontextmanager
    async def _session(self, actor: Actor, project_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            yield s

    # ------------------------------------------------------------ helpers
    @staticmethod
    async def _agent(s: AsyncSession, agent_id: uuid.UUID, *, lock: bool = False) -> Agent:
        stmt = select(Agent).where(Agent.id == agent_id)
        if lock:
            stmt = stmt.with_for_update()
        row = (await s.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise NOT_FOUND
        return row

    @staticmethod
    async def _version(s: AsyncSession, agent: Agent, number: int | None) -> AgentVersion:
        n = number or agent.latest_version
        row = (
            await s.execute(
                select(AgentVersion).where(
                    AgentVersion.agent_id == agent.id, AgentVersion.version == n
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise ProblemError(404, "version_not_found", f"Version {n} not found")
        return row

    @staticmethod
    async def _tags(s: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, int]]:
        out: dict[uuid.UUID, dict[str, int]] = {i: {} for i in ids}
        if ids:
            rows = await s.execute(
                select(AgentTag.agent_id, AgentTag.tag, AgentTag.version).where(
                    AgentTag.agent_id.in_(ids)
                )
            )
            for aid, tag, version in rows.all():
                out[aid][tag] = version
        return out

    @staticmethod
    def _out(a: Agent, tags: dict[str, int], config: dict[str, Any]) -> AgentOut:
        return AgentOut(
            id=a.id,
            name=a.name,
            description=a.description,
            status="published" if "prod" in tags else "draft",
            latest_version=a.latest_version,
            published_version=tags.get("prod"),
            tags=tags,
            model=str(config.get("model", "")),
            tools=len(config.get("tools", [])),
            created_at=a.created_at,
            updated_at=a.updated_at,
        )

    @staticmethod
    def _version_out(v: AgentVersion, tags: dict[str, int]) -> AgentVersionOut:
        return AgentVersionOut(
            version=v.version,
            config=AgentConfig.model_validate(v.config),
            note=v.note,
            tags=[t for t in TAG_ORDER if tags.get(t) == v.version],
            created_at=v.created_at,
            created_by=v.created_by,
        )

    async def _detail(self, s: AsyncSession, a: Agent, number: int | None = None) -> AgentDetail:
        v = await self._version(s, a, number)
        tags = (await self._tags(s, [a.id]))[a.id]
        base = self._out(a, tags, v.config)
        return AgentDetail(**base.model_dump(), version=self._version_out(v, tags))

    async def _move_tag(
        self, s: AsyncSession, actor: Actor, a: Agent, tag: str, version: int
    ) -> None:
        row = await s.get(AgentTag, (a.id, tag))
        if row is None:
            s.add(
                AgentTag(
                    agent_id=a.id,
                    project_id=a.project_id,
                    tag=tag,
                    version=version,
                    updated_by=actor.id,
                )
            )
        else:
            row.version, row.updated_by = version, actor.id
        await s.flush()

    @staticmethod
    async def _name_free(s: AsyncSession, name: str, exclude: uuid.UUID | None = None) -> None:
        stmt = select(Agent.id).where(func.lower(Agent.name) == name.lower())
        if exclude:
            stmt = stmt.where(Agent.id != exclude)
        if (await s.execute(stmt)).first():
            raise ProblemError(409, "name_taken", "An agent with this name exists")

    async def _pin(
        self, actor: Actor, project_id: uuid.UUID, cfg: AgentConfig
    ) -> tuple[AgentConfig, int]:
        """Validate the model and resolve what the agent references to concrete versions, so the
        saved version never changes under it (US-1.18.1 #1)."""
        resolution = self._gateway.registry.resolve(project_id, cfg.model, CHAT_KINDS)
        if cfg.tools and not any("tools" in m.capabilities for m in resolution.chain):
            raise ProblemError(
                422,
                "model_without_tools",
                "No model behind this alias supports tool calling",
                "Enable the 'tools' capability on a model of this alias, or remove the tools",
            )
        data = cfg.model_dump(mode="json")
        ref = cfg.prompt
        if ref.version is None:
            _, pv = await self._prompts.for_use(actor, project_id, ref.prompt_id)
        else:
            pv = await self._prompts.version(actor, project_id, ref.prompt_id, ref.version)
        data["prompt"]["version"] = pv.version
        known = {v.name for v in pv.variables}
        unknown = sorted(set(ref.variables) - known)
        if unknown:
            raise ProblemError(
                422, "unknown_variables", "The prompt has no such variables", unknown
            )
        tools: list[dict[str, Any]] = []
        for tool in cfg.tools:
            if isinstance(tool, RagTool):
                p = await self._rag.studio.agent_pipeline(
                    actor, project_id, tool.pipeline_id, tool.version
                )
                entry = tool.model_dump(mode="json")
                entry["version"] = p.version
                entry["name"] = tool.name or tool_name_for(p.name)
                entry["description"] = tool.description or (
                    p.description or f"Search the documents of '{p.name}'"
                )
                tools.append(entry)
            elif isinstance(tool, LibraryTool):
                v = await self._tools.resolve_library(actor, project_id, tool.tool_id, tool.version)
                entry = tool.model_dump(mode="json")
                entry["version"] = v.version
                entry["name"] = tool.name or await self._tools.tool_name(
                    actor, project_id, tool.tool_id
                )
                tools.append(entry)
            else:
                tools.append(tool.model_dump(mode="json"))
        data["tools"] = tools
        pinned = AgentConfig.model_validate(data)  # re-checks unique tool names
        return pinned, pv.version

    async def _add_version(
        self, s: AsyncSession, actor: Actor, a: Agent, cfg: AgentConfig, note: str
    ) -> AgentVersion:
        pinned, prompt_version = await self._pin(actor, a.project_id, cfg)
        a.latest_version += 1
        a.updated_by = actor.id
        v = AgentVersion(
            project_id=a.project_id,
            agent_id=a.id,
            version=a.latest_version,
            config=pinned.model_dump(mode="json"),
            prompt_id=pinned.prompt.prompt_id,
            prompt_version=prompt_version,
            note=note,
            created_by=actor.id,
        )
        s.add(v)
        await s.flush()
        await self._move_tag(s, actor, a, "draft", v.version)
        return v

    @staticmethod
    async def _insert(s: AsyncSession, a: Agent) -> None:
        s.add(a)
        try:
            await s.flush()
        except IntegrityError as exc:
            raise ProblemError(409, "name_taken", "An agent with this name exists") from exc

    # ------------------------------------------------------------ library
    async def list_agents(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        *,
        status: str | None = None,
        q: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> AgentPage:
        limit = max(1, min(limit, 200))
        published = exists().where(AgentTag.agent_id == Agent.id, AgentTag.tag == "prod")
        stmt = (
            select(Agent, AgentVersion.config)
            .join(
                AgentVersion,
                and_(
                    AgentVersion.agent_id == Agent.id,
                    AgentVersion.version == Agent.latest_version,
                ),
            )
            .order_by(Agent.updated_at.desc(), Agent.id.desc())
            .limit(limit + 1)
        )
        if status == "published":
            stmt = stmt.where(published)
        elif status == "draft":
            stmt = stmt.where(~published)
        if q:
            pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            stmt = stmt.where(Agent.name.ilike(pattern, escape="\\"))
        if since:
            stmt = stmt.where(Agent.updated_at >= since)
        if until:
            stmt = stmt.where(Agent.updated_at < until)
        if cursor:
            at, aid = _uncursor(cursor)
            stmt = stmt.where(
                or_(Agent.updated_at < at, and_(Agent.updated_at == at, Agent.id < aid))
            )
        async with self._session(actor, project_id) as s:
            rows = (await s.execute(stmt)).all()
            page = rows[:limit]
            tags = await self._tags(s, [a.id for a, _ in page])
        items = [self._out(a, tags[a.id], cfg) for a, cfg in page]
        nxt = _cursor(page[-1][0].updated_at, page[-1][0].id) if len(rows) > limit else None
        return AgentPage(items=items, next_cursor=nxt)

    async def get(self, actor: Actor, project_id: uuid.UUID, agent_id: uuid.UUID) -> AgentDetail:
        async with self._session(actor, project_id) as s:
            return await self._detail(s, await self._agent(s, agent_id))

    async def create(self, actor: Actor, project_id: uuid.UUID, body: AgentCreateIn) -> AgentDetail:
        async with self._session(actor, project_id) as s:
            await self._name_free(s, body.name)
            a = Agent(
                project_id=project_id,
                name=body.name,
                description=body.description,
                created_by=actor.id,
                updated_by=actor.id,
            )
            await self._insert(s, a)
            await self._add_version(s, actor, a, body.config, "")
            await audit.record(
                s,
                actor,
                "agent.create",
                "agent",
                a.id,
                {"name": body.name, "model": body.config.model},
                project_id=project_id,
            )
            return await self._detail(s, a)

    async def update(
        self, actor: Actor, project_id: uuid.UUID, agent_id: uuid.UUID, body: AgentUpdateIn
    ) -> AgentDetail:
        async with self._session(actor, project_id) as s:
            a = await self._agent(s, agent_id, lock=True)
            if body.name is not None and body.name.lower() != a.name.lower():
                await self._name_free(s, body.name, a.id)
                a.name = body.name
            if body.description is not None:
                a.description = body.description
            a.updated_by, a.updated_at = actor.id, utcnow()
            try:
                await s.flush()
            except IntegrityError as exc:
                raise ProblemError(409, "name_taken", "An agent with this name exists") from exc
            await audit.record(
                s, actor, "agent.update", "agent", a.id, body.model_dump(exclude_none=True),
                project_id=project_id,
            )  # fmt: skip
            return await self._detail(s, a)

    async def delete(self, actor: Actor, project_id: uuid.UUID, agent_id: uuid.UUID) -> None:
        async with self._session(actor, project_id) as s:
            a = await self._agent(s, agent_id, lock=True)
            refs: list[ReferenceOut] = []
            for check in self._reference_checks:
                refs += await check(s, a.id)
            if refs:
                raise ProblemError(
                    409,
                    "agent_in_use",
                    "Use cases use this agent",
                    [r.model_dump(mode="json") for r in refs],
                )
            await audit.record(
                s,
                actor,
                "agent.delete",
                "agent",
                a.id,
                {"name": a.name, "versions": a.latest_version},
                project_id=project_id,
            )
            await s.delete(a)  # versions, conversations and runs go with it

    async def duplicate(
        self, actor: Actor, project_id: uuid.UUID, agent_id: uuid.UUID, name: str
    ) -> AgentDetail:
        async with self._session(actor, project_id) as s:
            src = await self._agent(s, agent_id)
            v = await self._version(s, src, None)
            await self._name_free(s, name)
            a = Agent(
                project_id=project_id,
                name=name,
                description=src.description,
                created_by=actor.id,
                updated_by=actor.id,
            )
            await self._insert(s, a)
            await self._add_version(s, actor, a, AgentConfig.model_validate(v.config), "")
            await audit.record(
                s,
                actor,
                "agent.duplicate",
                "agent",
                a.id,
                {"from": src.name},
                project_id=project_id,
            )
            return await self._detail(s, a)

    # ------------------------------------------------------------ versions and tags
    async def versions(
        self, actor: Actor, project_id: uuid.UUID, agent_id: uuid.UUID
    ) -> list[AgentVersionOut]:
        async with self._session(actor, project_id) as s:
            a = await self._agent(s, agent_id)
            tags = (await self._tags(s, [a.id]))[a.id]
            rows = await s.execute(
                select(AgentVersion)
                .where(AgentVersion.agent_id == a.id)
                .order_by(AgentVersion.version.desc())
            )
            return [self._version_out(r, tags) for r in rows.scalars()]

    async def save_version(
        self, actor: Actor, project_id: uuid.UUID, agent_id: uuid.UUID, body: AgentVersionIn
    ) -> AgentDetail:
        async with self._session(actor, project_id) as s:
            a = await self._agent(s, agent_id, lock=True)
            v = await self._add_version(s, actor, a, body.config, body.note)
            await audit.record(
                s,
                actor,
                "agent.version",
                "agent",
                a.id,
                {"version": v.version},
                project_id=project_id,
            )
            return await self._detail(s, a)

    async def set_tag(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        tag: str,
        number: int | None,
    ) -> AgentDetail:
        async with self._session(actor, project_id) as s:
            a = await self._agent(s, agent_id, lock=True)
            row = await self._version(s, a, number)
            await self._move_tag(s, actor, a, tag, row.version)
            a.updated_by, a.updated_at = actor.id, utcnow()
            await audit.record(
                s,
                actor,
                "agent.publish" if tag == "prod" else "agent.tag",
                "agent",
                a.id,
                {"tag": tag, "version": row.version},
                project_id=project_id,
            )
            await s.flush()
            return await self._detail(s, a)

    async def remove_tag(
        self, actor: Actor, project_id: uuid.UUID, agent_id: uuid.UUID, tag: str
    ) -> AgentDetail:
        if tag == "draft":
            raise ProblemError(400, "tag_fixed", "The draft tag follows the latest version")
        async with self._session(actor, project_id) as s:
            a = await self._agent(s, agent_id, lock=True)
            row = await s.get(AgentTag, (a.id, tag))
            if row is not None:
                await s.delete(row)
                await audit.record(
                    s,
                    actor,
                    "agent.unpublish" if tag == "prod" else "agent.untag",
                    "agent",
                    a.id,
                    {"tag": tag},
                    project_id=project_id,
                )
                await s.flush()
            return await self._detail(s, a)

    # ------------------------------------------------------------ references
    async def prompt_references(self, s: AsyncSession, prompt_id: uuid.UUID) -> list[ReferenceOut]:
        """Agents whose latest or tagged version uses the prompt (blocks prompt delete)."""
        tagged = select(AgentTag.version).where(AgentTag.agent_id == Agent.id)
        rows = (
            await s.execute(
                select(Agent.id, Agent.name)
                .join(AgentVersion, AgentVersion.agent_id == Agent.id)
                .where(
                    AgentVersion.prompt_id == prompt_id,
                    or_(
                        AgentVersion.version == Agent.latest_version,
                        AgentVersion.version.in_(tagged.scalar_subquery()),
                    ),
                )
                .distinct()
            )
        ).all()
        return [ReferenceOut(kind="agent", id=i, name=n) for i, n in rows]

    async def tool_references(self, s: AsyncSession, tool_id: uuid.UUID) -> list[ToolReferenceOut]:
        tagged = select(AgentTag.version).where(AgentTag.agent_id == Agent.id)
        rows = (
            await s.execute(
                select(Agent.id, Agent.name)
                .join(AgentVersion, AgentVersion.agent_id == Agent.id)
                .where(
                    AgentVersion.config.contains(
                        {"tools": [{"kind": "library", "tool_id": str(tool_id)}]}
                    ),
                    or_(
                        AgentVersion.version == Agent.latest_version,
                        AgentVersion.version.in_(tagged.scalar_subquery()),
                    ),
                )
                .distinct()
            )
        ).all()
        return [ToolReferenceOut(kind="agent", id=i, name=n) for i, n in rows]

    async def pipeline_references(
        self, s: AsyncSession, pipeline_id: uuid.UUID
    ) -> list[ReferenceOut]:
        tagged = select(AgentTag.version).where(AgentTag.agent_id == Agent.id)
        rows = (
            await s.execute(
                select(Agent.id, Agent.name)
                .join(AgentVersion, AgentVersion.agent_id == Agent.id)
                .where(
                    AgentVersion.config.contains({"tools": [{"pipeline_id": str(pipeline_id)}]}),
                    or_(
                        AgentVersion.version == Agent.latest_version,
                        AgentVersion.version.in_(tagged.scalar_subquery()),
                    ),
                )
                .distinct()
            )
        ).all()
        return [ReferenceOut(kind="agent", id=i, name=n) for i, n in rows]

    # ------------------------------------------------------------ export / import (bundles)
    async def export_agent(
        self, actor: Actor, project_id: uuid.UUID, agent_id: uuid.UUID, scope: str = "all"
    ) -> tuple[dict[str, Any], set[uuid.UUID], set[uuid.UUID]]:
        """The agent with the chosen versions; the prompt and the pipelines are referenced by
        name (ids differ between installations) and re-pinned to versions on import. Returns
        the item and the ids of the prompts and pipelines it depends on (ADR-018)."""
        async with self._session(actor, project_id) as s:
            a = await self._agent(s, agent_id)
            tags = (await self._tags(s, [a.id]))[a.id]
            rows = list(
                (
                    await s.execute(
                        select(AgentVersion)
                        .where(AgentVersion.agent_id == a.id)
                        .order_by(AgentVersion.version)
                    )
                ).scalars()
            )
        if scope == "published":
            rows = [r for r in rows if r.version == tags.get("prod")] or rows[-1:]
        elif scope == "latest":
            rows = rows[-1:]
        prompts: set[uuid.UUID] = set()
        pipelines: set[uuid.UUID] = set()
        names: dict[str, str] = {}
        versions = []
        for r in rows:
            cfg = AgentConfig.model_validate(r.config).model_dump(mode="json")
            ref = cfg["prompt"]
            prompts.add(uuid.UUID(ref["prompt_id"]))
            if ref["prompt_id"] not in names:
                names[ref["prompt_id"]], _ = await self._prompts.for_use(
                    actor, project_id, uuid.UUID(ref["prompt_id"])
                )
            cfg["prompt"] = {"name": names[ref["prompt_id"]], "variables": ref["variables"]}
            tools = []
            for t in cfg["tools"]:
                if t["kind"] == "rag":
                    pid = t["pipeline_id"]
                    pipelines.add(uuid.UUID(pid))
                    if pid not in names:
                        info = await self._rag.studio.agent_pipeline(
                            actor, project_id, uuid.UUID(pid), t["version"]
                        )
                        names[pid] = info.name
                    t = {k: v for k, v in t.items() if k not in ("pipeline_id", "version")}
                    t["pipeline"] = names[pid]
                tools.append(t)
            cfg["tools"] = tools
            versions.append({"version": r.version, "config": cfg, "note": r.note})
        numbers = {r.version for r in rows}
        item = {
            "kind": "agent",
            "name": a.name,
            "description": a.description,
            "versions": versions,
            "tags": {t: v for t, v in tags.items() if v in numbers and t != "draft"},
        }
        return item, prompts, pipelines

    async def find_agent(self, actor: Actor, project_id: uuid.UUID, name: str) -> uuid.UUID | None:
        async with self._session(actor, project_id) as s:
            return (
                await s.execute(select(Agent.id).where(func.lower(Agent.name) == name.lower()))
            ).scalar_one_or_none()

    async def _free_copy_name(self, actor: Actor, project_id: uuid.UUID, name: str) -> str:
        async with self._session(actor, project_id) as s:
            taken = {n.lower() for n in (await s.execute(select(Agent.name))).scalars()}
        for n in range(1, 1000):
            candidate = f"{name} (imported{'' if n == 1 else f' {n}'})"[:120]
            if candidate.lower() not in taken:
                return candidate
        raise ProblemError(409, "name_taken", "No free name for the imported agent")

    async def import_agent(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        item: dict[str, Any],
        policy: str,
        *,
        prompts: dict[str, uuid.UUID],
        pipelines: dict[str, uuid.UUID],
        dry_run: bool = False,
        source: str = "bundle",
        assume_models: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        """Same policies as prompts. The prompt and the pipelines are resolved by name in this
        project and pinned to their published (else latest) version here, because version
        numbers differ between projects. Tags are applied only to agents the import creates."""
        name = str(item["name"])
        result: dict[str, Any] = {"kind": "agent", "name": name, "versions": 0, "warnings": []}
        configs: list[AgentConfig] = []
        for v in item.get("versions", []):
            raw = dict(v.get("config") or {})
            prompt = dict(raw.get("prompt") or {})
            pname = str(prompt.pop("name", "") or "")
            if pname.lower() not in prompts:
                return result | {"action": "error", "error": f"prompt '{pname}' not found"}
            tools: list[dict[str, Any]] = []
            for t in raw.get("tools") or []:
                t = dict(t)
                if t.get("kind") == "rag":
                    ref = str(t.pop("pipeline", "") or "")
                    if ref.lower() not in pipelines:
                        return result | {"action": "error", "error": f"pipeline '{ref}' not found"}
                    t["pipeline_id"] = str(pipelines[ref.lower()])
                tools.append(t)
            try:
                cfg = AgentConfig.model_validate(
                    raw
                    | {
                        "prompt": prompt | {"prompt_id": str(prompts[pname.lower()])},
                        "tools": tools,
                    }
                )
                if cfg.model.lower() not in assume_models:
                    self._gateway.registry.resolve(project_id, cfg.model, CHAT_KINDS)
            except ProblemError as exc:
                return result | {"action": "error", "error": f"{exc.code}: {exc.title}"}
            except ValueError as exc:
                return result | {"action": "error", "error": str(exc)[:300]}
            configs.append(cfg)
        if not configs:
            return result | {"action": "error", "error": "no versions in the bundle"}
        existing = await self.find_agent(actor, project_id, name)
        target, action = name, "created"
        if existing is not None:
            if policy == "skip":
                return result | {"action": "skipped", "target": name}
            if policy == "rename":
                target, action = await self._free_copy_name(actor, project_id, name), "renamed"
            else:
                action = "new_versions"
        result |= {"action": action, "target": target, "versions": len(configs)}
        if dry_run:
            return result
        async with self._session(actor, project_id) as s:
            if action == "new_versions":
                assert existing is not None
                a = await self._agent(s, existing, lock=True)
            else:
                a = Agent(
                    project_id=project_id,
                    name=target,
                    description=str(item.get("description") or "Imported")[:2000],
                    created_by=actor.id,
                    updated_by=actor.id,
                )
                await self._insert(s, a)
            mapping: dict[int, int] = {}
            for v, cfg in zip(item["versions"], configs, strict=True):
                note = str(v.get("note") or "") + f" (imported from {source} v{v.get('version')})"
                row = await self._add_version(s, actor, a, cfg, note.strip()[:500])
                mapping[int(v.get("version", 0))] = row.version
            if action != "new_versions":
                for tag, old in (item.get("tags") or {}).items():
                    if tag in ("staging", "prod") and old in mapping:
                        await self._move_tag(s, actor, a, tag, mapping[old])
            elif item.get("tags"):
                result["warnings"].append("tags not applied to an existing agent; publish it")
            await audit.record(
                s,
                actor,
                "agent.import",
                "agent",
                a.id,
                {"source": source, "versions": sorted(mapping.values()), "action": action},
                project_id=project_id,
            )
            result["id"] = str(a.id)
        return result

    # ------------------------------------------------------------ chat
    async def available(
        self, actor: Actor, project_id: uuid.UUID, *, include_drafts: bool
    ) -> list[AvailableAgent]:
        async with self._session(actor, project_id) as s:
            rows = (
                await s.execute(
                    select(Agent, AgentTag.version)
                    .join(AgentTag, and_(AgentTag.agent_id == Agent.id, AgentTag.tag == "prod"))
                    .order_by(Agent.name)
                )
            ).all()
            out = [
                AvailableAgent(
                    id=a.id, name=a.name, description=a.description, version=v, published=True
                )
                for a, v in rows
            ]
            if include_drafts:
                published = {a.id for a, _ in rows}
                drafts = (await s.execute(select(Agent).order_by(Agent.name))).scalars().all()
                out += [
                    AvailableAgent(
                        id=a.id,
                        name=a.name,
                        description=a.description,
                        version=a.latest_version,
                        published=False,
                    )
                    for a in drafts
                    if a.id not in published
                ]
                out.sort(key=lambda x: x.name.lower())
            return out

    async def create_conversation(
        self, actor: Actor, project_id: uuid.UUID, body: ConversationIn, *, is_builder: bool
    ) -> ConversationOut:
        async with self._session(actor, project_id) as s:
            a = await self._agent(s, body.agent_id)
            tags = (await self._tags(s, [a.id]))[a.id]
            if is_builder:
                number = body.version or a.latest_version
            else:
                if "prod" not in tags:
                    raise ProblemError(409, "agent_not_published", "The agent is not published yet")
                number = tags["prod"]
            v = await self._version(s, a, number)
            conv = Conversation(
                project_id=project_id,
                agent_id=a.id,
                agent_version=v.version,
                title=body.title,
                created_by=actor.id,
            )
            s.add(conv)
            await s.flush()
            return self._conversation_out(conv, a.name)

    @staticmethod
    def _conversation_out(c: Conversation, agent_name: str) -> ConversationOut:
        return ConversationOut(
            id=c.id,
            agent_id=c.agent_id,
            agent_name=agent_name,
            agent_version=c.agent_version,
            title=c.title,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )

    async def conversations(
        self, actor: Actor, project_id: uuid.UUID, agent_id: uuid.UUID | None, limit: int = 50
    ) -> list[ConversationOut]:
        stmt = (
            select(Conversation, Agent.name)
            .join(Agent, Agent.id == Conversation.agent_id)
            .where(Conversation.created_by == actor.id)
            .order_by(Conversation.updated_at.desc())
            .limit(max(1, min(limit, 200)))
        )
        if agent_id:
            stmt = stmt.where(Conversation.agent_id == agent_id)
        async with self._session(actor, project_id) as s:
            return [self._conversation_out(c, n) for c, n in (await s.execute(stmt)).all()]

    async def _own_conversation(
        self, s: AsyncSession, actor: Actor, conversation_id: uuid.UUID
    ) -> Conversation:
        conv = await s.get(Conversation, conversation_id)
        if conv is None or conv.created_by != actor.id:
            raise ProblemError(404, "not_found", "Conversation not found")
        return conv

    async def conversation(
        self, actor: Actor, project_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> ConversationDetail:
        async with self._session(actor, project_id) as s:
            conv = await self._own_conversation(s, actor, conversation_id)
            agent = await s.get(Agent, conv.agent_id)
            runs = (
                (
                    await s.execute(
                        select(AgentRun)
                        .where(AgentRun.conversation_id == conv.id)
                        .order_by(AgentRun.created_at)
                    )
                )
                .scalars()
                .all()
            )
            feedback = await self._feedback_map(s, actor, [r.id for r in runs])
            messages: list[MessageOut] = []
            for r in runs:
                messages.append(
                    MessageOut(
                        role="user",
                        content=r.input,
                        run_id=r.id,
                        status=r.status,
                        created_at=r.created_at,
                    )
                )
                if r.status in TERMINAL and (r.output or r.status != "succeeded"):
                    messages.append(
                        MessageOut(
                            role="assistant",
                            content=r.output or "",
                            run_id=r.id,
                            status=r.status,
                            citations=r.citations,
                            feedback=feedback.get(r.id),
                            created_at=r.finished_at or r.created_at,
                        )
                    )
            base = self._conversation_out(conv, agent.name if agent else "")
            return ConversationDetail(**base.model_dump(), messages=messages)

    async def delete_conversation(
        self, actor: Actor, project_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> None:
        async with self._session(actor, project_id) as s:
            conv = await self._own_conversation(s, actor, conversation_id)
            await s.delete(conv)

    async def send_message(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        conversation_id: uuid.UUID,
        content: str,
    ) -> AgentRunOut:
        """Create the run and start its workflow (ADR-004). The client follows it over SSE."""
        async with self._session(actor, project_id) as s:
            conv = await self._own_conversation(s, actor, conversation_id)
            busy = (
                await s.execute(
                    select(AgentRun.id).where(
                        AgentRun.conversation_id == conv.id,
                        AgentRun.status.in_(("queued", "running")),
                    )
                )
            ).first()
            if busy:
                raise ProblemError(
                    409, "run_in_progress", "Wait for the current answer or stop it first"
                )
            agent = await self._agent(s, conv.agent_id)
            v = await self._version(s, agent, conv.agent_version)
            cfg = AgentConfig.model_validate(v.config)
            run = AgentRun(
                project_id=project_id,
                conversation_id=conv.id,
                agent_id=agent.id,
                agent_version=v.version,
                input=content,
                limits=cfg.limits.model_dump(),
                trace_id=current_trace_id() or uuid.uuid4().hex,
                created_by=actor.id,
            )
            s.add(run)
            await s.flush()
            run.workflow_id = f"agent-run-{run.id}"
            if not conv.title:
                conv.title = content.strip().splitlines()[0][:80]
            conv.updated_at = utcnow()
            run_out = await self._run_out(s, run, agent.name)
            run_id, workflow_id = run.id, run.workflow_id
        if not await self._starter.start(project_id, run_id, workflow_id, cfg.limits.timeout_s):
            async with self._session(actor, project_id) as s:
                await s.execute(
                    update(AgentRun)
                    .where(AgentRun.id == run_id)
                    .values(
                        status="failed",
                        error_code="queue_unavailable",
                        error="The run could not be queued; try again in a moment",
                        finished_at=utcnow(),
                    )
                )
            raise ProblemError(
                503, "queue_unavailable", "The run could not be queued; try again in a moment"
            )
        return run_out

    async def cancel(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        *,
        can_see_all: bool,
    ) -> AgentRunOut:
        async with self._session(actor, project_id) as s:
            run = await self._visible_run(s, actor, run_id, can_see_all=can_see_all)
            if run.status in TERMINAL:
                return await self._run_out(s, run)
            run.cancel_requested = True
            if run.status == "queued":
                # Not picked up yet: end it here; the activity then finds it already ended.
                run.status, run.error_code = "cancelled", "cancelled"
                run.error, run.finished_at = "Stopped by the user", utcnow()
            await audit.record(
                s, actor, "agent_run.cancel", "agent_run", run.id, {}, project_id=project_id
            )
            out = await self._run_out(s, run)
        return out

    # ------------------------------------------------------------ runs
    @staticmethod
    async def _visible_run(
        s: AsyncSession, actor: Actor, run_id: uuid.UUID, *, can_see_all: bool
    ) -> AgentRun:
        run = await s.get(AgentRun, run_id)
        if run is None or (not can_see_all and run.created_by != actor.id):
            raise RUN_NOT_FOUND
        return run

    @staticmethod
    async def _feedback_map(
        s: AsyncSession, actor: Actor, run_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, FeedbackOut]:
        if not run_ids or actor.id is None:
            return {}
        rows = await s.execute(
            select(RunFeedback).where(
                RunFeedback.run_id.in_(run_ids), RunFeedback.user_id == actor.id
            )
        )
        return {
            f.run_id: FeedbackOut(rating=f.rating, comment=f.comment, updated_at=f.updated_at)
            for f in rows.scalars()
        }

    async def _run_out(
        self, s: AsyncSession, run: AgentRun, agent_name: str | None = None
    ) -> AgentRunOut:
        if agent_name is None:
            agent = await s.get(Agent, run.agent_id)
            agent_name = agent.name if agent else None
        return AgentRunOut(
            id=run.id,
            conversation_id=run.conversation_id,
            agent_id=run.agent_id,
            agent_name=agent_name,
            agent_version=run.agent_version,
            input=run.input,
            output=run.output,
            status=run.status,
            error_code=run.error_code,
            error=run.error,
            citations=run.citations,
            tokens_in=run.tokens_in,
            tokens_out=run.tokens_out,
            total_tokens=run.tokens_in + run.tokens_out,
            cost=run.cost or Decimal(0),
            steps=run.steps,
            latency_ms=run.latency_ms,
            trace_id=run.trace_id,
            created_by=run.created_by,
            created_at=run.created_at or utcnow(),
            finished_at=run.finished_at,
        )

    async def get_run(
        self, actor: Actor, project_id: uuid.UUID, run_id: uuid.UUID, *, can_see_all: bool
    ) -> AgentRunDetail:
        async with self._session(actor, project_id) as s:
            run = await self._visible_run(s, actor, run_id, can_see_all=can_see_all)
            base = await self._run_out(s, run)
            steps = (
                (
                    await s.execute(
                        select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.ordinal)
                    )
                )
                .scalars()
                .all()
            )
            fb = (await self._feedback_map(s, actor, [run.id])).get(run.id)
            return AgentRunDetail(
                **base.model_dump(exclude={"feedback"}),
                feedback=fb,
                limits=run.limits,
                step_list=[AgentStepOut.model_validate(x) for x in steps],
            )

    async def list_runs(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        *,
        can_see_all: bool,
        agent_id: uuid.UUID | None = None,
        status: str | None = None,
        user_id: uuid.UUID | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> AgentRunPage:
        limit = max(1, min(limit, 200))
        stmt = (
            select(AgentRun, Agent.name)
            .join(Agent, Agent.id == AgentRun.agent_id)
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .limit(limit + 1)
        )
        if not can_see_all:
            stmt = stmt.where(AgentRun.created_by == actor.id)  # US-1.20.1 #3, in the query
        elif user_id:
            stmt = stmt.where(AgentRun.created_by == user_id)
        if agent_id:
            stmt = stmt.where(AgentRun.agent_id == agent_id)
        if status:
            stmt = stmt.where(AgentRun.status == status)
        if since:
            stmt = stmt.where(AgentRun.created_at >= since)
        if until:
            stmt = stmt.where(AgentRun.created_at < until)
        if cursor:
            at, rid = _uncursor(cursor)
            stmt = stmt.where(
                or_(AgentRun.created_at < at, and_(AgentRun.created_at == at, AgentRun.id < rid))
            )
        async with self._session(actor, project_id) as s:
            rows = (await s.execute(stmt)).all()
            page = rows[:limit]
            feedback = await self._feedback_map(s, actor, [r.id for r, _ in page])
            items = []
            for r, name in page:
                out = await self._run_out(s, r, name)
                out.feedback = feedback.get(r.id)
                items.append(out)
        nxt = _cursor(page[-1][0].created_at, page[-1][0].id) if len(rows) > limit else None
        return AgentRunPage(items=items, next_cursor=nxt)

    @staticmethod
    async def replay_events(run: AgentRunDetail) -> list[dict[str, Any]]:
        """The run's events rebuilt from the database, for a client that connects after the
        Redis stream has expired."""
        events: list[dict[str, Any]] = []
        for step in run.step_list:
            base = {"ordinal": step.ordinal, "kind": step.kind, "name": step.name}
            events.append({"type": "step_started", **base})
            events.append(
                {
                    "type": "step_finished",
                    **base,
                    "status": step.status,
                    "error_code": step.error_code,
                    "tokens_in": step.tokens_in,
                    "tokens_out": step.tokens_out,
                    "latency_ms": step.latency_ms,
                }
            )
        if run.citations:
            events.append(
                {
                    "type": "citations",
                    "citations": [c.model_dump(mode="json") for c in run.citations],
                }
            )
        events.append(
            {
                "type": "done" if run.status == "succeeded" else "error",
                "output": run.output,
                "status": run.status,
                "error_code": run.error_code,
                "error": run.error,
                "tokens_in": run.tokens_in,
                "tokens_out": run.tokens_out,
                "cost": str(run.cost),
                "steps": run.steps,
                "latency_ms": run.latency_ms,
            }
        )
        return events

    async def put_feedback(
        self, actor: Actor, project_id: uuid.UUID, run_id: uuid.UUID, body: FeedbackIn
    ) -> FeedbackOut:
        """Thumbs and comment on the user's own run (US-1.19.2)."""
        if actor.id is None:
            raise ProblemError(403, "forbidden", "Feedback needs a signed-in user")
        async with self._session(actor, project_id) as s:
            run = await self._visible_run(s, actor, run_id, can_see_all=False)
            if run.status not in TERMINAL:
                raise ProblemError(409, "run_in_progress", "The run has not finished")
            row = await s.get(RunFeedback, (run.id, actor.id))
            if row is None:
                row = RunFeedback(run_id=run.id, user_id=actor.id, project_id=project_id)
                s.add(row)
            row.rating, row.comment, row.updated_at = body.rating, body.comment, utcnow()
            await s.flush()
            return FeedbackOut(rating=row.rating, comment=row.comment, updated_at=row.updated_at)

    async def usage(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> AgentUsageOut:
        """Daily totals from runs. Run totals are sums of their steps, so these reconcile with
        `llm_call` by construction (AC9)."""
        day = cast(AgentRun.created_at, DATE)
        stmt = (
            select(
                day.label("day"),
                func.count().label("runs"),
                func.count().filter(AgentRun.status == "succeeded").label("ok"),
                func.count().filter(AgentRun.status == "failed").label("failed"),
                func.count().filter(AgentRun.status == "cancelled").label("cancelled"),
                func.coalesce(func.sum(AgentRun.tokens_in), 0),
                func.coalesce(func.sum(AgentRun.tokens_out), 0),
                func.coalesce(func.sum(AgentRun.cost), 0),
            )
            .group_by(day)
            .order_by(day.desc())
            .limit(366)
        )
        if since:
            stmt = stmt.where(AgentRun.created_at >= since)
        if until:
            stmt = stmt.where(AgentRun.created_at < until)
        async with self._session(actor, project_id) as s:
            rows = (await s.execute(stmt)).all()
        days = [
            AgentUsageDay(
                day=r[0],
                runs=r[1],
                succeeded=r[2],
                failed=r[3],
                cancelled=r[4],
                tokens_in=int(r[5]),
                tokens_out=int(r[6]),
                cost=Decimal(r[7]),
            )
            for r in rows
        ]
        return AgentUsageOut(
            days=days,
            total_runs=sum(d.runs for d in days),
            total_tokens=sum(d.tokens_in + d.tokens_out for d in days),
            total_cost=sum((d.cost for d in days), Decimal(0)),
        )


__all__ = ["AgentStudio", "tool_name_for"]
