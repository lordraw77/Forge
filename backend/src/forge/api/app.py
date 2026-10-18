"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI

from forge import __version__
from forge.admin.service import SettingsStore, listen_dsn
from forge.api import (
    health,
    routes_admin,
    routes_auth,
    routes_models,
    routes_projects,
    routes_prompts,
    routes_v1,
)
from forge.api.deps import Services
from forge.core.crypto import Envelope, derive_key
from forge.core.db import create_engine, session_factory
from forge.core.errors import install_problem_handlers
from forge.core.logging import configure_logging, get_logger
from forge.core.settings import Settings, get_settings
from forge.gateway.service import Gateway
from forge.iam.service import AuthService, TokenCodec
from forge.prompts.service import PromptStudio
from forge.rag.service import SearchClient
from forge.tenancy.service import TenancyService

log = get_logger(__name__)


async def _initialise(app: FastAPI, svc: Services) -> None:
    """Load settings and bootstrap the admin; retried until the database is reachable."""
    delay = 1.0
    while True:
        try:
            await svc.store.start()
            await svc.gateway.start(svc.store)
            await svc.auth.ensure_bootstrap_admin(svc.settings)
            app.state.initialised = True
            log.info("api.initialised")
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("api.initialise_retry", error=str(exc), retry_in_s=delay)
            await svc.store.stop()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)


def create_app(settings: Settings | None = None, *, gateway_backoff_s: float = 0.5) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level)
        if settings.master_key is None:
            raise RuntimeError("FORGE_MASTER_KEY is required (see .env.example)")
        master = settings.master_key.get_secret_value()
        app.state.settings = settings
        app.state.engine = create_engine(settings)
        app.state.sessions = session_factory(app.state.engine)
        app.state.redis = redis.from_url(settings.redis_url)
        app.state.search = SearchClient(settings)
        app.state.initialised = False
        store = SettingsStore(
            app.state.sessions, listen_dsn(settings.database_url.get_secret_value())
        )
        envelope = Envelope.from_master_key(master)
        gateway = Gateway.build(
            app.state.sessions,
            envelope,
            store,
            app.state.redis,
            backoff_base_s=gateway_backoff_s,
        )
        svc = Services(
            settings=settings,
            store=store,
            auth=AuthService(
                app.state.sessions,
                store,
                envelope,
                TokenCodec(derive_key(master, "forge/jwt/v1")),
                app.state.redis,
            ),
            tenancy=TenancyService(app.state.sessions),
            gateway=gateway,
            prompts=PromptStudio(app.state.sessions, gateway),
        )
        app.state.services = svc
        init = asyncio.create_task(_initialise(app, svc))
        log.info("api.started", version=__version__)
        try:
            yield
        finally:
            init.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await init
            await store.stop()
            await svc.gateway.stop()
            await app.state.redis.aclose()
            await app.state.search.aclose()
            await app.state.engine.dispose()
            log.info("api.stopped")

    app = FastAPI(
        title="Forge API",
        version=__version__,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
        # operationId = function name: readable methods in the generated client (ADR-012).
        generate_unique_id_function=lambda route: route.name,
    )
    install_problem_handlers(app)
    app.include_router(health.router)
    app.include_router(routes_auth.router)
    app.include_router(routes_projects.router)
    app.include_router(routes_admin.router)
    app.include_router(routes_models.admin_router)
    app.include_router(routes_models.project_router)
    app.include_router(routes_prompts.router)
    app.include_router(routes_v1.router)
    return app
