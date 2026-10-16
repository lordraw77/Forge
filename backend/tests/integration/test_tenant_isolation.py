"""Tenant isolation suite (ADR-002, US-1.1.1).

`test_every_project_table_has_rls` is the CI guard: it fails as soon as a migration adds a
table with a `project_id` column but without forced RLS and a policy.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from forge.core.db import project_scope, session_factory
from forge.core.rls import enable_project_rls


async def test_app_role_is_bound_by_rls(app_engine: AsyncEngine) -> None:
    async with app_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT current_user, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
    assert row.current_user == "forge_app"
    assert row.rolbypassrls is False


@pytest.mark.ac("US-1.1.1/3")
async def test_every_project_table_has_rls(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as conn:
        offenders = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT c.relname
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'project_id'
                    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
                      AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity
                           OR NOT EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid))
                    """
                    )
                )
            )
            .scalars()
            .all()
        )
    assert offenders == [], f"tables with project_id but without forced RLS: {offenders}"


@pytest.fixture
async def scoped_table(owner_engine: AsyncEngine) -> AsyncIterator[str]:
    name = f"rls_probe_{uuid.uuid4().hex[:8]}"
    async with owner_engine.begin() as conn:
        await conn.execute(text(f"CREATE TABLE {name} (id serial PRIMARY KEY, project_id uuid)"))
        for stmt in enable_project_rls(name, nullable_project=True).split(";"):
            if stmt.strip():
                await conn.execute(text(stmt))
    yield name
    async with owner_engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE {name}"))


@pytest.mark.ac("US-1.1.1/1")
async def test_rows_are_visible_only_inside_their_project(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, scoped_table: str
) -> None:
    project_a, project_b = uuid.uuid4(), uuid.uuid4()
    async with owner_engine.begin() as conn:
        await conn.execute(text("SET LOCAL ROLE forge_admin"))
        await conn.execute(
            text(f"INSERT INTO {scoped_table} (project_id) VALUES (:a), (:b), (NULL)"),
            {"a": project_a, "b": project_b},
        )

    sessions = session_factory(app_engine)
    count = text(f"SELECT project_id FROM {scoped_table}")

    async with project_scope(sessions, None) as s:
        assert (await s.execute(count)).scalars().all() == [None]

    async with project_scope(sessions, project_a) as s:
        assert set((await s.execute(count)).scalars().all()) == {project_a, None}

    # The setting is transaction-local: a new transaction on a pooled connection sees nothing.
    async with sessions() as s:
        assert (await s.execute(count)).scalars().all() == [None]


async def test_cannot_write_into_another_project(
    app_engine: AsyncEngine, scoped_table: str
) -> None:
    sessions = session_factory(app_engine)
    with pytest.raises(Exception, match="row-level security"):
        async with project_scope(sessions, uuid.uuid4()) as s:
            await s.execute(
                text(f"INSERT INTO {scoped_table} (project_id) VALUES (:p)"), {"p": uuid.uuid4()}
            )
