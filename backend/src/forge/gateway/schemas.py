"""Registry inputs and outputs (F1.2, US-1.5.x). Inputs forbid unknown fields (threat model TB1)."""

from __future__ import annotations

import ipaddress
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    WithJsonSchema,
    field_validator,
    model_validator,
)

ProviderType = Literal[
    "vllm",
    "llamacpp",
    "nim",
    "ollama",
    "openai",
    "azure",
    "anthropic",
    "bedrock",
    "gemini",
    "vertex",
    "mistral",
    "openai_compatible",
]
ModelType = Literal["chat", "embedding", "reranker", "speech", "guard"]
Capability = Literal["tools", "vision", "json", "streaming"]

# Provider types that only make sense with an explicit endpoint (self-hosted or generic).
BASE_URL_REQUIRED = frozenset({"vllm", "llamacpp", "ollama", "azure", "openai_compatible"})
RERANK_PROVIDERS = frozenset({"vllm", "llamacpp", "nim", "openai_compatible"})
HALFVEC_FROM = 2001  # pgvector `vector` indexes stop at 2000 dimensions (ADR-008)
NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$"
TOKENIZER_PATTERN = (
    r"^(hf:[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*|tiktoken:[a-z0-9_]+)$"
)
_BLOCKED_HOSTS = frozenset({"metadata.google.internal", "metadata"})


# Cost per 1M tokens. A decimal string in the API (numbers are accepted on input).
Cost = Annotated[
    Decimal,
    Field(ge=0, max_digits=14, decimal_places=6),
    WithJsonSchema({"type": "string", "pattern": r"^\d+(\.\d{1,6})?$"}),
]


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def check_base_url(value: str | None) -> str | None:
    """http(s) only; cloud metadata endpoints are refused (SSRF, threat model §5)."""
    if value is None or value == "":
        return None
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("base URL must be an http(s) URL")
    host = parts.hostname.lower()
    if host in _BLOCKED_HOSTS:
        raise ValueError("cloud metadata endpoints are not allowed")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return value.rstrip("/")
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        raise ValueError("link-local, multicast and unspecified addresses are not allowed")
    return value.rstrip("/")


class ProviderSettings(In):
    # Plain HTTP is for on-prem endpoints on a trusted network only (threat model TB3).
    allow_http: bool = False
    api_version: str | None = Field(default=None, max_length=40)
    region: str | None = Field(default=None, max_length=40)
    vertex_project: str | None = Field(default=None, max_length=120)
    vertex_location: str | None = Field(default=None, max_length=40)
    headers: dict[str, str] = Field(default_factory=dict, max_length=20)

    @field_validator("headers")
    @classmethod
    def _no_auth_headers(cls, value: dict[str, str]) -> dict[str, str]:
        for name in value:
            if name.lower() in ("authorization", "api-key", "x-api-key"):
                raise ValueError("credentials go in the API key field, not in headers")
        return value


class ProviderIn(In):
    name: str = Field(min_length=2, max_length=80)
    provider_type: ProviderType
    base_url: str | None = Field(default=None, max_length=500)
    # Write-only. On update: omitted or null keeps the stored value, "" removes it.
    api_key: SecretStr | None = Field(default=None)
    config: ProviderSettings = Field(default_factory=ProviderSettings)
    status: Literal["active", "disabled"] = "active"

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str | None) -> str | None:
        return check_base_url(value)

    @model_validator(mode="after")
    def _type_requirements(self) -> ProviderIn:
        if self.provider_type in BASE_URL_REQUIRED and not self.base_url:
            raise ValueError(f"base_url is required for provider type {self.provider_type}")
        if self.base_url and self.base_url.startswith("http://") and not self.config.allow_http:
            raise ValueError(
                "plain HTTP needs config.allow_http (on-prem endpoints on a trusted network)"
            )
        if self.provider_type == "azure" and not self.config.api_version:
            raise ValueError("Azure OpenAI needs config.api_version")
        if self.provider_type == "bedrock" and not self.config.region:
            raise ValueError("AWS Bedrock needs config.region")
        if self.provider_type == "vertex" and not (
            self.config.vertex_project and self.config.vertex_location
        ):
            raise ValueError("Vertex AI needs config.vertex_project and config.vertex_location")
        return self


class ProviderOut(Out):
    id: uuid.UUID
    project_id: uuid.UUID | None
    name: str
    provider_type: str
    base_url: str | None
    config: dict[str, Any]
    status: str
    has_credentials: bool
    model_count: int = 0
    created_at: datetime
    updated_at: datetime


class ModelDraft(In):
    """The fields a Test Connection needs; a saved model adds identity and enablement."""

    model_type: ModelType
    model_id: str = Field(min_length=1, max_length=200)
    params: dict[str, Any] = Field(default_factory=dict, max_length=30)
    embedding_dims: int | None = Field(default=None, ge=1)
    timeout_s: int = Field(default=60, ge=1, le=3600)


class ModelIn(ModelDraft):
    name: str = Field(min_length=1, max_length=100, pattern=NAME_PATTERN)
    display_name: str = Field(min_length=1, max_length=120)
    provider_id: uuid.UUID
    context_window: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    cost_input: Cost = Decimal(0)
    cost_output: Cost = Decimal(0)
    capabilities: list[Capability] = Field(default_factory=list)
    max_retries: int = Field(default=2, ge=0, le=10)
    rpm_limit: int | None = Field(default=None, ge=1)
    # Exact token counts (chunking, context budget, quotas): `hf:<org>/<repo>` (tokenizer.json
    # from Hugging Face) or `tiktoken:<encoding>`. Unset: estimated.
    tokenizer: str | None = Field(default=None, max_length=200, pattern=TOKENIZER_PATTERN)
    # Platform models only: which projects may use the model (F1.2 "Enabled projects").
    all_projects: bool = False
    enabled_project_ids: list[uuid.UUID] = Field(default_factory=list, max_length=1000)
    status: Literal["active", "disabled"] = "active"

    @model_validator(mode="after")
    def _embedding_dims(self) -> ModelIn:
        if self.model_type == "embedding":
            if self.embedding_dims is None:
                raise ValueError("embedding models need embedding_dims")
            if self.embedding_dims > 4000:
                raise ValueError("embedding_dims above 4000 are not supported (pgvector halfvec)")
        else:
            self.embedding_dims = None
        if (
            self.context_window
            and self.max_output_tokens
            and self.max_output_tokens > self.context_window
        ):
            raise ValueError("max_output_tokens cannot exceed context_window")
        return self


class ModelOut(Out):
    id: uuid.UUID
    project_id: uuid.UUID | None
    scope: Literal["platform", "project"]
    provider_id: uuid.UUID
    provider_name: str
    provider_type: str
    name: str
    display_name: str
    model_type: str
    model_id: str
    params: dict[str, Any]
    context_window: int | None
    max_output_tokens: int | None
    embedding_dims: int | None
    vector_storage: Literal["vector", "halfvec"] | None
    cost_input: Decimal
    cost_output: Decimal
    capabilities: list[str]
    timeout_s: int
    max_retries: int
    rpm_limit: int | None
    tokenizer: str | None
    all_projects: bool
    enabled_project_ids: list[uuid.UUID]
    status: str
    created_at: datetime
    updated_at: datetime


class ModelReference(Out):
    kind: Literal["alias"]
    name: str
    project_id: uuid.UUID | None


class AliasIn(In):
    model_ids: list[uuid.UUID] = Field(min_length=1, max_length=10)


class AliasOut(Out):
    name: str
    scope: Literal["platform", "project"]
    model_ids: list[uuid.UUID]
    model_names: list[str]
    overrides_platform: bool = False
    updated_at: datetime | None = None


class TestConnectionIn(In):
    """Test a draft before saving: a saved provider (optionally with a new key) or a draft one."""

    provider_id: uuid.UUID | None = None
    provider: ProviderIn | None = None
    api_key: SecretStr | None = None
    model: ModelDraft

    @model_validator(mode="after")
    def _one_provider(self) -> TestConnectionIn:
        if (self.provider_id is None) == (self.provider is None):
            raise ValueError("give exactly one of provider_id or provider")
        return self


class TestResult(Out):
    ok: bool
    latency_ms: int
    detail: str
    error_code: str | None = None
    sample: str | None = None
    dimensions: int | None = None


class ProjectGatewayConfigIn(In):
    """Project limits; null inherits the platform value, 0 means unlimited (if the platform
    ceiling allows it)."""

    tokens_per_day: int | None = Field(default=None, ge=0)
    requests_per_minute: int | None = Field(default=None, ge=0)
    user_tokens_per_day: int | None = Field(default=None, ge=0)
    user_requests_per_minute: int | None = Field(default=None, ge=0)
    max_tokens_per_call: int | None = Field(default=None, ge=0)
    max_tokens_policy: Literal["clamp", "reject"] | None = None
    cache_enabled: bool = False
    cache_ttl_s: int = Field(default=3600, ge=1, le=2_592_000)
    cache_nonzero_temperature: bool = False


class EffectiveLimits(Out):
    tokens_per_day: int
    requests_per_minute: int
    user_tokens_per_day: int
    user_requests_per_minute: int
    max_tokens_per_call: int
    max_tokens_policy: Literal["clamp", "reject"]
    cache_enabled: bool
    cache_ttl_s: int
    cache_nonzero_temperature: bool


class ProjectGatewayConfigOut(Out):
    config: ProjectGatewayConfigIn
    platform: EffectiveLimits
    effective: EffectiveLimits


class UsageOut(Out):
    day: str
    tokens_today: int
    tokens_per_day: int
    requests_this_minute: int
    requests_per_minute: int
    calls_today: int
    cost_today: Decimal
