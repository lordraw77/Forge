"""In-memory model registry: providers, models, aliases and project limits (ADR-005, ADR-007).

Loaded at start and reloaded on every `gateway` notification on the config channel, so a
change made in any process applies to the next request everywhere, without restarts. Loading
reads every project's rows, so it runs on the reviewed `forge_admin` path (ADR-002).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import GatewayLimits, SettingsStore, secrets
from forge.core.crypto import Envelope
from forge.core.db import admin_scope
from forge.core.errors import ProblemError
from forge.core.logging import get_logger
from forge.gateway.models import (
    GatewayProjectConfig,
    Model,
    ModelAlias,
    ModelEnablement,
    ModelProvider,
)
from forge.gateway.schemas import EffectiveLimits

log = get_logger(__name__)
NOTIFY_KEY = "gateway"
STANDARD_ALIASES = ("default-chat", "fast", "embedding", "rerank")


@dataclass(frozen=True)
class Provider:
    id: uuid.UUID
    project_id: uuid.UUID | None
    name: str
    provider_type: str
    base_url: str | None
    api_key: str | None
    config: dict[str, Any]
    status: str


@dataclass(frozen=True)
class ModelConfig:
    id: uuid.UUID
    project_id: uuid.UUID | None
    name: str
    display_name: str
    model_type: str
    model_id: str
    params: dict[str, Any]
    context_window: int | None
    max_output_tokens: int | None
    embedding_dims: int | None
    cost_input: Decimal
    cost_output: Decimal
    capabilities: frozenset[str]
    timeout_s: int
    max_retries: int
    rpm_limit: int | None
    all_projects: bool
    enabled_projects: frozenset[uuid.UUID]
    status: str
    provider: Provider
    tokenizer: str | None = None

    def usable_by(self, project_id: uuid.UUID) -> bool:
        if self.status != "active" or self.provider.status != "active":
            return False
        if self.project_id is not None:
            return self.project_id == project_id
        return self.all_projects or project_id in self.enabled_projects

    def cost(self, tokens_in: int, tokens_out: int) -> Decimal:
        return (self.cost_input * tokens_in + self.cost_output * tokens_out) / Decimal(1_000_000)


@dataclass(frozen=True)
class Resolution:
    requested: str
    alias: str | None
    chain: tuple[ModelConfig, ...]


@dataclass
class Snapshot:
    providers: dict[uuid.UUID, Provider] = field(default_factory=dict)
    models: dict[uuid.UUID, ModelConfig] = field(default_factory=dict)
    platform_models: dict[str, ModelConfig] = field(default_factory=dict)
    project_models: dict[uuid.UUID, dict[str, ModelConfig]] = field(default_factory=dict)
    platform_aliases: dict[str, tuple[uuid.UUID, ...]] = field(default_factory=dict)
    project_aliases: dict[uuid.UUID, dict[str, tuple[uuid.UUID, ...]]] = field(default_factory=dict)
    project_configs: dict[uuid.UUID, GatewayProjectConfig] = field(default_factory=dict)


def effective_limits(
    platform: GatewayLimits, project: GatewayProjectConfig | None
) -> EffectiveLimits:
    """Project values apply within the platform ceilings; null inherits, 0 means unlimited."""

    def pick(ceiling: int, value: int | None) -> int:
        if value is None:
            return ceiling
        if ceiling == 0:
            return value
        return ceiling if value == 0 else min(value, ceiling)

    p = project
    return EffectiveLimits(
        tokens_per_day=pick(platform.tokens_per_day, p.tokens_per_day if p else None),
        requests_per_minute=pick(
            platform.requests_per_minute, p.requests_per_minute if p else None
        ),
        user_tokens_per_day=pick(
            platform.user_tokens_per_day, p.user_tokens_per_day if p else None
        ),
        user_requests_per_minute=pick(
            platform.user_requests_per_minute, p.user_requests_per_minute if p else None
        ),
        max_tokens_per_call=pick(
            platform.max_tokens_per_call, p.max_tokens_per_call if p else None
        ),
        max_tokens_policy=(
            p.max_tokens_policy if p and p.max_tokens_policy else platform.max_tokens_policy
        ),
        cache_enabled=bool(p and p.cache_enabled),
        cache_ttl_s=min(p.cache_ttl_s if p else 3600, platform.cache_ttl_max_s),
        cache_nonzero_temperature=bool(p and p.cache_nonzero_temperature),
    )


class Registry:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        envelope: Envelope,
        store: SettingsStore,
    ) -> None:
        self._sessions = sessions
        self._envelope = envelope
        self._store = store
        self._snapshot = Snapshot()
        self._lock = asyncio.Lock()
        self.loaded = False

    @property
    def snapshot(self) -> Snapshot:
        return self._snapshot

    async def on_notify(self, key: str) -> None:
        if key in (NOTIFY_KEY, "*"):
            await self.load()

    async def load(self) -> None:
        async with self._lock, admin_scope(self._sessions) as s:
            snap = Snapshot()
            providers: dict[uuid.UUID, Provider] = {}
            for row in (await s.execute(select(ModelProvider))).scalars():
                api_key = None
                if row.secret_id is not None:
                    try:
                        api_key = await secrets.get(s, self._envelope, row.secret_id)
                    except Exception as exc:  # wrong master key, tampered row
                        log.error("gateway.secret_unreadable", provider=row.name, error=str(exc))
                providers[row.id] = Provider(
                    row.id,
                    row.project_id,
                    row.name,
                    row.provider_type,
                    row.base_url,
                    api_key,
                    dict(row.config),
                    row.status,
                )
            enabled: dict[uuid.UUID, set[uuid.UUID]] = {}
            for e in (await s.execute(select(ModelEnablement))).scalars():
                enabled.setdefault(e.model_id, set()).add(e.project_id)
            for m in (await s.execute(select(Model))).scalars():
                cfg = ModelConfig(
                    id=m.id,
                    project_id=m.project_id,
                    name=m.name,
                    display_name=m.display_name,
                    model_type=m.model_type,
                    model_id=m.model_id,
                    params=dict(m.params),
                    context_window=m.context_window,
                    max_output_tokens=m.max_output_tokens,
                    embedding_dims=m.embedding_dims,
                    cost_input=Decimal(m.cost_input),
                    cost_output=Decimal(m.cost_output),
                    capabilities=frozenset(m.capabilities),
                    timeout_s=m.timeout_s,
                    max_retries=m.max_retries,
                    rpm_limit=m.rpm_limit,
                    tokenizer=m.tokenizer,
                    all_projects=m.all_projects,
                    enabled_projects=frozenset(enabled.get(m.id, set())),
                    status=m.status,
                    provider=providers[m.provider_id],
                )
                snap.models[m.id] = cfg
                if m.project_id is None:
                    snap.platform_models[m.name.lower()] = cfg
                else:
                    snap.project_models.setdefault(m.project_id, {})[m.name.lower()] = cfg
            for a in (await s.execute(select(ModelAlias))).scalars():
                chain = tuple(a.model_ids)
                if a.project_id is None:
                    snap.platform_aliases[a.name.lower()] = chain
                else:
                    snap.project_aliases.setdefault(a.project_id, {})[a.name.lower()] = chain
            for c in (await s.execute(select(GatewayProjectConfig))).scalars():
                s.expunge(c)
                snap.project_configs[c.project_id] = c
            snap.providers = providers
            self._snapshot = snap
            self.loaded = True
        log.info(
            "gateway.registry_loaded",
            providers=len(providers),
            models=len(snap.models),
            aliases=len(snap.platform_aliases) + sum(map(len, snap.project_aliases.values())),
        )

    # ---- resolution (F1.4, US-1.5.3)
    def resolve(self, project_id: uuid.UUID, requested: str, kinds: frozenset[str]) -> Resolution:
        """Alias (project, then platform) or model name (project, then platform)."""
        snap = self._snapshot
        key = requested.strip().lower()
        chain_ids = snap.project_aliases.get(project_id, {}).get(key)
        if chain_ids is None:
            chain_ids = snap.platform_aliases.get(key)
        if chain_ids is not None:
            models = [snap.models[i] for i in chain_ids if i in snap.models]
            usable = tuple(m for m in models if m.usable_by(project_id))
            if not usable:
                raise ProblemError(
                    403,
                    "model_not_enabled",
                    "No model behind this alias is enabled for the project",
                    f"Alias '{requested}' has no enabled model for this project",
                )
            self._check_kind(usable[0], requested, kinds)
            return Resolution(requested, key, usable)
        model = snap.project_models.get(project_id, {}).get(key) or snap.platform_models.get(key)
        if model is None:
            raise ProblemError(
                404, "model_not_found", "Unknown model or alias", f"Unknown model '{requested}'"
            )
        if not model.usable_by(project_id):
            raise ProblemError(
                403,
                "model_not_enabled",
                "Model not enabled for the project",
                f"Model '{requested}' is not enabled for this project",
            )
        self._check_kind(model, requested, kinds)
        return Resolution(requested, None, (model,))

    @staticmethod
    def _check_kind(model: ModelConfig, requested: str, kinds: frozenset[str]) -> None:
        if model.model_type not in kinds:
            raise ProblemError(
                400,
                "wrong_model_type",
                "Model type not valid for this endpoint",
                f"'{requested}' is a {model.model_type} model; expected {', '.join(sorted(kinds))}",
            )

    def available(self, project_id: uuid.UUID) -> tuple[list[ModelConfig], list[str]]:
        """Models usable by the project and alias names that resolve to at least one."""
        snap = self._snapshot
        models = [m for m in snap.models.values() if m.usable_by(project_id)]
        aliases = dict(snap.platform_aliases) | snap.project_aliases.get(project_id, {})
        names = [
            name
            for name, ids in aliases.items()
            if any(i in snap.models and snap.models[i].usable_by(project_id) for i in ids)
        ]
        return sorted(models, key=lambda m: m.name.lower()), sorted(names)

    def limits(self, project_id: uuid.UUID) -> EffectiveLimits:
        return effective_limits(
            self._store.get(GatewayLimits), self._snapshot.project_configs.get(project_id)
        )
