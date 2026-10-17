"""Public interface of the gateway module (ADR-001, ADR-005)."""

from __future__ import annotations

from dataclasses import dataclass, field

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import SettingsStore
from forge.core.crypto import Envelope
from forge.gateway.adapter import LiteLLMAdapter, ProviderAdapter, UpstreamError, warm_up
from forge.gateway.catalog import Catalog
from forge.gateway.limits import Limiter, ResponseCache
from forge.gateway.pipeline import Caller, GatewayResponse, GatewayService
from forge.gateway.registry import STANDARD_ALIASES, ModelConfig, Registry
from forge.gateway.schemas import (
    AliasIn,
    AliasOut,
    ModelIn,
    ModelOut,
    ProjectGatewayConfigIn,
    ProjectGatewayConfigOut,
    ProviderIn,
    ProviderOut,
    TestConnectionIn,
    TestResult,
    UsageOut,
)


@dataclass
class Gateway:
    registry: Registry
    catalog: Catalog
    service: GatewayService
    _subscribed: bool = field(default=False, init=False)

    @classmethod
    def build(
        cls,
        sessions: async_sessionmaker[AsyncSession],
        envelope: Envelope,
        store: SettingsStore,
        client: redis.Redis,
        adapter: ProviderAdapter | None = None,
        *,
        backoff_base_s: float = 0.5,
    ) -> Gateway:
        registry = Registry(sessions, envelope, store)
        return cls(
            registry=registry,
            catalog=Catalog(sessions, envelope, registry, store),
            service=GatewayService(
                sessions,
                registry,
                adapter or LiteLLMAdapter(),
                Limiter(client),
                ResponseCache(client),
                backoff_base_s=backoff_base_s,
            ),
        )

    async def start(self, store: SettingsStore, *, warm: bool = True) -> None:
        """Load the registry and follow changes; import LiteLLM off the event loop."""
        if warm:
            await warm_up()
        await self.registry.load()
        if not self._subscribed:
            store.subscribe(self.registry.on_notify)
            self._subscribed = True

    async def stop(self) -> None:
        await self.service.drain()


__all__ = [
    "STANDARD_ALIASES",
    "AliasIn",
    "AliasOut",
    "Caller",
    "Gateway",
    "GatewayResponse",
    "ModelConfig",
    "ModelIn",
    "ModelOut",
    "ProjectGatewayConfigIn",
    "ProjectGatewayConfigOut",
    "ProviderAdapter",
    "ProviderIn",
    "ProviderOut",
    "TestConnectionIn",
    "TestResult",
    "UpstreamError",
    "UsageOut",
]
