"""Process-level settings (ADR-007).

Only two kinds of values come from the environment:

* bootstrap values from `.env` (`FORGE_ADMIN_*`, database URL, master key, public URL);
* infrastructure wiring injected by Compose/Helm (Redis, S3, Temporal, OpenSearch), with defaults
  that match the shipped deployment files.

Everything else is an application setting stored in PostgreSQL and edited from the GUI.
"""

from __future__ import annotations

import base64
import binascii
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FORGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Bootstrap (.env)
    admin_username: str = "admin"
    admin_email: str = "admin@example.com"
    admin_password: SecretStr | None = None
    database_url: SecretStr = SecretStr("postgresql+asyncpg://forge:forge@localhost:5432/forge")
    master_key: SecretStr | None = None
    public_url: str = "http://localhost:8080"

    # Infrastructure wiring (deployment env, not user-editable)
    db_app_role: str = "forge_app"
    db_pool_size: int = Field(default=10, ge=1)
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint: str = "http://localhost:8333"
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None
    s3_bucket: str = "forge"
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_ingest_queue: str = "ingest"
    temporal_runs_queue: str = "runs"
    # Activities one worker process runs at once. Runs are I/O bound (model and tool calls) and
    # many can wait together; ingestion parses and embeds, so it stays low (CPU).
    worker_runs_activities: int = Field(default=64, ge=1, le=1000)
    worker_ingest_activities: int = Field(default=8, ge=1, le=256)
    # BM25 full-text engine (ADR-016); the CA file verifies its TLS certificate.
    search_url: str = "https://localhost:9200"
    search_username: str = "forge"
    search_password: SecretStr | None = None
    search_ca_file: str | None = None
    # Alias of the chunk index; the physical index is `<alias>-v<N>` (ADR-016).
    search_index: str = "forge-chunks"

    # Observability (ADR-009): OTLP/HTTP endpoint of the collector (spans are exported only when
    # set) and the internal port of the Prometheus `/metrics` server (0 = off).
    otlp_endpoint: str | None = None
    metrics_port: int = Field(default=9100, ge=0, le=65535)
    # Agent `http_get` may reach private addresses only when this deployment flag is on (labs).
    agent_allow_private_hosts: bool = False
    # Library tools (HTTP, MCP over HTTP, SQL data sources) may reach private addresses only when
    # this flag is on; MCP `stdio` runs only the executables listed here (comma separated).
    tools_allow_private_hosts: bool = False
    # Sandbox runner service (E3.4): empty URL = Python tools and code nodes are unavailable.
    sandbox_url: str = ""
    sandbox_token: SecretStr | None = None
    mcp_stdio_allowed: str = ""
    # Directories (comma separated) that `filesystem` RAG sources may read: mount SMB or NFS shares
    # there. Empty = the connector is off (E2.13).
    source_roots: str = ""

    # Email for approval notifications (M2.4); no host = no email, the Approvals page still works.
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: SecretStr | None = None
    smtp_from: str = "forge@localhost"
    smtp_starttls: bool = True
    # Hours an approval waits when the node does not say (at most 720 per node).
    approval_default_hours: int = Field(default=72, ge=1, le=720)

    # Boot-time log level, used until the GUI setting is loaded from the database.
    log_level: str = "INFO"

    @property
    def secure_cookies(self) -> bool:
        """Cookies get the Secure flag whenever the public URL is HTTPS."""
        return self.public_url.startswith("https://")

    @field_validator("master_key")
    @classmethod
    def _master_key_is_32_bytes(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        try:
            raw = base64.b64decode(value.get_secret_value(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("FORGE_MASTER_KEY must be base64") from exc
        if len(raw) != 32:
            raise ValueError("FORGE_MASTER_KEY must decode to 32 bytes")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
