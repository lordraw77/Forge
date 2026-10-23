"""M1.4 RAG on upload against PostgreSQL, OpenSearch, SeaweedFS, Temporal and the mock provider
(US-1.14.1, US-1.14.2, US-1.15.1, US-1.15.2, US-1.16.1, US-1.16.2, US-1.17.1, US-1.17.2).

An ingestion worker runs in a thread on a task queue of its own; the API starts workflows on it.
Embeddings are the mock's hashed bag of words (`emb-64-bow`), so retrieval follows shared words.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from forge.api.app import create_app
from forge.core.settings import Settings
from tests.integration.test_gateway import alias, model, ok, project, provider, uid
from tests.integration.test_platform_core import MASTER_KEY, create_user, csrf, login, sql
from tests.support.mock_provider import MockProvider

pytestmark = pytest.mark.skipif(
    not os.environ.get("FORGE_TEST_S3_ENDPOINT"), reason="needs S3, OpenSearch and Temporal"
)

MANUAL = b"""# Forge Operations Manual

## Backups

The nightly backup job copies the PostgreSQL database to the object store at 02:00.
Restore uses the forge restore command with the backup identifier.

## Certificates

TLS certificates are renewed by the certificate manager thirty days before expiry.
"""
NOTES = b"""Office notes

The cafeteria serves lunch between noon and two o'clock.

Parking permits are requested from the facilities desk.
"""
SALARIES = b"""# Salary bands

Engineering salary bands are reviewed every March by the compensation committee.
"""


def make_settings(**extra: Any) -> Settings:
    run = uuid.uuid4().hex[:8]
    return Settings(
        database_url=os.environ["FORGE_TEST_DATABASE_URL"],
        redis_url=os.environ.get("FORGE_TEST_REDIS_URL", "redis://127.0.0.1:6379/15"),
        master_key=MASTER_KEY,
        admin_password="Bootstrap-Admin-Pass-1",
        db_pool_size=4,
        s3_endpoint=os.environ["FORGE_TEST_S3_ENDPOINT"],
        s3_access_key=os.environ["FORGE_TEST_S3_ACCESS_KEY"],
        s3_secret_key=os.environ["FORGE_TEST_S3_SECRET_KEY"],
        s3_bucket="forge-test",
        search_url=os.environ["FORGE_TEST_SEARCH_URL"],
        search_password=os.environ["FORGE_TEST_SEARCH_PASSWORD"],
        search_ca_file=os.environ["FORGE_TEST_SEARCH_CA"],
        search_index="forge-test-chunks",
        temporal_address=os.environ["FORGE_TEST_TEMPORAL_ADDRESS"],
        temporal_ingest_queue=f"ingest-test-{run}",
        **extra,
    )


@pytest.fixture(scope="module")
def settings() -> Settings:
    return make_settings()


@pytest.fixture(scope="module")
def worker(settings: Settings) -> Iterator[None]:
    """The real ingestion worker (forge.workers.runner) in a thread with its own event loop."""
    from temporalio.client import Client

    from forge.workers.runner import build_registrations, new_worker

    stop = threading.Event()
    started = threading.Event()
    errors: list[BaseException] = []

    async def main() -> None:
        reg = await build_registrations("ingest", settings)
        client = await Client.connect(settings.temporal_address)
        try:
            async with new_worker(client, settings.temporal_ingest_queue, reg):
                started.set()
                await asyncio.to_thread(stop.wait)
        finally:
            for close in reg.closers:
                await close()

    def run() -> None:
        try:
            asyncio.run(main())
        except BaseException as exc:
            errors.append(exc)
            started.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert started.wait(60), "ingest worker did not start"
    assert not errors, errors
    yield
    stop.set()
    thread.join(timeout=30)


@pytest.fixture(scope="module")
def mock() -> Iterator[str]:
    with MockProvider() as base_url:
        yield base_url


@pytest.fixture(scope="module")
def client(settings: Settings, worker: None) -> Iterator[TestClient]:
    app = create_app(settings, gateway_backoff_s=0.01)
    with TestClient(app) as c:
        deadline = time.time() + 30
        while not app.state.initialised and time.time() < deadline:
            time.sleep(0.1)
        assert app.state.initialised
        yield c


@pytest.fixture(scope="module")
def admin(client: TestClient) -> TestClient:
    return login(client, create_user(client, super_admin=True))


@pytest.fixture(scope="module")
def env(admin: TestClient, client: TestClient, mock: str) -> dict[str, Any]:
    proj = project(admin, client)
    prov = provider(admin, mock)
    ids = {}
    for key, model_id, extra in (
        ("chat", "r-echo", {}),
        (
            "embed",
            "emb-64-bow",
            {"model_type": "embedding", "embedding_dims": 64, "capabilities": []},
        ),
        ("rerank", "rr", {"model_type": "reranker", "capabilities": []}),
        ("judge", "r-judge", {}),
    ):
        ids[key] = model(admin, prov["id"], model_id, all_projects=True, **extra)
    owner = login(client, proj["owner"])
    base = f"/api/v1/projects/{proj['id']}"
    alias(owner, "default-chat", [ids["chat"]["id"]], url=base)
    alias(owner, "embedding", [ids["embed"]["id"]], url=base)
    alias(owner, "rerank", [ids["rerank"]["id"]], url=base)
    return {"project": proj, "owner": owner, "base": base, "models": ids}


# ---- helpers
def wait_for(predicate: Any, timeout: float = 90, what: str = "condition") -> Any:
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError(f"timed out waiting for {what}")


def collection(env: dict[str, Any], name: str | None = None) -> dict[str, Any]:
    c = env["owner"]
    body = {"name": name or f"Docs {uid()}", "description": "test"}
    return ok(c.post(f"{env['base']}/collections", headers=csrf(c), json=body), 201)  # type: ignore[no-any-return]


def upload(
    env: dict[str, Any],
    cid: str,
    files: list[tuple[str, bytes]],
    groups: list[str] | None = None,
    roles: list[str] | None = None,
) -> list[dict[str, Any]]:
    c = env["owner"]
    r = c.post(
        f"{env['base']}/collections/{cid}/documents",
        headers=csrf(c),
        files=[("files", (name, data)) for name, data in files],
        data={"group_ids": groups or [], "role_ids": roles or []},
    )
    return ok(r)  # type: ignore[no-any-return]


def documents(env: dict[str, Any], cid: str) -> dict[str, dict[str, Any]]:
    rows = ok(env["owner"].get(f"{env['base']}/collections/{cid}/documents"))
    return {d["name"]: d for d in rows}


def settled(env: dict[str, Any], cid: str) -> dict[str, dict[str, Any]] | None:
    docs = documents(env, cid)
    busy = any(d["status"] in ("pending", "processing") for d in docs.values())
    busy = busy or any(
        i["status"] in ("pending", "processing") for d in docs.values() for i in d["ingestions"]
    )
    return None if busy else docs


def pipeline(env: dict[str, Any], cid: str, **config: Any) -> dict[str, Any]:
    c = env["owner"]
    body = {
        "name": f"RAG {uid()}",
        "description": "ops answers",
        "config": {"collection_id": cid, "similarity_threshold": 0.2} | config,
    }
    return ok(c.post(f"{env['base']}/rag", headers=csrf(c), json=body), 201)  # type: ignore[no-any-return]


def kb_ready(env: dict[str, Any], cid: str) -> bool:
    kbs = ok(env["owner"].get(f"{env['base']}/collections/{cid}/knowledge-bases"))
    return bool(kbs) and all(k["status"] == "ready" for k in kbs)


def query(env: dict[str, Any], pid: str, text: str, client: TestClient | None = None) -> Any:
    c = client or env["owner"]
    return ok(c.post(f"{env['base']}/rag/{pid}/query", headers=csrf(c), json={"query": text}))


# ---- tests
@pytest.mark.ac("US-1.14.1/1")
@pytest.mark.ac("US-1.14.1/3")
@pytest.mark.ac("US-1.14.1/4")
@pytest.mark.ac("US-1.15.1/2")
@pytest.mark.ac("US-1.16.1/1")
@pytest.mark.ac("US-1.17.1/1")
def test_upload_ingest_and_answer_with_citations(env: dict[str, Any]) -> None:
    cid = collection(env)["id"]
    results = upload(
        env,
        cid,
        [
            ("manual.md", MANUAL),
            ("notes.txt", NOTES),
            ("tool.exe", b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\xff\xff" + bytes(range(256))),
            ("fake.pdf", b"just text pretending to be a pdf"),
        ],
    )
    status = {r["filename"]: r["status"] for r in results}
    assert status == {
        "manual.md": "accepted",
        "notes.txt": "accepted",
        "tool.exe": "rejected",
        "fake.pdf": "accepted",
    }
    # The type comes from the content, not the name: "fake.pdf" is plain text.
    assert next(r for r in results if r["filename"] == "fake.pdf")["document"]["kind"] == "txt"
    assert "binary" in next(r for r in results if r["filename"] == "tool.exe")["error"]

    p = pipeline(env, cid)
    assert p["version"]["kb_status"] in ("building", "ready")
    docs = wait_for(lambda: settled(env, cid), what="ingestion")
    assert {d["status"] for d in docs.values()} == {"ready"}
    ingestion = docs["manual.md"]["ingestions"][0]
    assert ingestion["chunks"] >= 1
    assert ingestion["embedding_model"] == env["models"]["embed"]["name"]
    wait_for(lambda: kb_ready(env, cid), what="knowledge base")

    run = query(env, p["id"], "How is the PostgreSQL backup restored?")
    assert run["status"] == "ok"
    assert run["citations"], run
    first = run["citations"][0]
    assert first["document_name"] == "manual.md"
    assert "Backups" in first["heading_path"]
    assert first["url"].endswith(f"/documents/{first['document_id']}/content")
    trace = ok(env["owner"].get(f"{env['base']}/rag/runs/{run['id']}"))
    retrieval = trace["retrieval"]
    assert retrieval["method"] == "hybrid"
    assert retrieval["vector_hits"] >= 1
    assert retrieval["bm25_hits"] >= 1
    assert retrieval["score"] == "reranker"
    assert retrieval["reranker"] == env["models"]["rerank"]["name"]
    assert "<context>" in trace["messages"][1]["content"]
    assert run["total_tokens"] > 0

    collections = ok(env["owner"].get(f"{env['base']}/collections"))
    mine = next(c for c in collections if c["id"] == cid)
    assert mine["status"] == "success"
    assert mine["documents"]["ready"] == 3


@pytest.mark.ac("US-1.17.1/2")
def test_no_relevant_chunk_says_so(env: dict[str, Any]) -> None:
    cid = collection(env)["id"]
    upload(env, cid, [("notes.txt", NOTES)])
    p = pipeline(env, cid, similarity_threshold=0.9)
    wait_for(lambda: settled(env, cid) and kb_ready(env, cid), what="ingestion")
    run = query(env, p["id"], "quantum chromodynamics lattice")
    assert run["status"] == "no_context"
    assert "No relevant information" in run["answer"]
    assert run["citations"] == []


@pytest.mark.ac("US-1.16.2/1")
def test_acl_by_group_and_role(env: dict[str, Any], client: TestClient) -> None:
    c, base = env["owner"], env["base"]
    owner_id = env["project"]["owner"]["id"]
    finance = ok(c.post(f"{base}/groups", headers=csrf(c), json={"name": f"Finance {uid()}"}), 201)
    roles = {r["role_type"]: r["id"] for r in ok(c.get(f"{base}/roles"))}
    principals = ok(c.get(f"{base}/acl-principals"))
    assert finance["id"] in {g["id"] for g in principals["groups"]}
    assert roles["viewer"] in {r["id"] for r in principals["roles"]}

    cid = collection(env)["id"]
    upload(env, cid, [("manual.md", MANUAL)])
    upload(env, cid, [("salaries.md", SALARIES)], groups=[finance["id"]])
    upload(env, cid, [("notes.txt", NOTES)], roles=[roles["viewer"]])
    p = pipeline(env, cid, search_method="vector", similarity_threshold=0.0, top_k=10)
    docs = wait_for(
        lambda: settled(env, cid) and kb_ready(env, cid) and settled(env, cid), what="ingestion"
    )
    acl = docs["salaries.md"]["acl"]
    assert acl == [{"kind": "group", "id": finance["id"], "name": finance["name"]}]

    def cited(question: str) -> set[str]:
        return {x["document_name"] for x in query(env, p["id"], question)["citations"]}

    salary_q, lunch_q = "When are engineering salary bands reviewed?", "When is lunch served?"
    # The Project Admin is neither in Finance nor a Viewer.
    assert "salaries.md" not in cited(salary_q)
    assert "notes.txt" not in cited(lunch_q)

    ok(
        c.patch(f"{base}/members/{owner_id}", headers=csrf(c), json={"group_ids": [finance["id"]]}),
        204,
    )
    assert "salaries.md" in cited(salary_q)
    member = next(m for m in ok(c.get(f"{base}/members")) if m["user_id"] == owner_id)
    assert member["group_ids"] == [finance["id"]]

    # What the retriever matches for this member: their groups and their role.
    terms = client.portal.call(  # type: ignore[attr-defined]
        client.app.state.services.tenancy.acl_terms,  # type: ignore[attr-defined]
        uuid.UUID(env["project"]["id"]),
        uuid.UUID(owner_id),
    )
    assert terms == sorted([f"group:{finance['id']}", f"role:{roles['project_admin']}"])

    # Role-based access: the notes move from the Viewer role to the Project Admin role,
    # without re-uploading.
    ok(
        c.patch(
            f"{base}/collections/{cid}/documents/{docs['notes.txt']['id']}",
            headers=csrf(c),
            json={"role_ids": [roles["project_admin"]]},
        )
    )
    wait_for(lambda: settled(env, cid), what="re-index after ACL change")
    assert "notes.txt" in cited(lunch_q)

    # Removing the group from the member hides the document again; a deleted group fails closed.
    ok(c.put(f"{base}/groups/{finance['id']}/members", headers=csrf(c), json={"user_ids": []}), 204)
    assert "salaries.md" not in cited(salary_q)
    ok(c.delete(f"{base}/groups/{finance['id']}", headers=csrf(c)), 204)
    after = documents(env, cid)["salaries.md"]["acl"]
    assert after == [{"kind": "group", "id": finance["id"], "name": None}]


@pytest.mark.ac("US-1.15.1/3")
def test_reupload_identical_is_unchanged_and_edits_reuse_embeddings(env: dict[str, Any]) -> None:
    cid = collection(env)["id"]
    upload(env, cid, [("manual.md", MANUAL)])
    pipeline(env, cid)
    wait_for(lambda: settled(env, cid) and kb_ready(env, cid), what="ingestion")
    assert upload(env, cid, [("manual.md", MANUAL)])[0]["status"] == "unchanged"

    edited = MANUAL + b"\n## Monitoring\n\nAlerts go to the on-call engineer through the pager.\n"
    assert upload(env, cid, [("manual.md", edited)])[0]["status"] == "accepted"
    docs = wait_for(lambda: settled(env, cid), what="re-ingestion")
    ingestion = docs["manual.md"]["ingestions"][0]
    assert ingestion["reused"] >= 1  # unchanged sections were not embedded again
    assert ingestion["embedded"] < ingestion["chunks"]


@pytest.mark.ac("US-1.14.1/4")
@pytest.mark.ac("US-1.14.2/1")
def test_failed_file_retry_delete_and_download(env: dict[str, Any], settings: Settings) -> None:
    c, base = env["owner"], env["base"]
    cid = collection(env)["id"]
    pdf = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
    upload(
        env,
        cid,
        [
            ("scan.pdf", pdf),
            (
                "page.html",
                b"<html><body><h1>T</h1><p>Hi</p><script>alert(1)</script></body></html>",
            ),
        ],
    )
    docs = wait_for(lambda: settled(env, cid), what="parsing")
    failed = docs["scan.pdf"]
    assert failed["status"] == "failed"
    assert failed["failed_at"]
    # The API image and this test environment have no Docling: a clear, non-retried error.
    assert "ingestion worker" in failed["error"] or "Docling" in failed["error"]
    retried = ok(
        c.post(f"{base}/collections/{cid}/documents/{failed['id']}/retry", headers=csrf(c))
    )
    assert retried["status"] == "pending"
    wait_for(lambda: documents(env, cid)["scan.pdf"]["status"] == "failed", what="retry")

    html = docs["page.html"]
    r = c.get(f"{base}/collections/{cid}/documents/{html['id']}/content")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")  # never rendered as HTML
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]
    assert b"<script>" in r.content

    ok(c.delete(f"{base}/collections/{cid}/documents/{html['id']}", headers=csrf(c)), 204)
    assert "page.html" not in documents(env, cid)
    rows = sql(c, settings, f"SELECT count(*) FROM rag_document WHERE id = '{html['id']}'")
    assert rows[0][0] == 0

    renamed = ok(c.patch(f"{base}/collections/{cid}", headers=csrf(c), json={"name": f"R {uid()}"}))
    assert renamed["name"].startswith("R ")
    ok(c.delete(f"{base}/collections/{cid}", headers=csrf(c)), 204)
    assert c.get(f"{base}/collections/{cid}").status_code == 404


@pytest.mark.ac("US-1.15.2/1")
@pytest.mark.ac("US-1.17.2/1")
def test_versions_new_index_library_and_prompt_reference(env: dict[str, Any]) -> None:
    c, base = env["owner"], env["base"]
    cid = collection(env)["id"]
    upload(env, cid, [("manual.md", MANUAL)])
    p = pipeline(env, cid)
    wait_for(lambda: settled(env, cid) and kb_ready(env, cid), what="ingestion")
    first_kb = p["version"]["kb_id"]

    # Same index parameters reuse the knowledge base; a new chunk size builds a new one.
    cfg = p["version"]["config"] | {"top_k": 3}
    same = ok(c.post(f"{base}/rag/{p['id']}/versions", headers=csrf(c), json={"config": cfg}), 201)
    assert same["version"]["kb_id"] == first_kb
    cfg2 = cfg | {"chunk_size": 200, "chunk_overlap": 20}
    v3 = ok(c.post(f"{base}/rag/{p['id']}/versions", headers=csrf(c), json={"config": cfg2}), 201)
    assert v3["version"]["kb_id"] != first_kb
    wait_for(lambda: kb_ready(env, cid), what="new knowledge base")
    assert query(env, p["id"], "certificate renewal")["status"] == "ok"

    published = ok(c.post(f"{base}/rag/{p['id']}/publish", headers=csrf(c), json={"version": 2}))
    assert published["status"] == "published"
    lib = ok(c.get(f"{base}/rag", params={"q": p["name"], "status": "published"}))
    assert [i["name"] for i in lib["items"]] == [p["name"]]
    copy = ok(
        c.post(f"{base}/rag/{p['id']}/duplicate", headers=csrf(c), json={"name": f"copy {uid()}"}),
        201,
    )
    assert copy["status"] == "draft"

    # A prompt used by a pipeline cannot be deleted (US-1.11.2 #3).
    prompt = ok(
        c.post(
            f"{base}/prompts",
            headers=csrf(c),
            json={
                "name": f"Answer style {uid()}",
                "description": "rag style",
                "version": {
                    "model": "default-chat",
                    "system_template": "Answer briefly. Cite [n].",
                },
            },
        ),
        201,
    )
    cfg4 = cfg | {"prompt_id": prompt["id"]}
    ok(c.post(f"{base}/rag/{copy['id']}/versions", headers=csrf(c), json={"config": cfg4}), 201)
    blocked = c.delete(f"{base}/prompts/{prompt['id']}", headers=csrf(c))
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "prompt_in_use"
    assert blocked.json()["detail"][0]["name"] == copy["name"]
    run = query(env, copy["id"], "nightly backup")
    trace = ok(c.get(f"{base}/rag/runs/{run['id']}"))
    assert trace["messages"][0]["content"] == "Answer briefly. Cite [n]."

    in_use = c.delete(f"{base}/collections/{cid}", headers=csrf(c))
    assert in_use.json()["code"] == "collection_in_use"


def test_isolation_between_projects(
    env: dict[str, Any], admin: TestClient, client: TestClient, settings: Settings
) -> None:
    cid = collection(env)["id"]
    upload(env, cid, [("manual.md", MANUAL)])
    p = pipeline(env, cid)
    wait_for(lambda: settled(env, cid) and kb_ready(env, cid), what="ingestion")
    other = project(admin, client)
    stranger = login(client, other["owner"])
    other_base = f"/api/v1/projects/{other['id']}"
    assert stranger.get(f"{other_base}/collections/{cid}").status_code == 404
    assert stranger.get(f"{other_base}/rag/{p['id']}").status_code == 404
    # RLS: without the project set, no chunk is visible to the application role.
    rows = sql(client, settings, "SELECT count(*) FROM rag_chunk", app_role=True)
    assert rows[0][0] == 0

    from forge.rag.search import SearchClient

    async def bm25_as_other() -> list[Any]:
        search = SearchClient(settings)
        try:
            kb = uuid.UUID(p["version"]["kb_id"])
            return await search.bm25("backup restore", uuid.UUID(other["id"]), [kb], [])
        finally:
            await search.aclose()

    assert client.portal.call(bm25_as_other) == []  # type: ignore[attr-defined]


def test_groups_are_validated(env: dict[str, Any], client: TestClient) -> None:
    c, base = env["owner"], env["base"]
    owner_id = env["project"]["owner"]["id"]
    g = ok(c.post(f"{base}/groups", headers=csrf(c), json={"name": f"Ops {uid()}"}), 201)
    dup = c.post(f"{base}/groups", headers=csrf(c), json={"name": g["name"].upper()})
    assert dup.json()["code"] == "name_taken"
    unknown = c.patch(
        f"{base}/members/{owner_id}", headers=csrf(c), json={"group_ids": [str(uuid.uuid4())]}
    )
    assert unknown.json()["code"] == "unknown_group"
    outsider = create_user(client)
    bad = c.put(
        f"{base}/groups/{g['id']}/members", headers=csrf(c), json={"user_ids": [outsider["id"]]}
    )
    assert bad.json()["code"] == "not_a_member"
    cid = collection(env)["id"]
    r = c.post(
        f"{base}/collections/{cid}/documents",
        headers=csrf(c),
        files=[("files", ("a.txt", b"hello world"))],
        data={"group_ids": [str(uuid.uuid4())]},
    )
    assert r.json()["code"] == "unknown_principal"
    renamed = ok(
        c.put(f"{base}/groups/{g['id']}", headers=csrf(c), json={"name": "Renamed " + uid()})
    )
    assert renamed["name"].startswith("Renamed")


@pytest.mark.ac("US-1.15.1/4")
def test_index_activity_retry_creates_no_duplicates(
    env: dict[str, Any], client: TestClient, settings: Settings
) -> None:
    """A worker killed mid-activity makes Temporal run the activity again: same result."""
    from sqlalchemy import func, select
    from temporalio.testing import ActivityEnvironment

    from forge.core.db import project_scope
    from forge.rag.models import Chunk
    from forge.workers.runner import build_registrations

    cid = collection(env)["id"]
    upload(env, cid, [("manual.md", MANUAL)])
    p = pipeline(env, cid)
    docs = wait_for(
        lambda: settled(env, cid) and kb_ready(env, cid) and settled(env, cid), what="ingestion"
    )
    did, kid, pid = docs["manual.md"]["id"], p["version"]["kb_id"], env["project"]["id"]
    app = client.app  # type: ignore[attr-defined]

    async def rerun() -> tuple[list[dict[str, int]], int, int]:
        reg = await build_registrations("ingest", settings)
        try:
            index = next(
                a for a in reg.activities if getattr(a, "__name__", "") == "index_document"
            )
            results = [await ActivityEnvironment().run(index, pid, did, kid) for _ in range(2)]
            async with project_scope(app.state.sessions, uuid.UUID(pid)) as s:
                rows = (
                    await s.execute(
                        select(func.count())
                        .select_from(Chunk)
                        .where(Chunk.document_id == uuid.UUID(did), Chunk.kb_id == uuid.UUID(kid))
                    )
                ).scalar_one()
            resp = await app.state.search.request(
                "POST",
                f"/{settings.search_index}/_count",
                params={"routing": pid},
                json={"query": {"bool": {"filter": [{"term": {"document_id": did}}]}}},
            )
            return results, rows, resp.json()["count"]
        finally:
            for close in reg.closers:
                await close()

    results, rows, indexed = client.portal.call(rerun)  # type: ignore[attr-defined]
    chunks = docs["manual.md"]["ingestions"][0]["chunks"]
    assert [r["chunks"] for r in results] == [chunks, chunks]
    assert all(r["embedded"] == 0 for r in results)  # every embedding is reused
    assert rows == chunks
    assert indexed == chunks


@pytest.mark.ac("US-1.15.1/2")
def test_registered_tokenizer_is_recorded_and_used_for_chunking(
    env: dict[str, Any], admin: TestClient
) -> None:
    """The embedding model's tokenizer (registry field) is copied to the knowledge base."""
    models = ok(admin.get("/api/v1/admin/models"))
    emb = next(m for m in models if m["id"] == env["models"]["embed"]["id"])
    body = {
        "name": emb["name"],
        "display_name": emb["display_name"],
        "provider_id": emb["provider_id"],
        "model_type": "embedding",
        "model_id": emb["model_id"],
        "embedding_dims": 64,
        "tokenizer": "tiktoken:o200k_base",
        "all_projects": True,
    }
    updated = ok(admin.put(f"/api/v1/admin/models/{emb['id']}", headers=csrf(admin), json=body))
    assert updated["tokenizer"] == "tiktoken:o200k_base"
    bad = admin.put(
        f"/api/v1/admin/models/{emb['id']}",
        headers=csrf(admin),
        json=body | {"tokenizer": "bad value"},
    )
    assert bad.status_code == 422
    try:
        cid = collection(env)["id"]
        upload(env, cid, [("manual.md", MANUAL)])
        pipeline(env, cid, chunk_size=150, chunk_overlap=20)
        docs = wait_for(
            lambda: settled(env, cid) and kb_ready(env, cid) and settled(env, cid), what="ingestion"
        )
        assert docs["manual.md"]["status"] == "ready"
        kbs = ok(env["owner"].get(f"{env['base']}/collections/{cid}/knowledge-bases"))
        assert len(kbs) == 1
    finally:
        ok(
            admin.put(
                f"/api/v1/admin/models/{emb['id']}",
                headers=csrf(admin),
                json=body | {"tokenizer": None},
            )
        )


@pytest.mark.ac("US-3.2.4/1")
@pytest.mark.ac("US-3.2.4/2")
def test_rag_evaluation_measures_retrieval_and_answers(env: dict[str, Any]) -> None:
    c = env["owner"]
    cid = collection(env)["id"]
    upload(env, cid, [("manual.md", MANUAL), ("notes.txt", NOTES)])
    p = pipeline(env, cid)
    wait_for(lambda: settled(env, cid) and kb_ready(env, cid), what="ingestion")
    judge = env["models"]["judge"]["name"]
    evals = f"{env['base']}/evals"
    cases = [
        {
            "input": {"query": "How is the PostgreSQL backup restored?"},
            "expected": "forge restore",
            "expected_sources": ["manual.md"],
        },
        {
            "input": {"query": "How is the PostgreSQL backup restored?"},
            "expected_sources": ["salaries.md"],  # not in the collection: recall 0
        },
    ]
    d = ok(
        c.post(
            f"{evals}/datasets",
            headers=csrf(c),
            json={"name": f"RAG {uid()}", "kind": "rag", "cases": cases},
        ),
        201,
    )
    body = {
        "dataset_id": d["id"],
        "target_id": p["id"],
        "evaluators": [
            {"type": "recall_at_k", "k": 3},
            {"type": "mrr"},
            {"type": "faithfulness", "model": judge},
            {"type": "relevance", "model": judge},
            {"type": "correctness", "model": judge},
        ],
    }
    run = ok(c.post(f"{evals}/runs", headers=csrf(c), json=body), 202)
    done = wait_for(
        lambda: (lambda r: r if r["status"] not in ("queued", "running") else None)(
            ok(c.get(f"{evals}/runs/{run['id']}"))
        ),
        what="evaluation",
    )
    assert done["status"] == "done"
    first, second = done["results"]
    assert first["scores"]["recall_at_k"]["score"] == 1.0
    assert first["scores"]["mrr"]["score"] == 1.0
    assert first["scores"]["faithfulness"]["score"] == 0.9
    assert first["scores"]["correctness"]["score"] == 0.9
    assert first["passed"]
    assert second["scores"]["recall_at_k"]["score"] == 0.0
    assert second["scores"]["correctness"]["score"] is None  # no expected answer: skipped
    assert not second["passed"]
    s = done["summary"]
    assert s["pass_rate"] == 0.5
    assert s["evaluators"]["recall_at_k"]["mean"] == 0.5


@pytest.mark.ac("US-3.7.4/1")
def test_reindex_rebuilds_the_bm25_index_from_postgres(
    env: dict[str, Any], client: TestClient
) -> None:
    from forge.rag.service import reindex

    cid = collection(env)["id"]
    upload(env, cid, [("manual.md", MANUAL)])
    p = pipeline(env, cid)
    wait_for(lambda: settled(env, cid) and kb_ready(env, cid), what="ingestion")
    question = "How is the PostgreSQL backup restored?"

    def bm25_hits() -> int:
        trace = ok(
            env["owner"].get(f"{env['base']}/rag/runs/{query(env, p['id'], question)['id']}")
        )
        return int(trace["retrieval"]["bm25_hits"])

    assert bm25_hits() >= 1
    pid = uuid.UUID(env["project"]["id"])
    kbs = [
        uuid.UUID(k["id"])
        for k in ok(env["owner"].get(f"{env['base']}/collections/{cid}/knowledge-bases"))
    ]
    app = client.app
    # An index lost (or left behind a restored database): empty, then rebuilt from PostgreSQL.
    client.portal.call(app.state.search.delete_where, pid, "kb_id", kbs)  # type: ignore[union-attr]
    assert bm25_hits() == 0
    done = client.portal.call(reindex, app.state.sessions, app.state.search, pid)  # type: ignore[union-attr]
    assert done[pid] >= 1
    assert bm25_hits() >= 1
