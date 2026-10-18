from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from forge import __version__
from forge.api.app import create_app
from forge.core.settings import Settings

# Ports with nothing listening: dependencies are unreachable but fail fast.
UNREACHABLE = Settings(
    database_url="postgresql+asyncpg://forge:x@127.0.0.1:1/forge",
    redis_url="redis://127.0.0.1:1/0",
    search_url="https://127.0.0.1:1",
    master_key="a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2U=",
)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(UNREACHABLE)) as c:
        yield c


def test_liveness_does_not_depend_on_dependencies(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readiness_reports_unreachable_dependencies(client: TestClient) -> None:
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json() == {
        "status": "fail",
        "checks": {"database": "fail", "redis": "fail", "search": "fail", "initialised": "fail"},
    }


def test_version(client: TestClient) -> None:
    assert client.get("/api/v1/version").json() == {"name": "forge", "version": __version__}


def test_errors_are_problem_json(client: TestClient) -> None:
    resp = client.get("/api/v1/does-not-exist")
    assert resp.status_code == 404
    assert resp.headers["content-type"] == "application/problem+json"
    body = resp.json()
    assert body["status"] == 404
    assert body["code"] == "http_404"
    assert body["instance"] == "/api/v1/does-not-exist"


def test_openapi_is_published_under_api_v1(client: TestClient) -> None:
    spec = client.get("/api/v1/openapi.json").json()
    assert spec["openapi"].startswith("3.1")
    assert "/readyz" in spec["paths"]
