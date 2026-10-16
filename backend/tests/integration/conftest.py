from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from forge.core.db import create_engine
from forge.core.settings import Settings

TEST_DB_URL = os.environ.get("FORGE_TEST_DATABASE_URL")

pytestmark = pytest.mark.integration


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/integration" in str(item.path):
            item.add_marker(pytest.mark.integration)
            if not TEST_DB_URL:
                item.add_marker(pytest.mark.skip(reason="FORGE_TEST_DATABASE_URL not set"))


@pytest.fixture
def settings() -> Settings:
    assert TEST_DB_URL
    return Settings(database_url=TEST_DB_URL, db_pool_size=2)


@pytest.fixture
async def owner_engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(settings, app_role=False)
    yield engine
    await engine.dispose()


@pytest.fixture
async def app_engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(settings)
    yield engine
    await engine.dispose()
