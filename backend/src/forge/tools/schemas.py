"""Tool definitions, credentials, data sources, MCP servers and call log (M2.2)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
PARAM_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,47}$")
HOST = re.compile(r"^(\*\.)?[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")
LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$")
JSON_TYPES = ("string", "integer", "number", "boolean", "array", "object")
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- tool configuration (stored in tool_version.config)
class ToolParam(In):
    name: str
    location: Literal["path", "query", "header", "body"] = Field(alias="in")
    type: Literal["string", "integer", "number", "boolean", "array", "object"] = "string"
    description: str = Field(default="", max_length=500)
    required: bool = False
    enum: list[str] | None = Field(default=None, max_length=100)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not PARAM_NAME.match(v):
            raise ValueError("parameter names are letters, digits and underscores (max 48)")
        return v


class HttpConfig(In):
    kind: Literal["http"] = "http"
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    url: str = Field(min_length=8, max_length=2000)
    params: list[ToolParam] = Field(default_factory=list, max_length=40)
    headers: dict[str, str] = Field(default_factory=dict, max_length=20)
    credential: str | None = Field(default=None, max_length=80)
    response_max_chars: int = Field(default=8000, ge=500, le=64000)

    @model_validator(mode="after")
    def _check(self) -> HttpConfig:
        if not re.match(r"^https?://", self.url):
            raise ValueError("url must start with http:// or https://")
        names = [p.name for p in self.params]
        if len(names) != len(set(names)):
            raise ValueError("parameter names must be unique")
        in_url = set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", self.url))
        path_params = {p.name for p in self.params if p.location == "path"}
        if in_url != path_params:
            raise ValueError(
                "path parameters and {placeholders} in the url must match: "
                f"url has {sorted(in_url)}, parameters declare {sorted(path_params)}"
            )
        if self.method == "GET" and any(p.location == "body" for p in self.params):
            raise ValueError("GET has no body: use query parameters")
        for key in self.headers:
            if key.lower() in ("authorization", "host", "cookie", "proxy-authorization"):
                raise ValueError(f"header {key!r} is set by Forge: use a credential")
        return self


class ToolSqlParam(In):
    name: str
    type: Literal["string", "integer", "number", "boolean"] = "string"
    description: str = Field(default="", max_length=500)
    required: bool = True

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not re.match(r"^[a-z_][a-z0-9_]{0,47}$", v):
            raise ValueError("SQL parameter names are lower-case letters, digits and underscores")
        return v


class SqlConfig(In):
    kind: Literal["sql"] = "sql"
    datasource: str = Field(min_length=1, max_length=80)
    query: str = Field(min_length=6, max_length=8000)
    params: list[ToolSqlParam] = Field(default_factory=list, max_length=20)
    row_limit: int = Field(default=100, ge=1, le=1000)


class McpConfig(In):
    kind: Literal["mcp"] = "mcp"
    server: str = Field(min_length=1, max_length=80)
    tool: str = Field(min_length=1, max_length=200)
    schema_in: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    response_max_chars: int = Field(default=8000, ge=500, le=64000)


ToolConfigIn = Annotated[HttpConfig | SqlConfig | McpConfig, Field(discriminator="kind")]


class ToolVersionIn(In):
    description: str = Field(min_length=1, max_length=1000)
    config: ToolConfigIn
    timeout_s: int = Field(default=15, ge=1, le=60)
    requires_approval: bool = False
    note: str = Field(default="", max_length=500)


class ToolCreateIn(ToolVersionIn):
    name: str

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not TOOL_NAME.match(v):
            raise ValueError("tool names are lower-case letters, digits and underscores (max 48)")
        return v


class ToolDuplicateIn(In):
    name: str

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not TOOL_NAME.match(v):
            raise ValueError("tool names are lower-case letters, digits and underscores (max 48)")
        return v


class ToolPublishIn(In):
    version: int | None = Field(default=None, ge=1, description="Empty = the latest version")


class ToolVersionOut(Out):
    version: int
    description: str
    config: dict[str, Any]
    schema_in: dict[str, Any]
    timeout_s: int
    requires_approval: bool
    writes: bool
    note: str
    created_at: datetime
    created_by: uuid.UUID | None


class ToolOut(Out):
    id: uuid.UUID
    name: str
    kind: str
    description: str
    latest_version: int
    published_version: int | None
    writes: bool = False
    requires_approval: bool = False
    updated_at: datetime
    created_at: datetime


class ToolDetail(ToolOut):
    version_detail: ToolVersionOut
    versions: list[int]


class ToolPage(Out):
    items: list[ToolOut]
    next_cursor: str | None


class ToolReferenceOut(Out):
    kind: str
    id: uuid.UUID
    name: str
    version: int | None = None


class ToolTestIn(In):
    version: int | None = Field(default=None, ge=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolTestOut(Out):
    status: str
    error_code: str | None
    content: str
    output: Any
    request: dict[str, Any]
    latency_ms: int
    call_id: uuid.UUID


class ToolCallOut(Out):
    id: uuid.UUID
    tool_id: uuid.UUID | None
    tool_name: str
    version: int | None
    source: str
    run_id: uuid.UUID | None
    user_id: uuid.UUID | None
    status: str
    error_code: str | None
    input: dict[str, Any] | None
    output: dict[str, Any] | None
    latency_ms: int
    created_at: datetime


# ---- OpenAPI import
class OpenApiPreviewIn(In):
    document: str = Field(min_length=2, max_length=2_000_000)


class OpenApiOperationOut(Out):
    operation_id: str
    method: str
    path: str
    summary: str
    name: str
    base_url: str


class OpenApiPreviewOut(Out):
    title: str
    base_urls: list[str]
    security: list[str]
    operations: list[OpenApiOperationOut]


class OpenApiImportIn(In):
    document: str = Field(min_length=2, max_length=2_000_000)
    operations: list[str] = Field(min_length=1, max_length=100)
    base_url: str | None = Field(default=None, max_length=2000)
    credential: str | None = Field(default=None, max_length=80)


class OpenApiImportOut(Out):
    created: list[ToolOut]
    skipped: list[str]


# ---- credentials, policy, data sources, MCP servers
class CredentialIn(In):
    name: str
    kind: Literal["bearer", "basic", "header", "query"]
    secret: str = Field(min_length=1, max_length=4000)
    username: str | None = Field(default=None, max_length=200)
    param_name: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _check(self) -> CredentialIn:
        if not LABEL.match(self.name):
            raise ValueError("name: letters, digits, space, _ . - (max 80)")
        if self.kind == "basic" and not self.username:
            raise ValueError("basic credentials need a username")
        if self.kind in ("header", "query") and not self.param_name:
            raise ValueError(f"{self.kind} credentials need the header or parameter name")
        return self


class CredentialOut(Out):
    id: uuid.UUID
    name: str
    kind: str
    username: str | None
    param_name: str | None
    used_by: int = 0
    created_at: datetime


class PolicyIn(In):
    allowed_hosts: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("allowed_hosts")
    @classmethod
    def _hosts(cls, hosts: list[str]) -> list[str]:
        clean = list(dict.fromkeys(h.strip().lower() for h in hosts if h.strip()))
        for h in clean:
            if not HOST.match(h):
                raise ValueError(f"invalid host pattern: {h!r}")
        return clean


class PolicyOut(Out):
    allowed_hosts: list[str]
    allow_private: bool = False


class DataSourceIn(In):
    name: str
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=100)
    username: str = Field(min_length=1, max_length=100)
    password: str | None = Field(default=None, max_length=1000)
    ssl_mode: Literal["disable", "require", "verify-full"] = "require"

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not LABEL.match(v):
            raise ValueError("name: letters, digits, space, _ . - (max 80)")
        return v

    @field_validator("host")
    @classmethod
    def _host(cls, v: str) -> str:
        v = v.lower()
        if not re.match(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$", v):
            raise ValueError("invalid host")
        return v


class DataSourceOut(Out):
    id: uuid.UUID
    name: str
    kind: str
    host: str
    port: int
    database: str
    username: str
    ssl_mode: str
    used_by: int = 0
    created_at: datetime


class McpServerIn(In):
    name: str
    transport: Literal["http", "sse", "stdio"]
    url: str | None = Field(default=None, max_length=2000)
    command: str | None = Field(default=None, max_length=200)
    args: list[str] = Field(default_factory=list, max_length=30)
    credential: str | None = Field(default=None, max_length=80)
    enabled: bool = True

    @model_validator(mode="after")
    def _check(self) -> McpServerIn:
        if not LABEL.match(self.name):
            raise ValueError("name: letters, digits, space, _ . - (max 80)")
        if self.transport == "stdio":
            if not self.command:
                raise ValueError("stdio servers need a command")
        elif not self.url or not re.match(r"^https?://", self.url):
            raise ValueError("http and sse servers need an http(s) url")
        return self


class McpServerOut(Out):
    id: uuid.UUID
    name: str
    transport: str
    url: str | None
    command: str | None
    args: list[str]
    credential: str | None
    enabled: bool
    used_by: int = 0
    created_at: datetime


class McpToolOut(Out):
    name: str
    description: str
    schema_in: dict[str, Any]
    imported_as: str | None = None
    changed: bool = False


class McpImportIn(In):
    tools: list[str] = Field(min_length=1, max_length=100)
