"""Process-level settings (ADR-007).

Only two kinds of values come from the environment:

* bootstrap values from `.env` (`FORGE_ADMIN_*`, database URL, master key, public URL);
* infrastructure wiring injected by Compose/Helm (Redis, S3, Temporal), with defaults
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
