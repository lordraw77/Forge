"""Prompt Studio inputs and outputs (spec Part 2 "Prompt fields", US-1.10.x to US-1.13.1).
Inputs forbid unknown fields (threat model TB1)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from forge.prompts.templates import MAX_TEMPLATE_CHARS, MAX_VALUE_CHARS, valid_name

Tag = Literal["draft", "staging", "prod"]
Framework = Literal["free_form", "co_star", "risen", "rtf", "crispe"]
Status = Literal["draft", "published"]
MAX_VARIABLES = 50
MAX_EXAMPLES = 20
DEFAULT_MODEL = "default-chat"


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Variable(In):
    name: str = Field(min_length=1, max_length=64)
    # Default value used by Try and pre-filled on the test page.
    value: str = Field(default="", max_length=MAX_VALUE_CHARS)
    is_file: bool = False
    required: bool = True

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not valid_name(v):
            raise ValueError("letters, digits and _ only; must not start with a digit")
        return v


class Example(In):
    input: str = Field(max_length=MAX_TEMPLATE_CHARS)
    output: str = Field(max_length=MAX_TEMPLATE_CHARS)


class ModelParams(In):
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    max_tokens: int | None = Field(default=None, ge=1, le=200_000)


class VersionIn(In):
    """The editable content of a prompt; saving it creates the next immutable version."""

    model: str = Field(default=DEFAULT_MODEL, min_length=1, max_length=100)
    system_template: str = Field(default="", max_length=MAX_TEMPLATE_CHARS)
    user_template: str = Field(default="", max_length=MAX_TEMPLATE_CHARS)
    variables: list[Variable] = Field(default_factory=list, max_length=MAX_VARIABLES)
    examples: list[Example] = Field(default_factory=list, max_length=MAX_EXAMPLES)
    model_params: ModelParams = Field(default_factory=ModelParams)
    note: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def _content(self) -> VersionIn:
        if not self.system_template and not self.user_template:
            raise ValueError("system or user prompt is required")
        names = [v.name for v in self.variables]
        if len(names) != len(set(names)):
            raise ValueError("variable names must be unique")
        return self


class PromptCreateIn(In):
    name: str = Field(min_length=1, max_length=120)
    # Asked on first save (spec: "Write a few lines about the prompt").
    description: str = Field(min_length=1, max_length=2000)
    version: VersionIn


class PromptUpdateIn(In):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class DuplicateIn(In):
    name: str = Field(min_length=1, max_length=120)


class TagIn(In):
    version: int = Field(ge=1)


class PublishIn(In):
    version: int | None = Field(default=None, ge=1, description="Defaults to the latest version")


Values = Annotated[
    dict[str, Annotated[str, Field(max_length=MAX_VALUE_CHARS)]], Field(max_length=MAX_VARIABLES)
]


class RunIn(In):
    version: int | None = Field(default=None, ge=1, description="Defaults to the latest version")
    variables: Values = Field(default_factory=dict)
    model: str | None = Field(
        default=None, max_length=100, description="Overrides the version's model"
    )


class PlaygroundIn(In):
    version: int | None = Field(default=None, ge=1)
    variables: Values = Field(default_factory=dict)
    models: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        min_length=2, max_length=4
    )

    @field_validator("models")
    @classmethod
    def _distinct(cls, v: list[str]) -> list[str]:
        if len({m.lower() for m in v}) != len(v):
            raise ValueError("models must be distinct")
        return v


class ParseIn(In):
    system_template: str = Field(default="", max_length=MAX_TEMPLATE_CHARS)
    user_template: str = Field(default="", max_length=MAX_TEMPLATE_CHARS)


class TemplateIssue(Out):
    field: Literal["system_template", "user_template"]
    line: int | None
    message: str


class ParseOut(Out):
    variables: list[str]
    issues: list[TemplateIssue]


class AssistIn(In):
    description: str = Field(min_length=10, max_length=4000)
    framework: Framework | None = Field(
        default=None, description="None: the model recommends one and says which"
    )


class AssistOut(Out):
    framework: Framework
    recommended: bool
    system_template: str
    user_template: str
    run_id: uuid.UUID


# ---------------------------------------------------------------- outputs
class VersionOut(Out):
    version: int
    model: str
    system_template: str
    user_template: str
    variables: list[Variable]
    examples: list[Example]
    model_params: ModelParams
    note: str
    tags: list[Tag]
    created_at: datetime
    created_by: uuid.UUID | None


class VersionSummary(Out):
    version: int
    model: str
    note: str
    tags: list[Tag]
    created_at: datetime
    created_by: uuid.UUID | None


class PromptOut(Out):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str
    status: Status
    latest_version: int
    published_version: int | None
    tags: dict[str, int]
    model: str
    created_at: datetime
    created_by: uuid.UUID | None
    updated_at: datetime


class PromptDetail(PromptOut):
    version: VersionOut


class PromptPage(Out):
    items: list[PromptOut]
    next_cursor: str | None


class Usage(Out):
    tokens_in: int
    tokens_out: int
    total_tokens: int


class RunOut(Out):
    """A run with its call trace: latency, cost, usage, input, output, metadata."""

    id: uuid.UUID
    prompt_id: uuid.UUID | None
    version: int | None
    kind: Literal["test", "playground", "assist"]
    group_id: uuid.UUID | None
    model: str
    model_name: str | None
    status: Literal["ok", "error"]
    error_code: str | None
    error: str | None
    output: str | None
    finish_reason: str | None
    usage: Usage
    cost: Decimal
    latency_ms: int
    cache_hit: bool
    created_at: datetime
    created_by: uuid.UUID | None


class RunDetail(RunOut):
    variables: dict[str, Any]
    messages: list[dict[str, Any]]
    params: dict[str, Any]
    llm_call_id: uuid.UUID | None


class ReferenceOut(Out):
    kind: str
    id: uuid.UUID
    name: str
