"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI

from forge import __version__
from forge.api import health
from forge.core.db import create_engine, session_factory
from forge.core.errors import install_problem_handlers
from forge.core.logging import configure_logging, get_logger
from forge.core.settings import Settings, get_settings

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level)
        app.state.settings = settings
        app.state.engine = create_engine(settings)
        app.state.sessions = session_factory(app.state.engine)
        app.state.redis = redis.from_url(settings.redis_url)
        log.info("api.started", version=__version__)
        try:
            yield
        finally:
            await app.state.redis.aclose()
            await app.state.engine.dispose()
            log.info("api.stopped")

    app = FastAPI(
        title="Forge API",
        version=__version__,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    install_problem_handlers(app)
    app.include_router(health.router)
    return app
