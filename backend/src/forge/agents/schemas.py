"""Agent inputs and outputs (E1.18 to E1.20). Inputs forbid unknown fields (threat model TB1)."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from forge.guardrails.service import GuardrailRuleIn, validate_rules

Tag = Literal["draft", "staging", "prod"]
RunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
BuiltinTool = Literal["http_get", "current_time"]
TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
HOST = re.compile(r"^(\*\.)?[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")

# Platform ceilings for run limits (the agent's own limits may be lower, never higher).
MAX_STEPS = 25
MAX_TOKENS = 500_000
MAX_TIMEOUT_S = 900
MAX_COST_USD = 50.0


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------ configuration
class Limits(In):
    """Exceeding any limit ends the run `failed` with the reason (US-1.18.1 #2)."""

    max_steps: int = Field(default=8, ge=1, le=MAX_STEPS)
    max_tokens: int = Field(default=50_000, ge=1_000, le=MAX_TOKENS)
    timeout_s: int = Field(default=120, ge=10, le=MAX_TIMEOUT_S)
    max_cost_usd: float = Field(default=1.0, ge=0.01, le=MAX_COST_USD)


class PromptRef(In):
    """The agent's instructions: a Library prompt, pinned to a version when the agent version
    is saved. Its system template and examples are used; the chat message is the user turn."""

    prompt_id: uuid.UUID
    version: int | None = Field(default=None, ge=1, description="Empty = the published version")
    variables: dict[str, str] = Field(default_factory=dict, max_length=50)


class RagTool(In):
    kind: Literal["rag"] = "rag"
    pipeline_id: uuid.UUID
    version: int | None = Field(default=None, ge=1, description="Empty = the published version")
    name: str | None = Field(default=None, description="Tool name shown to the model")
    description: str | None = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        if v is not None and not TOOL_NAME.match(v):
            raise ValueError("tool names are lower-case letters, digits and underscores (max 48)")
        return v


class BuiltinToolRef(In):
    kind: Literal["builtin"] = "builtin"
    name: BuiltinTool
    allowed_hosts: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("allowed_hosts")
    @classmethod
    def _hosts(cls, hosts: list[str]) -> list[str]:
        clean = [h.strip().lower() for h in hosts if h.strip()]
        for h in clean:
            if not HOST.match(h):
                raise ValueError(f"invalid host pattern: {h!r}")
        return clean


class LibraryTool(In):
    """A Tools Studio tool, pinned to a version when the agent version is saved (M2.2)."""

    kind: Literal["library"] = "library"
    tool_id: uuid.UUID
    version: int | None = Field(default=None, ge=1, description="Empty = the published version")
    name: str | None = Field(default=None, description="Tool name shown to the model")

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        if v is not None and not TOOL_NAME.match(v):
            raise ValueError("tool names are lower-case letters, digits and underscores (max 48)")
        return v


ToolConfig = Annotated[RagTool | BuiltinToolRef | LibraryTool, Field(discriminator="kind")]


class MemoryConfig(In):
    """Long-term memory (E3.6): facts about the user that survive across conversations."""

    long_term: bool = False
    embedding_model: str = Field(default="embedding", min_length=1, max_length=100)
    recall: int = Field(default=5, ge=1, le=20, description="Facts added to each run")
    max_items: int = Field(default=200, ge=10, le=500, description="Per user; oldest unused go")


class AgentConfig(In):
    prompt: PromptRef
    model: str = Field(default="default-chat", min_length=1, max_length=100)
    tools: list[ToolConfig] = Field(default_factory=list, max_length=20)
    limits: Limits = Field(default_factory=Limits)
    temperature: float | None = Field(default=0.2, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, ge=16, le=64_000)
    # The agent's own guardrail rules, applied after the project's policy (E2.11): an agent can
    # be stricter than its project, never looser.
    guardrails: list[GuardrailRuleIn] = Field(default_factory=list, max_length=20)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)

    @model_validator(mode="after")
    def _unique_names(self) -> AgentConfig:
        validate_rules(self.guardrails)
        names = [t.name for t in self.tools if t.name]
        if len(names) != len(set(names)):
            raise ValueError("tool names must be unique")
        builtins = [t.name for t in self.tools if isinstance(t, BuiltinToolRef)]
        if len(builtins) != len(set(builtins)):
            raise ValueError("a built-in tool can be added once")
        return self


class AgentCreateIn(In):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    config: AgentConfig


class AgentVersionIn(In):
    config: AgentConfig
    note: str = Field(default="", max_length=500)


class AgentUpdateIn(In):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class AgentDuplicateIn(In):
    name: str = Field(min_length=1, max_length=120)


class AgentTagIn(In):
    version: int = Field(ge=1)


class AgentPublishIn(In):
    version: int | None = Field(default=None, ge=1)


# ------------------------------------------------------------------ agent outputs
class AgentVersionOut(Out):
    version: int
    config: AgentConfig
    note: str
    tags: list[Tag]
    created_at: datetime
    created_by: uuid.UUID | None


class AgentOut(Out):
    id: uuid.UUID
    name: str
    description: str
    status: Literal["draft", "published"]
    latest_version: int
    published_version: int | None
    tags: dict[str, int]
    model: str
    tools: int
    created_at: datetime
    updated_at: datetime


class AgentDetail(AgentOut):
    version: AgentVersionOut


class AgentPage(Out):
    items: list[AgentOut]
    next_cursor: str | None


class AvailableAgent(Out):
    id: uuid.UUID
    name: str
    description: str
    version: int
    published: bool


# ------------------------------------------------------------------ chat and runs
class ConversationIn(In):
    agent_id: uuid.UUID
    version: int | None = Field(
        default=None, ge=1, description="Builders may pick a version; others get the published one"
    )
    title: str = Field(default="", max_length=200)


class MessageIn(In):
    content: str = Field(min_length=1, max_length=20_000)


class ConversationOut(Out):
    id: uuid.UUID
    agent_id: uuid.UUID
    agent_name: str
    agent_version: int
    title: str
    created_at: datetime
    updated_at: datetime


class AgentCitation(Out):
    index: int
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_name: str
    page: int | None
    heading_path: str
    score: float
    excerpt: str
    url: str
    pipeline: str | None = None


class AgentStepOut(Out):
    ordinal: int
    kind: Literal["llm", "tool"]
    name: str
    status: Literal["ok", "error"]
    error_code: str | None
    input: Any | None
    output: Any | None
    model_name: str | None
    tokens_in: int
    tokens_out: int
    cost: Decimal
    latency_ms: int
    ttft_ms: int | None
    llm_call_id: uuid.UUID | None
    tool_call_id: str | None
    created_at: datetime


class FeedbackOut(Out):
    rating: Literal["up", "down"]
    comment: str
    updated_at: datetime


class FeedbackIn(In):
    rating: Literal["up", "down"]
    comment: str = Field(default="", max_length=2000)


class AgentRunOut(Out):
    id: uuid.UUID
    conversation_id: uuid.UUID
    agent_id: uuid.UUID
    agent_name: str | None = None
    agent_version: int
    input: str
    output: str | None
    status: RunStatus
    error_code: str | None
    error: str | None
    citations: list[AgentCitation]
    tokens_in: int
    tokens_out: int
    total_tokens: int
    cost: Decimal
    steps: int
    latency_ms: int | None
    trace_id: str | None
    created_by: uuid.UUID | None
    created_at: datetime
    finished_at: datetime | None
    feedback: FeedbackOut | None = None


class AgentRunDetail(AgentRunOut):
    limits: dict[str, Any]
    step_list: list[AgentStepOut]


class AgentRunPage(Out):
    items: list[AgentRunOut]
    next_cursor: str | None


class MessageOut(Out):
    role: Literal["user", "assistant"]
    content: str
    run_id: uuid.UUID
    status: RunStatus
    citations: list[AgentCitation] = Field(default_factory=list)
    feedback: FeedbackOut | None = None
    created_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class AgentUsageDay(Out):
    day: date
    runs: int
    succeeded: int
    failed: int
    cancelled: int
    tokens_in: int
    tokens_out: int
    cost: Decimal


class AgentUsageOut(Out):
    days: list[AgentUsageDay]
    total_runs: int
    total_tokens: int
    total_cost: Decimal
