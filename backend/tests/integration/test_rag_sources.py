"""M2.6b scheduled RAG sources: filesystem, REST and web crawler through the real ingestion
worker (sync workflow, incremental re-sync, propagated deletion) (E2.13)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from forge.core.settings import Settings
from tests.integration.test_gateway import ok
from tests.integration.test_platform_core import csrf
from tests.integration.test_rag import (
    admin,
    client,
    collection,
    documents,
    env,
    make_settings,
    mock,
    wait_for,
    worker,
)
from tests.support import mock_http
from tests.support.mock_http import MockHttp

__all__ = ["admin", "client", "env", "mock", "worker"]


@pytest.fixture(scope="module")
def share(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("share")


@pytest.fixture(scope="module")
def settings(share: Path) -> Settings:
    return make_settings(source_roots=f"{share}=*", tools_allow_private_hosts=True)


@pytest.fixture(scope="module")
def http() -> Iterator[str]:
    with MockHttp() as base:
        yield base


def create(env: dict[str, Any], **body: Any) -> dict[str, Any]:
    c = env["owner"]
    return ok(c.post(f"{env['base']}/sources", headers=csrf(c), json=body), 201)  # type: ignore[no-any-return]


def sync(env: dict[str, Any], sid: str, *, mass: bool = False) -> dict[str, Any]:
    """Start a sync and wait for the new run to end; returns that run."""
    c = env["owner"]
    before = {r["id"] for r in ok(c.get(f"{env['base']}/sources/{sid}/runs"))}
    ok(
        c.post(
            f"{env['base']}/sources/{sid}/sync", headers=csrf(c), json={"allow_mass_delete": mass}
        ),
        202,
    )

    def done() -> dict[str, Any] | None:
        runs = ok(c.get(f"{env['base']}/sources/{sid}/runs"))
        new = [r for r in runs if r["id"] not in before and r["status"] != "running"]
        return new[0] if new else None

    return wait_for(done, 60, "sync run")  # type: ignore[no-any-return]


def test_filesystem_source_incremental_and_deletion(env: dict[str, Any], share: Path) -> None:
    folder = share / f"docs-{uuid.uuid4().hex[:6]}"
    (folder / "sub").mkdir(parents=True)
    (folder / "a.md").write_text("# Alpha\n\nThe alpha policy.")
    (folder / "sub" / "b.txt").write_text("Bravo notes about bravo.")
    (folder / "c.md").write_text("# Charlie\n\nCharlie handbook.")
    col = collection(env)
    src = create(
        env,
        name=f"fs {uuid.uuid4().hex[:6]}",
        collection_id=col["id"],
        type="filesystem",
        config={"path": str(folder)},
    )
    first = sync(env, src["id"])
    assert (first["status"], first["added"], first["failed"]) == ("success", 3, 0)
    assert set(documents(env, col["id"])) == {"a.md", "b.txt", "c.md"}

    again = sync(env, src["id"])
    assert (again["added"], again["updated"], again["unchanged"]) == (0, 0, 3)

    (folder / "a.md").write_text("# Alpha\n\nThe alpha policy changed.")
    changed = sync(env, src["id"])
    assert (changed["updated"], changed["unchanged"]) == (1, 2)

    (folder / "c.md").unlink()
    gone = sync(env, src["id"])
    assert gone["deleted"] == 1
    assert set(documents(env, col["id"])) == {"a.md", "b.txt"}

    # Everything vanishing at once looks like an unmounted share: nothing is deleted.
    (folder / "a.md").unlink()
    (folder / "sub" / "b.txt").unlink()
    blocked = sync(env, src["id"])
    assert blocked["deleted"] == 0
    assert blocked["status"] == "partial"
    assert "nothing was deleted" in blocked["message"]
    assert set(documents(env, col["id"])) == {"a.md", "b.txt"}
    confirmed = sync(env, src["id"], mass=True)
    assert confirmed["deleted"] == 2
    assert documents(env, col["id"]) == {}


def test_filesystem_path_outside_the_enabled_roots_is_refused(env: dict[str, Any]) -> None:
    c = env["owner"]
    r = c.post(
        f"{env['base']}/sources",
        headers=csrf(c),
        json={
            "name": "escape",
            "collection_id": collection(env)["id"],
            "type": "filesystem",
            "config": {"path": "/etc"},
        },
    )
    assert r.status_code == 422
    assert r.json()["code"] == "path_not_allowed"


def test_rest_source_with_credentials_and_deletion(env: dict[str, Any], http: str) -> None:
    c = env["owner"]
    ok(c.put(f"{env['base']}/tool-policy", headers=csrf(c), json={"allowed_hosts": ["127.0.0.1"]}))
    mock_http.KB_ITEMS[:] = [
        {"id": "1", "title": "VPN", "body": "Connect with the corporate client."},
        {"id": "2", "title": "Printers", "body": "Use the follow-me queue."},
    ]
    col = collection(env)
    src = create(
        env,
        name=f"rest {uuid.uuid4().hex[:6]}",
        collection_id=col["id"],
        type="rest",
        config={
            "url": f"{http}/kb/items",
            "items_path": "data",
            "id_field": "id",
            "title_field": "title",
            "content_fields": ["body"],
        },
        credentials={"bearer_token": "s3cret-token"},
    )
    assert src["has_credentials"] is True
    assert "s3cret" not in str(src)
    assert sync(env, src["id"])["added"] == 2
    mock_http.KB_ITEMS[:] = [mock_http.KB_ITEMS[0] | {"body": "Use the new VPN client."}]
    second = sync(env, src["id"], mass=True)
    assert (second["updated"], second["deleted"]) == (1, 1)
    assert len(documents(env, col["id"])) == 1


def test_url_source_crawls_linked_pages(env: dict[str, Any], http: str) -> None:
    c = env["owner"]
    ok(c.put(f"{env['base']}/tool-policy", headers=csrf(c), json={"allowed_hosts": ["127.0.0.1"]}))
    mock_http.SITE.update(
        {
            "index": '<html><body><h1>Home</h1><a href="/site/one">one</a>'
            '<a href="https://elsewhere.invalid/x">out</a></body></html>',
            "one": "<html><body><h1>Page one</h1><p>Rotate the keys yearly.</p></body></html>",
        }
    )
    col = collection(env)
    src = create(
        env,
        name=f"web {uuid.uuid4().hex[:6]}",
        collection_id=col["id"],
        type="url",
        config={"start_urls": [f"{http}/site/index"], "max_depth": 1},
        interval_minutes=60,
    )
    assert src["next_run_at"] is not None  # scheduled sources are due at once

    def finished() -> dict[str, Any] | None:
        runs = ok(c.get(f"{env['base']}/sources/{src['id']}/runs"))
        return runs[0] if runs and runs[0]["status"] != "running" else None

    run = wait_for(finished, 90, "scheduled run")
    assert run["trigger"] == "schedule"
    assert (run["status"], run["added"]) == ("success", 2)


def test_source_crud_and_credentials_validation(env: dict[str, Any]) -> None:
    c = env["owner"]
    col = collection(env)
    r = c.post(
        f"{env['base']}/sources",
        headers=csrf(c),
        json={"name": "bad", "collection_id": col["id"], "type": "s3", "config": {"bucket": "x"}},
    )
    assert r.status_code == 422  # bucket name too short
    r = c.post(
        f"{env['base']}/sources",
        headers=csrf(c),
        json={
            "name": "bad2",
            "collection_id": col["id"],
            "type": "git",
            "config": {"repo_url": "https://example.com/r.git"},
            "credentials": {"api_key": "x"},
        },
    )
    assert r.status_code == 422
    assert r.json()["code"] == "invalid_credentials"
    src = create(
        env,
        name=f"git {uuid.uuid4().hex[:6]}",
        collection_id=col["id"],
        type="git",
        config={"repo_url": "https://example.com/r.git"},
        credentials={"token": "t"},
    )
    upd = ok(
        c.patch(
            f"{env['base']}/sources/{src['id']}",
            headers=csrf(c),
            json={"credentials": {}, "interval_minutes": 15},
        )
    )
    assert upd["has_credentials"] is False
    assert upd["interval_minutes"] == 15
    ok(c.delete(f"{env['base']}/sources/{src['id']}", headers=csrf(c)), 204)
    assert c.get(f"{env['base']}/sources/{src['id']}").status_code == 404


def test_sql_source_reads_rows_as_documents(env: dict[str, Any]) -> None:
    from tests.integration.test_tools import datasource

    ds = datasource(env)
    c = env["owner"]
    ok(c.put(f"{env['base']}/tool-policy", headers=csrf(c), json={"allowed_hosts": ["127.0.0.1"]}))
    col = collection(env)
    src = create(
        env,
        name=f"sql {uuid.uuid4().hex[:6]}",
        collection_id=col["id"],
        type="sql",
        config={
            "datasource": ds["name"],
            "query": "SELECT 'r' || n AS id, 'Row ' || n AS title, 'Body of row ' || n AS body "
            "FROM generate_series(1, 3) AS n",
            "id_column": "id",
            "title_column": "title",
            "content_columns": ["body"],
        },
    )
    run = sync(env, src["id"])
    assert (run["status"], run["added"]) == ("success", 3)
    assert sync(env, src["id"])["unchanged"] == 3


def test_s3_source_lists_a_bucket(env: dict[str, Any], settings: Settings) -> None:
    import boto3

    s3 = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key.get_secret_value(),  # type: ignore[union-attr]
        aws_secret_access_key=settings.s3_secret_key.get_secret_value(),  # type: ignore[union-attr]
        region_name="us-east-1",
    )
    prefix = f"src-{uuid.uuid4().hex[:6]}/"
    s3.put_object(Bucket="forge-test", Key=prefix + "guide/one.md", Body=b"# One\n\nFirst guide.")
    s3.put_object(Bucket="forge-test", Key=prefix + "two.txt", Body=b"Second file text.")
    c = env["owner"]
    ok(c.put(f"{env['base']}/tool-policy", headers=csrf(c), json={"allowed_hosts": ["127.0.0.1"]}))
    col = collection(env)
    src = create(
        env,
        name=f"s3 {uuid.uuid4().hex[:6]}",
        collection_id=col["id"],
        type="s3",
        config={"endpoint": settings.s3_endpoint, "bucket": "forge-test", "prefix": prefix},
        credentials={
            "access_key": settings.s3_access_key.get_secret_value(),  # type: ignore[union-attr]
            "secret_key": settings.s3_secret_key.get_secret_value(),  # type: ignore[union-attr]
        },
    )
    run = sync(env, src["id"])
    assert (run["status"], run["added"]) == ("success", 2)
    assert set(documents(env, col["id"])) == {"one.md", "two.txt"}
    s3.delete_object(Bucket="forge-test", Key=prefix + "two.txt")
    s3.put_object(Bucket="forge-test", Key=prefix + "three.txt", Body=b"Third.")
    again = sync(env, src["id"])
    assert (again["added"], again["deleted"], again["unchanged"]) == (1, 1, 1)
