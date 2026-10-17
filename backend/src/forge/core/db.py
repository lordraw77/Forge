"""Database engine and tenant-scoped sessions (ADR-002).

The process connects with the owner credentials from `FORGE_DATABASE_URL` (the spec allows a
single URL in `.env`) and immediately switches to the `forge_app` role, which is subject to
row-level security. Migrations run as the owner.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from forge.core.settings import Settings


def create_engine(settings: Settings, *, app_role: bool = True) -> AsyncEngine:
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        pool_size=settings.db_pool_size,
        pool_pre_ping=True,
    )
    if app_role:
        role = settings.db_app_role
        if not role.replace("_", "").isalnum():
            raise ValueError(f"invalid database role name: {role!r}")

        @event.listens_for(engine.sync_engine, "connect")
        def _set_role(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute(f'SET ROLE "{role}"')
            cursor.close()

    return engine


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def project_scope(
    sessions: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID | None,
    user_id: uuid.UUID | None = None,
) -> AsyncIterator[AsyncSession]:
    """Open a transaction with `app.project_id` / `app.user_id` set for its duration only.

    `set_config(..., true)` is the function form of `SET LOCAL`: the values vanish at commit
    or rollback, so a pooled connection never carries another request's tenant.
    """
    async with sessions() as session, session.begin():
        await session.execute(
            text(
                "SELECT set_config('app.project_id', :project_id, true),"
                " set_config('app.user_id', :user_id, true)"
            ),
            {
                "project_id": str(project_id) if project_id else "",
                "user_id": str(user_id) if user_id else "",
            },
        )
        yield session


@asynccontextmanager
async def admin_scope(
    sessions: async_sessionmaker[AsyncSession], user_id: uuid.UUID | None = None
) -> AsyncIterator[AsyncSession]:
    """Cross-project access as `forge_admin` (BYPASSRLS), for named platform code paths only.

    ADR-002: keep callers few and reviewed (Super Admin console, purge, API key lookup).
    """
    async with sessions() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE forge_admin"))
        await session.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"),
            {"user_id": str(user_id) if user_id else ""},
        )
        yield session
