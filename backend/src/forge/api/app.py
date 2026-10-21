"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI, Request, Response

from forge import __version__
from forge.admin.service import SettingsStore, listen_dsn
from forge.agents.service import AgentStudio, RunEvents, RunStarter
from forge.analytics.service import Analytics
from forge.api import (
    health,
    routes_admin,
    routes_agents,
    routes_analytics,
    routes_approvals,
    routes_auth,
    routes_bundles,
    routes_data,
    routes_guardrails,
    routes_idp,
    routes_models,
    routes_notifications,
    routes_projects,
    routes_prompts,
    routes_rag,
    routes_sources,
    routes_tools,
    routes_usecases,
    routes_v1,
)
from forge.api.deps import Services
from forge.bundles.service import Bundles
from forge.core.context import SYSTEM
from forge.core.crypto import Envelope, derive_key
from forge.core.db import create_engine, session_factory
from forge.core.errors import install_problem_handlers
from forge.core.logging import configure_logging, get_logger
from forge.core.settings import Settings, get_settings
from forge.gateway.service import Gateway
from forge.iam.service import AuthService, ExternalAuth, IdentityProviders, TokenCodec
from forge.observability.service import (
    instrument_app,
    metrics,
    setup,
    start_metrics_server,
)
from forge.prompts.service import PromptStudio
from forge.rag.service import Rag, SearchClient
from forge.tenancy.service import GroupMappings, TenancyService
from forge.tools.service import ToolStudio
from forge.usecases.approvals import Approvals
from forge.usecases.files import UseCaseFiles
from forge.usecases.mail import Mailer
from forge.usecases.planner import Planner
from forge.usecases.runs import UseCaseRuns
from forge.usecases.service import UseCaseStudio

log = get_logger(__name__)


async def _directory_sync(app: FastAPI, external: ExternalAuth) -> None:
    """Every minute run the LDAP syncs that are due; one replica at a time (US-2.1.6)."""
    while True:
        await asyncio.sleep(60)
        try:
            if await app.state.redis.set("idp:sync-lock", "1", nx=True, ex=55):
                await external.sync_due(SYSTEM)
        except Exception as exc:  # never end the loop
            log.warning("idp.sync_loop_error", error=str(exc))


async def _initialise(app: FastAPI, svc: Services) -> None:
    """Load settings and bootstrap the admin; retried until the database is reachable."""
    delay = 1.0
    while True:
        try:
            await svc.store.start()
            await svc.gateway.start(svc.store)
            await svc.auth.ensure_bootstrap_admin(svc.settings)
            try:
                await svc.rag.start(app.state.search)
            except Exception as exc:  # S3 or OpenSearch not up yet: retried on first use
                log.warning("api.rag_start_deferred", error=str(exc))
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


async def _http_metrics(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """RED metrics per route template (never the raw path: no ids in labels)."""
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        template = getattr(route, "path", None) or "unmatched"
        metrics.HTTP_REQUESTS.labels(request.method, template, str(status_code)).inc()
        metrics.HTTP_DURATION.labels(request.method, template).observe(
            time.perf_counter() - started
        )


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
        setup("forge-api", settings)
        start_metrics_server(settings.metrics_port)
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
        tenancy = TenancyService(app.state.sessions)
        prompts = PromptStudio(app.state.sessions, gateway)
        rag = Rag.build(
            settings,
            app.state.sessions,
            app.state.search,
            gateway,
            prompts,
            tenancy.acl_terms,
            tenancy.acl_principals,
            envelope,
        )
        run_events = RunEvents(app.state.redis)
        tools = ToolStudio(app.state.sessions, envelope, settings)
        agents = AgentStudio(app.state.sessions, gateway, prompts, rag, RunStarter(settings), tools)
        usecases = UseCaseStudio(app.state.sessions, agents, tools, prompts, tenancy)
        usecase_files = UseCaseFiles(app.state.sessions, rag.store)
        usecase_runs = UseCaseRuns(
            app.state.sessions, usecases, RunStarter(settings), tools, usecase_files
        )
        auth = AuthService(
            app.state.sessions,
            store,
            envelope,
            TokenCodec(derive_key(master, "forge/jwt/v1")),
            app.state.redis,
        )
        providers = IdentityProviders(app.state.sessions, envelope, store)
        mappings = GroupMappings(app.state.sessions)
        auth.external = ExternalAuth(
            auth,
            app.state.sessions,
            store,
            providers,
            mappings,
            app.state.redis,
            settings.public_url,
        )
        svc = Services(
            settings=settings,
            store=store,
            auth=auth,
            providers=providers,
            mappings=mappings,
            tenancy=tenancy,
            gateway=gateway,
            prompts=prompts,
            rag=rag,
            agents=agents,
            tools=tools,
            usecases=usecases,
            usecase_runs=usecase_runs,
            approvals=Approvals(app.state.sessions, tenancy),
            guardrails=gateway.guardrails,
            analytics=Analytics(app.state.sessions, store),
            mailer=Mailer(settings, store, app.state.sessions, envelope),
            envelope=envelope,
            planner=Planner(gateway, usecases, usecase_files),
            run_events=run_events,
            bundles=Bundles(prompts, rag, gateway, tenancy, agents, usecases),
        )
        app.state.services = svc
        init = asyncio.create_task(_initialise(app, svc))
        idp_sync = asyncio.create_task(_directory_sync(app, auth.external))
        log.info("api.started", version=__version__)
        try:
            yield
        finally:
            init.cancel()
            idp_sync.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await init
            with contextlib.suppress(asyncio.CancelledError):
                await idp_sync
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
    instrument_app(app)
    app.middleware("http")(_http_metrics)
    app.include_router(health.router)
    app.include_router(routes_auth.router)
    app.include_router(routes_projects.router)
    app.include_router(routes_admin.router)
    app.include_router(routes_idp.public_router)
    app.include_router(routes_idp.admin_router)
    app.include_router(routes_models.admin_router)
    app.include_router(routes_models.project_router)
    app.include_router(routes_prompts.router)
    app.include_router(routes_data.router)
    app.include_router(routes_rag.router)
    app.include_router(routes_sources.router)
    app.include_router(routes_agents.router)
    app.include_router(routes_tools.router)
    app.include_router(routes_usecases.router)
    app.include_router(routes_approvals.router)
    app.include_router(routes_approvals.public_router)
    app.include_router(routes_guardrails.router)
    app.include_router(routes_notifications.router)
    app.include_router(routes_analytics.project_router)
    app.include_router(routes_analytics.admin_router)
    app.include_router(routes_analytics.me_router)
    app.include_router(routes_bundles.router)
    app.add_middleware(routes_data.BodyLimit)
    app.include_router(routes_v1.router)
    return app
