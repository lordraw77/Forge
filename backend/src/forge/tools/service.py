"""Tools Studio (M2.2): library, versions, credentials, egress policy, data sources, MCP servers,
OpenAPI import, test console and call log. Public interface of the tools module (ADR-001)."""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import audit, secrets
from forge.core.context import Actor
from forge.core.crypto import Envelope
from forge.core.db import project_scope
from forge.core.egress import EgressError
from forge.core.errors import ProblemError
from forge.core.settings import Settings
from forge.tools import openapi, runtime, sqlguard
from forge.tools.models import (
    Credential,
    DataSource,
    McpServer,
    Tool,
    ToolCall,
    ToolPolicy,
    ToolVersion,
)
from forge.tools.runtime import ExecResult
from forge.tools.schemas import (
    CredentialIn,
    DataSourceIn,
    HttpConfig,
    McpConfig,
    McpServerIn,
    McpToolOut,
    OpenApiImportIn,
    OpenApiOperationOut,
    OpenApiPreviewOut,
    PolicyIn,
    SqlConfig,
    ToolCreateIn,
    ToolDetail,
    ToolOut,
    ToolPage,
    ToolReferenceOut,
    ToolVersionIn,
    ToolVersionOut,
)

NOT_FOUND = ProblemError(404, "not_found", "Tool not found")
STEP_OUTPUT_BYTES = 64 * 1024


def _config(kind: str, data: dict[str, Any]) -> HttpConfig | SqlConfig | McpConfig:
    if kind == "http":
        return HttpConfig.model_validate(data)
    if kind == "sql":
        return SqlConfig.model_validate(data)
    return McpConfig.model_validate(data)


ReferenceCheck = Callable[[AsyncSession, uuid.UUID], Awaitable[list[ToolReferenceOut]]]


def _bounded(value: Any) -> Any:
    raw = json.dumps(value, default=str)
    if len(raw) <= STEP_OUTPUT_BYTES:
        return json.loads(raw)
    return {"truncated": True, "preview": raw[:STEP_OUTPUT_BYTES]}


@dataclass
class PreparedTool:
    """A library tool pinned to a version with everything it needs resolved for one run."""

    tool_id: uuid.UUID
    name: str
    version: int
    kind: str
    description: str
    schema_in: dict[str, Any]
    timeout_s: int
    requires_approval: bool
    writes: bool
    config: HttpConfig | SqlConfig | McpConfig
    allowed_hosts: list[str]
    allow_private: bool
    credential: runtime.ResolvedCredential | None = None
    datasource: runtime.ResolvedDataSource | None = None
    server: runtime.ResolvedMcp | None = None
    _studio: ToolStudio | None = field(default=None, repr=False)

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description[:1000],
                "parameters": self.schema_in,
            },
        }

    async def execute(self, args: dict[str, Any]) -> ExecResult:
        invalid = runtime.check_arguments(self.schema_in, args)
        if invalid:
            return runtime.error("invalid_arguments", invalid)
        started = time.monotonic()
        try:
            if isinstance(self.config, HttpConfig):
                res = await runtime.run_http(
                    self.config,
                    args,
                    credential=self.credential,
                    timeout_s=self.timeout_s,
                    allowed_hosts=self.allowed_hosts,
                    allow_private=self.allow_private,
                )
            elif isinstance(self.config, SqlConfig):
                assert self.datasource
                res = await runtime.run_sql(
                    self.config,
                    args,
                    source=self.datasource,
                    timeout_s=self.timeout_s,
                    allowed_hosts=self.allowed_hosts,
                    allow_private=self.allow_private,
                )
            else:
                assert self.server
                res = await runtime.run_mcp(
                    self.config,
                    args,
                    server=self.server,
                    timeout_s=self.timeout_s,
                    allowed_hosts=self.allowed_hosts,
                    allow_private=self.allow_private,
                )
        except EgressError as exc:
            res = runtime.error(exc.code, exc.message)
        if not res.latency_ms:
            res.latency_ms = int((time.monotonic() - started) * 1000)
        return self._scrubbed(res)

    def _scrubbed(self, res: ExecResult) -> ExecResult:
        known: list[str] = []
        if self.credential:
            known += [self.credential.secret]
        if self.datasource:
            known += [self.datasource.password]
        if self.server:
            known += list(self.server.headers.values())
        res.content = runtime.scrub(res.content, known)
        res.output = runtime.scrub(res.output, known)
        res.request = runtime.scrub(res.request, known)
        return res


class ToolStudio:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        envelope: Envelope,
        settings: Settings,
    ) -> None:
        self._sessions = sessions
        self._envelope = envelope
        self._settings = settings
        self._checks: list[ReferenceCheck] = []

    @staticmethod
    async def name_in(s: AsyncSession, item_id: uuid.UUID) -> str | None:
        """Name of one item inside an open session (used by other modules' reference checks)."""
        return (await s.execute(select(Tool.name).where(Tool.id == item_id))).scalar_one_or_none()

    def add_reference_check(self, check: ReferenceCheck) -> None:
        """Modules that use tools (agents) register who references one."""
        self._checks.append(check)

    @asynccontextmanager
    async def _session(
        self, actor_id: uuid.UUID | None, project_id: uuid.UUID
    ) -> AsyncIterator[AsyncSession]:
        async with project_scope(self._sessions, project_id, actor_id) as s:
            yield s

    @property
    def allow_private(self) -> bool:
        return self._settings.tools_allow_private_hosts

    @property
    def stdio_allowed(self) -> list[str]:
        return [c.strip() for c in self._settings.mcp_stdio_allowed.split(",") if c.strip()]

    # ------------------------------------------------------------------ policy
    async def get_policy(self, actor: Actor, project_id: uuid.UUID) -> tuple[list[str], bool]:
        async with self._session(actor.id, project_id) as s:
            return await self._hosts(s, project_id), self._settings.tools_allow_private_hosts

    @staticmethod
    async def _hosts(s: AsyncSession, project_id: uuid.UUID) -> list[str]:
        row = await s.get(ToolPolicy, project_id)
        return list(row.allowed_hosts) if row else []

    async def set_policy(self, actor: Actor, project_id: uuid.UUID, body: PolicyIn) -> list[str]:
        async with self._session(actor.id, project_id) as s:
            row = await s.get(ToolPolicy, project_id)
            before = list(row.allowed_hosts) if row else []
            if row is None:
                s.add(
                    ToolPolicy(
                        project_id=project_id, allowed_hosts=body.allowed_hosts, updated_by=actor.id
                    )
                )
            else:
                row.allowed_hosts, row.updated_by = body.allowed_hosts, actor.id
            await audit.record(
                s,
                actor,
                "tools.policy_update",
                "project",
                project_id,
                {"before": before, "after": body.allowed_hosts},
                project_id=project_id,
            )
        return body.allowed_hosts

    # ------------------------------------------------------------------ credentials
    async def list_credentials(
        self, actor: Actor, project_id: uuid.UUID
    ) -> list[tuple[Credential, int]]:
        async with self._session(actor.id, project_id) as s:
            rows = list(
                (await s.execute(select(Credential).order_by(func.lower(Credential.name))))
                .scalars()
                .all()
            )
            return [(c, await self._usage(s, "credential", c.name)) for c in rows]

    async def create_credential(
        self, actor: Actor, project_id: uuid.UUID, body: CredentialIn
    ) -> Credential:
        async with self._session(actor.id, project_id) as s:
            secret_id = await secrets.put(
                s, self._envelope, f"tool.{body.kind}", body.secret, project_id
            )
            row = Credential(
                project_id=project_id,
                name=body.name,
                kind=body.kind,
                username=body.username,
                param_name=body.param_name,
                secret_id=secret_id,
                created_by=actor.id,
            )
            s.add(row)
            await self._flush(s, "A credential with this name exists")
            await audit.record(
                s,
                actor,
                "credential.create",
                "credential",
                row.id,
                {"name": row.name, "kind": row.kind},
                project_id=project_id,
            )
            return row

    async def delete_credential(
        self, actor: Actor, project_id: uuid.UUID, credential_id: uuid.UUID
    ) -> None:
        async with self._session(actor.id, project_id) as s:
            row = await s.get(Credential, credential_id)
            if row is None:
                raise ProblemError(404, "not_found", "Credential not found")
            used = await self._usage(s, "credential", row.name)
            servers = (
                await s.execute(select(func.count()).where(McpServer.credential_id == row.id))
            ).scalar_one()
            if used or servers:
                raise ProblemError(
                    409, "credential_in_use", "Tools or MCP servers use this credential"
                )
            await audit.record(
                s,
                actor,
                "credential.delete",
                "credential",
                row.id,
                {"name": row.name},
                project_id=project_id,
            )
            await s.delete(row)
            await s.flush()
            await secrets.delete(s, row.secret_id)

    async def _credential(self, s: AsyncSession, project_id: uuid.UUID, name: str) -> Credential:
        row = (
            await s.execute(select(Credential).where(func.lower(Credential.name) == name.lower()))
        ).scalar_one_or_none()
        if row is None:
            raise ProblemError(422, "unknown_credential", f"No credential named '{name}'")
        return row

    async def _resolve_credential(
        self, s: AsyncSession, row: Credential
    ) -> runtime.ResolvedCredential:
        value = await secrets.get(s, self._envelope, row.secret_id)
        return runtime.ResolvedCredential(row.kind, value, row.username, row.param_name)

    # ------------------------------------------------------------------ data sources
    async def list_datasources(
        self, actor: Actor, project_id: uuid.UUID
    ) -> list[tuple[DataSource, int]]:
        async with self._session(actor.id, project_id) as s:
            rows = list(
                (await s.execute(select(DataSource).order_by(func.lower(DataSource.name))))
                .scalars()
                .all()
            )
            return [(d, await self._usage(s, "datasource", d.name)) for d in rows]

    async def save_datasource(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        body: DataSourceIn,
        source_id: uuid.UUID | None = None,
    ) -> DataSource:
        async with self._session(actor.id, project_id) as s:
            if source_id is None:
                if not body.password:
                    raise ProblemError(422, "password_required", "A password is required")
                secret_id = await secrets.put(
                    s, self._envelope, "tool.datasource", body.password, project_id
                )
                row = DataSource(
                    project_id=project_id,
                    name=body.name,
                    host=body.host,
                    port=body.port,
                    database=body.database,
                    username=body.username,
                    ssl_mode=body.ssl_mode,
                    secret_id=secret_id,
                    created_by=actor.id,
                )
                s.add(row)
            else:
                found = await s.get(DataSource, source_id)
                if found is None:
                    raise ProblemError(404, "not_found", "Data source not found")
                row = found
                row.name, row.host, row.port = body.name, body.host, body.port
                row.database, row.username, row.ssl_mode = (
                    body.database,
                    body.username,
                    body.ssl_mode,
                )
                if body.password:
                    await secrets.delete(s, row.secret_id)
                    row.secret_id = await secrets.put(
                        s, self._envelope, "tool.datasource", body.password, project_id
                    )
            await self._flush(s, "A data source with this name exists")
            await audit.record(
                s,
                actor,
                "datasource.save",
                "datasource",
                row.id,
                {"name": row.name, "host": row.host, "database": row.database},
                project_id=project_id,
            )
            return row

    async def delete_datasource(
        self, actor: Actor, project_id: uuid.UUID, source_id: uuid.UUID
    ) -> None:
        async with self._session(actor.id, project_id) as s:
            row = await s.get(DataSource, source_id)
            if row is None:
                raise ProblemError(404, "not_found", "Data source not found")
            if await self._usage(s, "datasource", row.name):
                raise ProblemError(409, "datasource_in_use", "Tools use this data source")
            await audit.record(
                s,
                actor,
                "datasource.delete",
                "datasource",
                row.id,
                {"name": row.name},
                project_id=project_id,
            )
            await s.delete(row)
            await s.flush()
            await secrets.delete(s, row.secret_id)

    # ------------------------------------------------------------------ MCP servers
    async def list_servers(
        self, actor: Actor, project_id: uuid.UUID
    ) -> list[tuple[McpServer, str | None, int]]:
        async with self._session(actor.id, project_id) as s:
            rows = (
                await s.execute(
                    select(McpServer, Credential.name)
                    .outerjoin(Credential, Credential.id == McpServer.credential_id)
                    .order_by(func.lower(McpServer.name))
                )
            ).all()
            return [(m, c, await self._usage(s, "server", m.name)) for m, c in rows]

    async def save_server(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        body: McpServerIn,
        server_id: uuid.UUID | None = None,
    ) -> McpServer:
        async with self._session(actor.id, project_id) as s:
            if body.transport == "stdio" and body.command not in self.stdio_allowed:
                raise ProblemError(
                    422,
                    "stdio_not_allowed",
                    "This command is not allowed",
                    "The deployment must list it in FORGE_MCP_STDIO_ALLOWED",
                )
            cred_id = None
            if body.credential:
                cred_id = (await self._credential(s, project_id, body.credential)).id
            row = (
                await s.get(McpServer, server_id) if server_id else McpServer(project_id=project_id)
            )
            if row is None:
                raise ProblemError(404, "not_found", "MCP server not found")
            row.name, row.transport, row.url = body.name, body.transport, body.url
            row.command, row.args, row.enabled = body.command, body.args, body.enabled
            row.credential_id = cred_id
            if server_id is None:
                row.created_by = actor.id
                s.add(row)
            await self._flush(s, "An MCP server with this name exists")
            await audit.record(
                s,
                actor,
                "mcp_server.save",
                "mcp_server",
                row.id,
                {"name": row.name, "transport": row.transport, "url": row.url},
                project_id=project_id,
            )
            return row

    async def delete_server(
        self, actor: Actor, project_id: uuid.UUID, server_id: uuid.UUID
    ) -> None:
        async with self._session(actor.id, project_id) as s:
            row = await s.get(McpServer, server_id)
            if row is None:
                raise ProblemError(404, "not_found", "MCP server not found")
            if await self._usage(s, "server", row.name):
                raise ProblemError(409, "server_in_use", "Tools use this MCP server")
            await audit.record(
                s,
                actor,
                "mcp_server.delete",
                "mcp_server",
                row.id,
                {"name": row.name},
                project_id=project_id,
            )
            await s.delete(row)

    async def _resolve_server(self, s: AsyncSession, row: McpServer) -> runtime.ResolvedMcp:
        headers: dict[str, str] = {}
        if row.credential_id:
            cred = await s.get(Credential, row.credential_id)
            if cred is not None:
                resolved = await self._resolve_credential(s, cred)
                if resolved.kind == "bearer":
                    headers["Authorization"] = f"Bearer {resolved.secret}"
                elif resolved.kind == "header" and resolved.param_name:
                    headers[resolved.param_name] = resolved.secret
        return runtime.ResolvedMcp(
            row.transport, row.url, row.command, list(row.args), headers, self.stdio_allowed
        )

    async def discover(
        self, actor: Actor, project_id: uuid.UUID, server_id: uuid.UUID
    ) -> list[McpToolOut]:
        async with self._session(actor.id, project_id) as s:
            row = await s.get(McpServer, server_id)
            if row is None:
                raise ProblemError(404, "not_found", "MCP server not found")
            resolved = await self._resolve_server(s, row)
            hosts = await self._hosts(s, project_id)
            existing = await self._mcp_tools(s, row.name)
            server_name = row.name
        try:
            found = await runtime.mcp_list_tools(
                resolved, hosts, self._settings.tools_allow_private_hosts
            )
        except EgressError as exc:
            raise ProblemError(422, exc.code, exc.message) from exc
        except Exception as exc:
            raise ProblemError(
                502, "mcp_unreachable", "The MCP server could not be queried", type(exc).__name__
            ) from exc
        out = []
        for t in found:
            have = existing.get(t["name"])
            out.append(
                McpToolOut(
                    name=t["name"],
                    description=t["description"],
                    schema_in=t["schema_in"],
                    imported_as=have[0] if have else None,
                    changed=bool(have and have[1] != t["schema_in"]),
                )
            )
        _ = server_name
        return out

    @staticmethod
    async def _mcp_tools(
        s: AsyncSession, server_name: str
    ) -> dict[str, tuple[str, dict[str, Any]]]:
        rows = (
            await s.execute(
                select(Tool.name, ToolVersion.config)
                .join(
                    ToolVersion,
                    and_(
                        ToolVersion.tool_id == Tool.id, ToolVersion.version == Tool.latest_version
                    ),
                )
                .where(Tool.kind == "mcp", ToolVersion.config["server"].astext == server_name)
            )
        ).all()
        return {c["tool"]: (n, c.get("schema_in", {})) for n, c in rows}

    async def import_mcp_tools(
        self, actor: Actor, project_id: uuid.UUID, server_id: uuid.UUID, names: list[str]
    ) -> list[ToolOut]:
        listed = {t.name: t for t in await self.discover(actor, project_id, server_id)}
        missing = [n for n in names if n not in listed]
        if missing:
            raise ProblemError(422, "unknown_tool", "The server has no such tools", missing)
        async with self._session(actor.id, project_id) as s:
            row = await s.get(McpServer, server_id)
            assert row is not None
            created: list[ToolOut] = []
            for n in names:
                t = listed[n]
                cfg = McpConfig(server=row.name, tool=n, schema_in=t.schema_in)
                tool_name = await self._free_name(s, _tool_name(n))
                body = ToolVersionIn(
                    description=(t.description or n)[:1000], config=cfg, note="Imported from MCP"
                )
                tool = await self._create(s, actor, project_id, tool_name, body)
                created.append(self._out(tool, await self._latest(s, tool)))
            return created

    # ------------------------------------------------------------------ tools
    @staticmethod
    async def _flush(s: AsyncSession, message: str) -> None:
        try:
            await s.flush()
        except IntegrityError as exc:
            raise ProblemError(409, "name_taken", message) from exc

    @staticmethod
    async def _tool(s: AsyncSession, tool_id: uuid.UUID, *, lock: bool = False) -> Tool:
        stmt = select(Tool).where(Tool.id == tool_id)
        if lock:
            stmt = stmt.with_for_update()
        tool = (await s.execute(stmt)).scalar_one_or_none()
        if tool is None:
            raise NOT_FOUND
        return tool

    @staticmethod
    async def _version(s: AsyncSession, tool: Tool, number: int | None) -> ToolVersion:
        wanted = number or tool.latest_version
        row = (
            await s.execute(
                select(ToolVersion).where(
                    ToolVersion.tool_id == tool.id, ToolVersion.version == wanted
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise ProblemError(404, "version_not_found", f"Version {wanted} does not exist")
        return row

    async def _latest(self, s: AsyncSession, tool: Tool) -> ToolVersion:
        return await self._version(s, tool, None)

    @staticmethod
    def _out(tool: Tool, v: ToolVersion) -> ToolOut:
        return ToolOut(
            id=tool.id,
            name=tool.name,
            kind=tool.kind,
            description=v.description,
            latest_version=tool.latest_version,
            published_version=tool.published_version,
            writes=v.writes,
            requires_approval=v.requires_approval,
            updated_at=tool.updated_at,
            created_at=tool.created_at,
        )

    async def _free_name(self, s: AsyncSession, base: str) -> str:
        name, n = base, 1
        while (await s.execute(select(Tool.id).where(Tool.name == name))).first():
            n += 1
            name = f"{base[: 48 - len(str(n)) - 1]}_{n}"
        return name

    async def _validate(self, s: AsyncSession, project_id: uuid.UUID, body: ToolVersionIn) -> bool:
        """Check the references and the query; returns whether the tool can change data."""
        cfg = body.config
        if isinstance(cfg, HttpConfig):
            if cfg.credential:
                await self._credential(s, project_id, cfg.credential)
            return cfg.method != "GET"
        if isinstance(cfg, SqlConfig):
            ds = (
                await s.execute(
                    select(DataSource.id).where(
                        func.lower(DataSource.name) == cfg.datasource.lower()
                    )
                )
            ).first()
            if ds is None:
                raise ProblemError(422, "unknown_datasource", f"No data source '{cfg.datasource}'")
            try:
                parsed = sqlguard.parse(cfg.query)
            except sqlguard.SqlRejected as exc:
                raise ProblemError(
                    422, "invalid_query", "The query is not allowed", str(exc)
                ) from exc
            declared = {p.name for p in cfg.params}
            undeclared = [n for n in parsed.names if n not in declared]
            if undeclared:
                raise ProblemError(
                    422, "invalid_query", "Parameters used but not declared", undeclared
                )
            return False
        server = (
            await s.execute(
                select(McpServer.id).where(func.lower(McpServer.name) == cfg.server.lower())
            )
        ).first()
        if server is None:
            raise ProblemError(422, "unknown_server", f"No MCP server '{cfg.server}'")
        return True  # an MCP tool may change data: shown as such

    async def _add_version(
        self, s: AsyncSession, actor: Actor, tool: Tool, body: ToolVersionIn
    ) -> ToolVersion:
        if body.config.kind != tool.kind:
            raise ProblemError(422, "kind_mismatch", "A tool cannot change its type")
        writes = await self._validate(s, tool.project_id, body)
        tool.latest_version += 1
        tool.description = body.description
        tool.updated_by = actor.id
        v = ToolVersion(
            project_id=tool.project_id,
            tool_id=tool.id,
            version=tool.latest_version,
            description=body.description,
            config=body.config.model_dump(mode="json", by_alias=True),
            schema_in=runtime.schema_for(body.config),
            timeout_s=body.timeout_s,
            requires_approval=body.requires_approval,
            writes=writes,
            note=body.note,
            created_by=actor.id,
        )
        s.add(v)
        await s.flush()
        return v

    async def _create(
        self, s: AsyncSession, actor: Actor, project_id: uuid.UUID, name: str, body: ToolVersionIn
    ) -> Tool:
        tool = Tool(
            project_id=project_id,
            name=name,
            kind=body.config.kind,
            description=body.description,
            created_by=actor.id,
            updated_by=actor.id,
        )
        s.add(tool)
        await self._flush(s, f"A tool named '{name}' already exists")
        v = await self._add_version(s, actor, tool, body)
        await audit.record(
            s,
            actor,
            "tool.create",
            "tool",
            tool.id,
            {"name": name, "kind": tool.kind, "version": v.version},
            project_id=project_id,
        )
        return tool

    async def create(self, actor: Actor, project_id: uuid.UUID, body: ToolCreateIn) -> ToolDetail:
        async with self._session(actor.id, project_id) as s:
            tool = await self._create(s, actor, project_id, body.name, body)
            return await self._detail(s, tool, None)

    async def save_version(
        self, actor: Actor, project_id: uuid.UUID, tool_id: uuid.UUID, body: ToolVersionIn
    ) -> ToolDetail:
        async with self._session(actor.id, project_id) as s:
            tool = await self._tool(s, tool_id, lock=True)
            v = await self._add_version(s, actor, tool, body)
            await audit.record(
                s,
                actor,
                "tool.version",
                "tool",
                tool.id,
                {"version": v.version},
                project_id=project_id,
            )
            return await self._detail(s, tool, None)

    async def _detail(self, s: AsyncSession, tool: Tool, number: int | None) -> ToolDetail:
        v = await self._version(s, tool, number)
        versions = list(
            (
                await s.execute(
                    select(ToolVersion.version)
                    .where(ToolVersion.tool_id == tool.id)
                    .order_by(ToolVersion.version.desc())
                )
            ).scalars()
        )
        latest = await self._latest(s, tool)
        base = self._out(tool, latest)
        return ToolDetail(
            **base.model_dump(),
            version_detail=ToolVersionOut.model_validate(v),
            versions=versions,
        )

    async def get(
        self, actor: Actor, project_id: uuid.UUID, tool_id: uuid.UUID, version: int | None = None
    ) -> ToolDetail:
        async with self._session(actor.id, project_id) as s:
            return await self._detail(s, await self._tool(s, tool_id), version)

    async def list_tools(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        *,
        q: str | None = None,
        kind: str | None = None,
        published: bool | None = None,
        published_only: bool = False,
    ) -> ToolPage:
        async with self._session(actor.id, project_id) as s:
            stmt = (
                select(Tool, ToolVersion)
                .join(
                    ToolVersion,
                    and_(
                        ToolVersion.tool_id == Tool.id, ToolVersion.version == Tool.latest_version
                    ),
                )
                .order_by(Tool.updated_at.desc())
                .limit(200)
            )
            if q:
                like = f"%{q.lower()}%"
                stmt = stmt.where(
                    or_(func.lower(Tool.name).like(like), func.lower(Tool.description).like(like))
                )
            if kind:
                stmt = stmt.where(Tool.kind == kind)
            if published is True or published_only:
                stmt = stmt.where(Tool.published_version.is_not(None))
            elif published is False:
                stmt = stmt.where(Tool.published_version.is_(None))
            rows = (await s.execute(stmt)).all()
            return ToolPage(items=[self._out(t, v) for t, v in rows], next_cursor=None)

    async def publish(
        self, actor: Actor, project_id: uuid.UUID, tool_id: uuid.UUID, version: int | None
    ) -> ToolDetail:
        async with self._session(actor.id, project_id) as s:
            tool = await self._tool(s, tool_id, lock=True)
            v = await self._version(s, tool, version)
            tool.published_version = v.version
            tool.updated_by = actor.id
            await audit.record(
                s,
                actor,
                "tool.publish",
                "tool",
                tool.id,
                {"version": v.version},
                project_id=project_id,
            )
            await s.flush()
            return await self._detail(s, tool, None)

    async def unpublish(
        self, actor: Actor, project_id: uuid.UUID, tool_id: uuid.UUID
    ) -> ToolDetail:
        async with self._session(actor.id, project_id) as s:
            tool = await self._tool(s, tool_id, lock=True)
            tool.published_version = None
            await audit.record(
                s, actor, "tool.unpublish", "tool", tool.id, {}, project_id=project_id
            )
            await s.flush()
            return await self._detail(s, tool, None)

    async def duplicate(
        self, actor: Actor, project_id: uuid.UUID, tool_id: uuid.UUID, name: str
    ) -> ToolDetail:
        async with self._session(actor.id, project_id) as s:
            src = await self._tool(s, tool_id)
            v = await self._latest(s, src)
            body = ToolVersionIn(
                description=v.description,
                config=_config(src.kind, v.config),
                timeout_s=v.timeout_s,
                requires_approval=v.requires_approval,
                note=f"Copy of {src.name} v{v.version}",
            )
            tool = await self._create(s, actor, project_id, name, body)
            return await self._detail(s, tool, None)

    async def references(self, s: AsyncSession, tool_id: uuid.UUID) -> list[ToolReferenceOut]:
        refs: list[ToolReferenceOut] = []
        for check in self._checks:
            refs.extend(await check(s, tool_id))
        return refs

    async def delete(self, actor: Actor, project_id: uuid.UUID, tool_id: uuid.UUID) -> None:
        async with self._session(actor.id, project_id) as s:
            tool = await self._tool(s, tool_id, lock=True)
            refs = await self.references(s, tool.id)
            if refs:
                raise ProblemError(
                    409,
                    "tool_in_use",
                    "Agents use this tool",
                    [r.model_dump(mode="json") for r in refs],
                )
            await audit.record(
                s, actor, "tool.delete", "tool", tool.id, {"name": tool.name}, project_id=project_id
            )
            await s.execute(delete(ToolVersion).where(ToolVersion.tool_id == tool.id))
            await s.delete(tool)

    @staticmethod
    async def _usage(s: AsyncSession, key: str, name: str) -> int:
        """Tools whose latest version refers to a credential, data source or MCP server."""
        stmt = (
            select(func.count(func.distinct(Tool.id)))
            .join(
                ToolVersion,
                and_(ToolVersion.tool_id == Tool.id, ToolVersion.version == Tool.latest_version),
            )
            .where(ToolVersion.config[key].astext == name)
        )
        return int((await s.execute(stmt)).scalar_one())

    # ------------------------------------------------------------------ OpenAPI
    @staticmethod
    def preview_openapi(document: str) -> OpenApiPreviewOut:
        try:
            spec = openapi.parse(document)
        except openapi.OpenApiError as exc:
            raise ProblemError(
                422, "invalid_openapi", "Cannot read the document", str(exc)
            ) from exc
        base = spec.base_urls[0] if spec.base_urls else ""
        return OpenApiPreviewOut(
            title=spec.title,
            base_urls=spec.base_urls,
            security=spec.security,
            operations=[
                OpenApiOperationOut(
                    operation_id=o.operation_id,
                    method=o.method,
                    path=o.path,
                    summary=o.summary,
                    name=o.name,
                    base_url=base,
                )
                for o in spec.operations
            ],
        )

    async def import_openapi(
        self, actor: Actor, project_id: uuid.UUID, body: OpenApiImportIn
    ) -> tuple[list[ToolOut], list[str]]:
        try:
            spec = openapi.parse(body.document)
        except openapi.OpenApiError as exc:
            raise ProblemError(
                422, "invalid_openapi", "Cannot read the document", str(exc)
            ) from exc
        base = body.base_url or (spec.base_urls[0] if spec.base_urls else "")
        if not re.match(r"^https?://", base):
            raise ProblemError(
                422, "base_url_required", "The document has no absolute server URL: give one"
            )
        by_id = {o.operation_id: o for o in spec.operations}
        unknown = [o for o in body.operations if o not in by_id]
        if unknown:
            raise ProblemError(422, "unknown_operation", "No such operations", unknown)
        created: list[ToolOut] = []
        skipped: list[str] = []
        async with self._session(actor.id, project_id) as s:
            for op_id in body.operations:
                op = by_id[op_id]
                try:
                    cfg = openapi.to_http_config(op, base, body.credential)
                    version = ToolVersionIn(
                        description=(op.summary or op.description or op.name)[:1000],
                        config=cfg,
                        note=f"Imported from OpenAPI: {op.method} {op.path}",
                    )
                    name = await self._free_name(s, op.name)
                    tool = await self._create(s, actor, project_id, name, version)
                except (ValueError, ProblemError) as exc:
                    skipped.append(f"{op_id}: {getattr(exc, 'title', None) or exc}")
                    continue
                created.append(self._out(tool, await self._latest(s, tool)))
        return created, skipped

    # ------------------------------------------------------------------ execution
    async def prepare(
        self,
        actor_id: uuid.UUID | None,
        project_id: uuid.UUID,
        tool_id: uuid.UUID,
        version: int | None,
        *,
        name: str | None = None,
    ) -> PreparedTool:
        async with self._session(actor_id, project_id) as s:
            tool = await self._tool(s, tool_id)
            v = await self._version(s, tool, version)
            cfg = _config(tool.kind, v.config)
            hosts = await self._hosts(s, project_id)
            prepared = PreparedTool(
                tool_id=tool.id,
                name=name or tool.name,
                version=v.version,
                kind=tool.kind,
                description=v.description,
                schema_in=v.schema_in,
                timeout_s=v.timeout_s,
                requires_approval=v.requires_approval,
                writes=v.writes,
                config=cfg,
                allowed_hosts=hosts,
                allow_private=self._settings.tools_allow_private_hosts,
                _studio=self,
            )
            if isinstance(cfg, HttpConfig) and cfg.credential:
                row = await self._credential(s, project_id, cfg.credential)
                prepared.credential = await self._resolve_credential(s, row)
            elif isinstance(cfg, SqlConfig):
                ds = (
                    await s.execute(
                        select(DataSource).where(
                            func.lower(DataSource.name) == cfg.datasource.lower()
                        )
                    )
                ).scalar_one_or_none()
                if ds is None:
                    raise ProblemError(422, "unknown_datasource", "The data source was removed")
                prepared.datasource = runtime.ResolvedDataSource(
                    ds.host,
                    ds.port,
                    ds.database,
                    ds.username,
                    await secrets.get(s, self._envelope, ds.secret_id),
                    ds.ssl_mode,
                )
            elif isinstance(cfg, McpConfig):
                srv = (
                    await s.execute(
                        select(McpServer).where(func.lower(McpServer.name) == cfg.server.lower())
                    )
                ).scalar_one_or_none()
                if srv is None or not srv.enabled:
                    raise ProblemError(422, "unknown_server", "The MCP server is gone or disabled")
                prepared.server = await self._resolve_server(s, srv)
            return prepared

    async def prepare_named(
        self,
        actor_id: uuid.UUID | None,
        project_id: uuid.UUID,
        name: str,
        version: int,
    ) -> PreparedTool:
        """A library tool by name and version, for use case steps."""
        async with self._session(actor_id, project_id) as s:
            tool_id = (
                await s.execute(select(Tool.id).where(Tool.name == name.lower()))
            ).scalar_one_or_none()
        if tool_id is None:
            raise ProblemError(422, "unknown_tool", f"Tool '{name}' does not exist")
        return await self.prepare(actor_id, project_id, tool_id, version)

    async def log_call(
        self,
        project_id: uuid.UUID,
        user_id: uuid.UUID | None,
        tool: PreparedTool,
        args: dict[str, Any],
        result: ExecResult,
        *,
        source: str,
        run_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        async with self._session(user_id, project_id) as s:
            row = ToolCall(
                project_id=project_id,
                tool_id=tool.tool_id,
                tool_name=tool.name,
                version=tool.version,
                source=source,
                run_id=run_id,
                user_id=user_id,
                status=result.status,
                error_code=result.error_code,
                input=_bounded({"arguments": args, "request": result.request}),
                output=_bounded(result.output)
                if isinstance(result.output, dict)
                else _bounded({"value": result.output}),
                latency_ms=result.latency_ms,
            )
            s.add(row)
            await s.flush()
            return row.id

    async def test(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        tool_id: uuid.UUID,
        version: int | None,
        args: dict[str, Any],
    ) -> tuple[ExecResult, uuid.UUID]:
        prepared = await self.prepare(actor.id, project_id, tool_id, version)
        result = await prepared.execute(args)
        call_id = await self.log_call(project_id, actor.id, prepared, args, result, source="test")
        return result, call_id

    async def list_calls(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        *,
        tool_id: uuid.UUID | None = None,
        status: str | None = None,
        source: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> list[ToolCall]:
        async with self._session(actor.id, project_id) as s:
            stmt = (
                select(ToolCall).order_by(ToolCall.created_at.desc()).limit(max(1, min(limit, 500)))
            )
            if tool_id:
                stmt = stmt.where(ToolCall.tool_id == tool_id)
            if status:
                stmt = stmt.where(ToolCall.status == status)
            if source:
                stmt = stmt.where(ToolCall.source == source)
            if since:
                stmt = stmt.where(ToolCall.created_at >= since)
            if until:
                stmt = stmt.where(ToolCall.created_at < until)
            return list((await s.execute(stmt)).scalars())

    async def resolve_library(
        self, actor: Actor, project_id: uuid.UUID, tool_id: uuid.UUID, version: int | None
    ) -> ToolVersion:
        """For agents: the tool version to pin (the published one by default)."""
        async with self._session(actor.id, project_id) as s:
            tool = await self._tool(s, tool_id)
            if version is None:
                if tool.published_version is None:
                    raise ProblemError(
                        422,
                        "tool_not_published",
                        f"Tool '{tool.name}' is not published: publish it or choose a version",
                    )
                version = tool.published_version
            v = await self._version(s, tool, version)
            s.expunge(v)
            return v

    async def tool_name(self, actor: Actor, project_id: uuid.UUID, tool_id: uuid.UUID) -> str:
        async with self._session(actor.id, project_id) as s:
            return (await self._tool(s, tool_id)).name


def _tool_name(raw: str) -> str:
    name = re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_").lower() or "tool"
    if not name[0].isalpha():
        name = "t_" + name
    return name[:48].rstrip("_")


tool_error = runtime.error

__all__ = ["PreparedTool", "ToolReferenceOut", "ToolStudio", "tool_error"]
