"""Use case inputs and outputs (E2.4, E2.5). Inputs forbid unknown fields (threat model TB1)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$")
USE_CASE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.()-]{0,79}$")  # "(imported)" copies
CATEGORY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.&-]{0,79}$")
FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
Pattern = Literal["supervisor", "graph"]
Status = Literal["draft", "testing", "published", "deprecated"]
OutputType = Literal["markdown", "json", "docx", "xlsx", "pdf", "chat"]
FieldType = Literal["text", "long_text", "number", "select", "file", "collection"]
HitlPolicy = Literal["agent_setting", "always", "never"]


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def _name(v: str, pattern: re.Pattern[str] = NAME) -> str:
    if not pattern.match(v):
        raise ValueError("1-80 characters: letters, digits, space, _ . -")
    return v


# ------------------------------------------------------------------ catalogs
class CatalogIn(In):
    name: str
    description: str = Field(default="", max_length=500)
    status: Literal["active", "disabled"] = "active"

    _n = field_validator("name")(_name)


class CategoryIn(In):
    name: str
    description: str = Field(default="", max_length=500)

    @field_validator("name")
    @classmethod
    def _ok(cls, v: str) -> str:
        return _name(v, CATEGORY_NAME)


class CategoryOut(Out):
    id: uuid.UUID
    catalog_id: uuid.UUID
    name: str
    description: str
    use_cases: int = 0


class CatalogOut(Out):
    id: uuid.UUID
    name: str
    description: str
    status: str
    builtin: bool
    categories: list[CategoryOut]
    use_cases: int = 0


class CatalogAccessItem(In):
    catalog_id: uuid.UUID
    enabled: bool
    excluded_categories: list[uuid.UUID] = Field(default_factory=list, max_length=500)


class CatalogAccessIn(In):
    catalogs: list[CatalogAccessItem] = Field(max_length=200)


class CatalogAccessOut(BaseModel):
    role_name_id: uuid.UUID
    role_name: str
    catalogs: list[CatalogAccessItem]


# ------------------------------------------------------------------ use cases
class UseCaseInputField(In):
    name: str
    label: str = Field(default="", max_length=120)
    type: FieldType
    required: bool = False
    help: str = Field(default="", max_length=500)
    options: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=100
    )
    default: str | float | None = None
    min: float | None = None
    max: float | None = None

    @field_validator("name")
    @classmethod
    def _name_ok(cls, v: str) -> str:
        if not FIELD_NAME.match(v):
            raise ValueError("field names look like [a-z][a-z0-9_]*, up to 48 characters")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> UseCaseInputField:
        if self.type == "select" and not self.options:
            raise ValueError("a select field needs options")
        if self.type != "select" and self.options:
            raise ValueError("only select fields have options")
        if self.type == "select" and self.default is not None and self.default not in self.options:
            raise ValueError("the default is not one of the options")
        if self.type == "number" and self.default is not None and isinstance(self.default, str):
            raise ValueError("the default of a number field must be a number")
        if self.type in {"text", "long_text"} and isinstance(self.default, float):
            raise ValueError("the default of a text field must be text")
        if self.type in {"file", "collection"} and self.default is not None:
            raise ValueError("file and collection fields have no default")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min is greater than max")
        return self


class UseCaseDefaults(In):
    model_aliases: dict[str, str] = Field(default_factory=dict, max_length=20)
    max_cost_usd: float = Field(default=5.0, ge=0.01, le=100)
    max_duration_s: int = Field(default=1800, ge=10, le=86400)
    hitl_policy: HitlPolicy = "agent_setting"


class UseCaseBody(In):
    """The editable part of a use case; saving overwrites the draft."""

    description: str = Field(default="", max_length=2000)
    requirements: str = Field(default="", max_length=4000)
    icon: str = Field(default="", max_length=60)
    tags: list[Annotated[str, Field(min_length=1, max_length=40)]] = Field(
        default_factory=list, max_length=20
    )
    catalog_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    input_fields: list[UseCaseInputField] = Field(default_factory=list, max_length=50)
    output_type: OutputType = "markdown"
    output_schema: dict[str, Any] | None = None
    defaults: UseCaseDefaults = Field(default_factory=UseCaseDefaults)
    definition: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _consistent(self) -> UseCaseBody:
        names = [f.name for f in self.input_fields]
        if len(names) != len(set(names)):
            raise ValueError("input field names must be unique")
        if self.output_type == "json" and self.output_schema is None:
            raise ValueError("a JSON output needs its schema")
        if self.output_type != "json" and self.output_schema is not None:
            raise ValueError("only a JSON output has a schema")
        if self.category_id and not self.catalog_id:
            raise ValueError("a category needs its catalog")
        return self


class UseCaseCreateIn(UseCaseBody):
    name: str
    pattern: Pattern

    @field_validator("name")
    @classmethod
    def _name_ok(cls, v: str) -> str:
        return _name(v, USE_CASE_NAME)


class UseCaseUpdateIn(UseCaseBody):
    name: str | None = None

    @field_validator("name")
    @classmethod
    def _ok(cls, v: str | None) -> str | None:
        return None if v is None else _name(v, USE_CASE_NAME)


class UseCaseDuplicateIn(In):
    name: str

    @field_validator("name")
    @classmethod
    def _name_ok(cls, v: str) -> str:
        return _name(v, USE_CASE_NAME)


class UseCaseStatusIn(In):
    status: Literal["draft", "testing", "deprecated"]


class UseCasePublishIn(In):
    version: int | None = Field(
        default=None,
        ge=1,
        description="Empty = publish the draft as a new version; a number = roll back to it",
    )
    note: str = Field(default="", max_length=300)


class UseCaseIssueOut(BaseModel):
    code: str
    message: str
    node: str | None = None


class UseCaseOut(Out):
    id: uuid.UUID
    name: str
    description: str
    icon: str
    tags: list[str]
    catalog_id: uuid.UUID | None
    catalog: str | None = None
    category_id: uuid.UUID | None
    category: str | None = None
    pattern: str
    status: str
    latest_version: int
    published_version: int | None
    dirty: bool
    updated_at: datetime
    created_at: datetime


class UseCasePage(BaseModel):
    items: list[UseCaseOut]


class UseCaseDetail(UseCaseOut):
    requirements: str = ""
    input_fields: list[dict[str, Any]]
    input_schema: dict[str, Any]
    output_type: str
    output_schema: dict[str, Any] | None
    defaults: dict[str, Any]
    definition: dict[str, Any]
    issues: list[UseCaseIssueOut] = Field(default_factory=list)
    versions: list[int] = Field(default_factory=list)


class UseCaseVersionSummary(Out):
    version: int
    note: str
    published_at: datetime
    published_by: uuid.UUID | None
    live: bool = False


class UseCaseVersionDetail(UseCaseVersionSummary):
    name: str
    description: str
    pattern: str
    input_fields: list[dict[str, Any]]
    input_schema: dict[str, Any]
    output_type: str
    output_schema: dict[str, Any] | None
    defaults: dict[str, Any]
    definition: dict[str, Any]


class UseCaseVersionDiff(BaseModel):
    left: UseCaseVersionDetail
    right: UseCaseVersionDetail
    changed: list[str]


class UseCaseReferenceOut(BaseModel):
    kind: str
    id: uuid.UUID
    name: str


class CatalogUseCase(Out):
    """What an end user sees: no agents, prompts or graphs (US-2.9.1)."""

    id: uuid.UUID
    name: str
    description: str
    icon: str
    tags: list[str]
    catalog_id: uuid.UUID
    catalog: str
    category_id: uuid.UUID
    category: str
    version: int
    input_schema: dict[str, Any]
    input_fields: list[dict[str, Any]]
    output_type: str
    favorite: bool = False
    last_run_at: datetime | None = None


# ------------------------------------------------------------------ runs
RunStatus = Literal["queued", "running", "waiting_approval", "succeeded", "failed", "cancelled"]


class RunStartIn(In):
    inputs: dict[str, Any] = Field(default_factory=dict)
    mode: Literal["test", "published"] = Field(
        default="published",
        description="`test` runs the current draft (builders); `published` the live version",
    )
    session_id: uuid.UUID | None = Field(
        default=None, description="Add the run to this session; a new session is opened if absent"
    )
    delivery: Literal["async", "sync", "stream"] = Field(
        default="async",
        description="`async` returns the run id at once; `sync` waits up to `timeout_s`; "
        "`stream` answers with server-sent events until the run ends",
    )
    timeout_s: int = Field(default=60, ge=1, le=300, description="Wait of `sync` delivery")
    webhook_url: str | None = Field(
        default=None,
        max_length=2000,
        description="Called with the run id and status when the run ends (host must be in the "
        "project's egress allowlist)",
    )


class RunStepOut(Out):
    step_key: str
    agent_run_id: uuid.UUID | None = None
    node_id: str
    node_type: str
    ref: str | None
    status: str
    input: Any | None
    output: Any | None
    error_code: str | None
    error: str | None
    tokens_in: int
    tokens_out: int
    cost: float
    latency_ms: int
    started_at: datetime
    finished_at: datetime | None


class RunFileOut(Out):
    id: uuid.UUID
    kind: str
    name: str
    content_type: str
    size: int
    created_at: datetime


class UseCaseRunOut(Out):
    id: uuid.UUID
    session_id: uuid.UUID | None = None
    use_case_id: uuid.UUID
    use_case_name: str
    version: int | None
    mode: str
    status: RunStatus
    error_code: str | None
    error: str | None
    error_node: str | None
    steps: int
    tokens_in: int
    tokens_out: int
    cost: float
    created_at: datetime
    created_by: uuid.UUID | None
    started_at: datetime | None
    finished_at: datetime | None


class UseCaseRunDetail(UseCaseRunOut):
    inputs: dict[str, Any]
    outputs: dict[str, Any] | None
    limits: dict[str, Any]
    graph: dict[str, Any] | None = None
    step_list: list[RunStepOut] = Field(default_factory=list)
    files: list[RunFileOut] = Field(default_factory=list)
    webhook_url: str | None = None
    webhook_status: str | None = None
    webhook_error: str | None = None


class SessionOut(Out):
    id: uuid.UUID
    use_case_id: uuid.UUID
    use_case_name: str
    title: str
    runs: int
    last_status: str | None
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class SessionDetail(SessionOut):
    run_list: list[UseCaseRunOut] = Field(default_factory=list)


# ------------------------------------------------------------------ approvals (M2.4)
class ApprovalOut(Out):
    id: uuid.UUID
    run_id: uuid.UUID
    use_case_id: uuid.UUID
    use_case_name: str
    node_id: str
    kind: str
    status: str
    requested_at: datetime
    expires_at: datetime
    decided_by: uuid.UUID | None = None
    decided_at: datetime | None = None
    decided_via: str | None = None
    comment: str | None = None


class ApprovalDetail(ApprovalOut):
    instructions: str
    content: dict[str, Any]
    edited: Any | None = None
    assignees: list[str] = Field(default_factory=list)


class DecisionIn(In):
    decision: Literal["approve", "reject"]
    edited: Any | None = Field(
        default=None,
        description="Approve with changes: the edited text (review) or arguments object "
        "(tool call). Absent = approve as proposed",
    )
    comment: str = Field(default="", max_length=2000)
