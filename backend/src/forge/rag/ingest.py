"""Ingestion on Temporal (E1.15, ADR-004): parse → chunk → enrich → embed (batch 32) → index.

Workflows are thin and deterministic; every side effect is an activity. Activities are
idempotent, so a worker killed mid-run is simply retried (US-1.15.1 #4):

- chunk ids are deterministic (knowledge base, document, ordinal, content hash), PostgreSQL
  rows are upserted and OpenSearch documents use the same `_id`;
- stale chunks of the document are deleted in the same transaction as the upsert;
- embeddings of unchanged chunks are reused (SHA-256 of the embedded text, US-1.15.1 #3).
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from sqlalchemy import Table, delete, func, select
    from sqlalchemy.dialects.postgresql import distinct_on, insert
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from forge.core.db import project_scope
    from forge.core.logging import get_logger
    from forge.core.models import utcnow
    from forge.gateway.service import Caller, Gateway, tokenizers
    from forge.rag.chunking import chunk
    from forge.rag.models import Chunk, Document, Ingestion, KnowledgeBase
    from forge.rag.parsing import DOCLING_KINDS, ParsedDocument, ParseError, parse
    from forge.rag.search import SearchClient, acl_terms
    from forge.rag.storage import ObjectStore, parsed_key

log = get_logger(__name__)

EMBED_BATCH = 32
CHUNK_NAMESPACE = uuid.UUID("6f0c2a52-6d0e-4f1f-9d9e-8a3c1f7b2e41")
# Documents indexed at once while a knowledge base is built (a sliding window, not batches).
KB_PARALLELISM = 16
# A knowledge-base build hands documents to the worker in batches: every Temporal round trip and
# database session per document costs about 100 ms, which dominated one-page documents. A batch
# runs inside one activity, a few documents at a time; BATCHES batches run in parallel.
KB_BATCH = 8
KB_BATCH_INNER = 4
KB_BATCHES = 4


def chunk_id(
    kb_id: uuid.UUID, document_id: uuid.UUID, ordinal: int, content_hash: bytes
) -> uuid.UUID:
    return uuid.uuid5(CHUNK_NAMESPACE, f"{kb_id}:{document_id}:{ordinal}:{content_hash.hex()}")


@dataclass
class IngestDeps:
    sessions: async_sessionmaker[AsyncSession]
    store: ObjectStore
    search: SearchClient
    gateway: Gateway


class IngestActivities:
    def __init__(self, deps: IngestDeps) -> None:
        self.d = deps

    def _scope(self, project_id: str) -> Any:
        return project_scope(self.d.sessions, uuid.UUID(project_id))

    # ------------------------------------------------------------ parse
    @activity.defn(name="rag.parse_document")
    async def parse_document(self, project_id: str, document_id: str) -> list[str]:
        """Parse once per file content; returns the knowledge bases to index it into."""
        async with self._scope(project_id) as s:
            doc = await s.get(Document, uuid.UUID(document_id))
            if doc is None:
                return []  # deleted meanwhile
            doc.status, doc.error, doc.failed_at = "processing", None, None
            key = parsed_key(doc.project_id, doc.sha256.hex())
            kind, name, object_key = doc.kind, doc.name, doc.object_key
        if not await self.d.store.exists(key):
            parsed = await self._parse(kind, name, object_key)
            await self.d.store.put_bytes(
                key, json.dumps(parsed.to_json()).encode(), "application/json"
            )
            pages = parsed.pages
        else:
            pages = ParsedDocument.from_json(json.loads(await self.d.store.get_bytes(key))).pages
        async with self._scope(project_id) as s:
            doc = await s.get(Document, uuid.UUID(document_id))
            if doc is None:
                return []
            doc.parsed_key, doc.pages = key, pages
            kb_ids = list(
                (
                    await s.execute(
                        select(KnowledgeBase.id).where(
                            KnowledgeBase.collection_id == doc.collection_id
                        )
                    )
                ).scalars()
            )
            for kb in kb_ids:
                await s.execute(
                    insert(Ingestion)
                    .values(document_id=doc.id, kb_id=kb, project_id=doc.project_id)
                    .on_conflict_do_nothing()
                )
        return [str(k) for k in kb_ids]

    async def _parse(self, kind: str, name: str, object_key: str) -> ParsedDocument:
        try:
            if kind in DOCLING_KINDS:
                with tempfile.TemporaryDirectory() as tmp:
                    path = os.path.join(tmp, f"source.{kind}")
                    await self.d.store.download_to(object_key, path)
                    activity.heartbeat("parsing")
                    return await asyncio.to_thread(parse, kind, None, path, name)
            data = await self.d.store.get_bytes(object_key)
            return await asyncio.to_thread(parse, kind, data, None, name)
        except ParseError as exc:
            # The file itself is the problem: retrying cannot help.
            raise ApplicationError(exc.message, type=exc.code, non_retryable=True) from exc

    # ------------------------------------------------------------ index
    @activity.defn(name="rag.index_document")
    async def index_document(
        self, project_id: str, document_id: str, kb_id: str, bulk: bool = False
    ) -> dict[str, int]:
        """`bulk`: part of a knowledge-base build: the search index is not forced to refresh for
        each document (it refreshes by itself every second), which is what limited a build to a
        few documents per second."""
        pid, did, kid = uuid.UUID(project_id), uuid.UUID(document_id), uuid.UUID(kb_id)
        async with self._scope(project_id) as s:
            doc = await s.get(Document, did)
            kb = await s.get(KnowledgeBase, kid)
            if doc is None or kb is None or not doc.parsed_key:
                return {"chunks": 0, "embedded": 0, "reused": 0}
            await s.execute(
                insert(Ingestion)
                .values(
                    document_id=did,
                    kb_id=kid,
                    project_id=pid,
                    status="processing",
                    started_at=utcnow(),
                )
                .on_conflict_do_update(
                    index_elements=["document_id", "kb_id"],
                    set_={"status": "processing", "error": None, "started_at": utcnow()},
                )
            )
            parsed_location = doc.parsed_key
            acl = acl_terms(doc.acl_groups)
            owner = doc.created_by
            model, size, overlap, parser = (
                kb.embedding_model,
                kb.chunk_size,
                kb.chunk_overlap,
                kb.node_parser,
            )
            tokenizer = kb.tokenizer
        parsed = ParsedDocument.from_json(json.loads(await self.d.store.get_bytes(parsed_location)))
        # The knowledge base's tokenizer (the embedding model's, recorded at creation).
        counter = await asyncio.to_thread(tokenizers.counter, tokenizer)
        drafts = await asyncio.to_thread(
            chunk, parsed, size=size, overlap=overlap, parser=parser, count=counter.count
        )
        hashes = list({d.content_hash for d in drafts})

        # Reuse embeddings of identical chunks already in this knowledge base.
        known: dict[bytes, list[float]] = {}
        if hashes:
            async with self._scope(project_id) as s:
                rows = await s.execute(
                    select(Chunk.content_hash, Chunk.embedding)
                    .where(Chunk.kb_id == kid, Chunk.content_hash.in_(hashes))
                    .ext(distinct_on(Chunk.content_hash))
                )
                known = {h: list(e) for h, e in rows.all()}
        missing = [d for d in drafts if d.content_hash not in known]
        unique_missing = list({d.content_hash: d for d in missing}.values())
        caller = Caller(project_id=pid, user_id=owner, trace_id=f"ingest:{document_id}")
        for i in range(0, len(unique_missing), EMBED_BATCH):
            batch = unique_missing[i : i + EMBED_BATCH]
            response = await self.d.gateway.service.embeddings(
                caller, model, [d.embed_text for d in batch], {}
            )
            vectors = [
                row["embedding"]
                for row in sorted((response.body or {}).get("data", []), key=lambda r: r["index"])
            ]
            if len(vectors) != len(batch) or any(len(v) != len(vectors[0]) for v in vectors):
                raise ApplicationError("embedding model returned an unexpected shape")
            for d, v in zip(batch, vectors, strict=True):
                known[d.content_hash] = v
            activity.heartbeat(f"embedded {min(i + EMBED_BATCH, len(unique_missing))}")

        rows_out: list[dict[str, Any]] = []
        for d in drafts:
            vector = known[d.content_hash]
            rows_out.append(
                {
                    "id": chunk_id(kid, did, d.ordinal, d.content_hash),
                    "project_id": pid,
                    "kb_id": kid,
                    "document_id": did,
                    "ordinal": d.ordinal,
                    "content": d.content,
                    "heading_path": d.heading_path,
                    "page": d.page,
                    "metadata": {"title": parsed.title, "tokens_estimated": not counter.exact},
                    "acl_groups": acl,
                    "content_hash": d.content_hash,
                    "token_count": d.token_count,
                    "embedding": vector,
                }
            )
        new_ids = [r["id"] for r in rows_out]
        async with self._scope(project_id) as s:
            stale = list(
                (
                    await s.execute(
                        select(Chunk.id).where(
                            Chunk.kb_id == kid, Chunk.document_id == did, Chunk.id.not_in(new_ids)
                        )
                        if new_ids
                        else select(Chunk.id).where(Chunk.kb_id == kid, Chunk.document_id == did)
                    )
                ).scalars()
            )
            if stale:
                await s.execute(delete(Chunk).where(Chunk.id.in_(stale)))
            for i in range(0, len(rows_out), 200):
                stmt = insert(cast(Table, Chunk.__table__)).values(rows_out[i : i + 200])
                await s.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["id"], set_={"acl_groups": stmt.excluded.acl_groups}
                    )
                )
        refresh = "false" if bulk else "wait_for"
        await self.d.search.delete_ids(pid, stale, refresh=refresh)
        await self.d.search.index_chunks(
            pid,
            [
                {
                    "id": r["id"],
                    "project_id": str(pid),
                    "kb_id": str(kid),
                    "document_id": str(did),
                    "acl_groups": acl,
                    "heading_path": r["heading_path"],
                    "content": r["content"],
                    "page": r["page"],
                }
                for r in rows_out
            ],
            refresh=refresh,
        )
        counts = {
            "chunks": len(drafts),
            "embedded": len(unique_missing),
            "reused": len(drafts) - len(missing),
        }
        async with self._scope(project_id) as s:
            ing = await s.get(Ingestion, (did, kid))
            if ing is not None:
                ing.status, ing.error, ing.finished_at = "ready", None, utcnow()
                ing.chunks, ing.embedded, ing.reused = (
                    counts["chunks"],
                    counts["embedded"],
                    counts["reused"],
                )
        return counts

    @activity.defn(name="rag.index_batch")
    async def index_batch(
        self, project_id: str, kb_id: str, document_ids: list[str]
    ) -> dict[str, int]:
        """Index and settle several documents of a knowledge-base build in one activity. A failing
        document is marked failed (like `_index` does) and does not stop the others."""
        window = asyncio.Semaphore(KB_BATCH_INNER)
        failed = 0

        async def one(d: str) -> None:
            nonlocal failed
            async with window:
                try:
                    await self.index_document(project_id, d, kb_id, True)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failed += 1
                    await self.mark_ingestion_failed(
                        project_id, d, kb_id, str(exc) or type(exc).__name__
                    )
                await self.finish_document(project_id, d, None)
                activity.heartbeat(d)

        await asyncio.gather(*(one(d) for d in document_ids))
        return {"documents": len(document_ids), "failed": failed}

    # ------------------------------------------------------------ bookkeeping
    @activity.defn(name="rag.mark_ingestion_failed")
    async def mark_ingestion_failed(
        self, project_id: str, document_id: str, kb_id: str, error: str
    ) -> None:
        async with self._scope(project_id) as s:
            ing = await s.get(Ingestion, (uuid.UUID(document_id), uuid.UUID(kb_id)))
            if ing is not None:
                ing.status, ing.error, ing.finished_at = "failed", error[:2000], utcnow()

    @activity.defn(name="rag.finish_document")
    async def finish_document(self, project_id: str, document_id: str, error: str | None) -> str:
        """Document status: failed if parsing or any knowledge base failed, else ready."""
        async with self._scope(project_id) as s:
            doc = await s.get(Document, uuid.UUID(document_id))
            if doc is None:
                return "deleted"
            if error is None:
                failed = (
                    (
                        await s.execute(
                            select(Ingestion.error).where(
                                Ingestion.document_id == doc.id, Ingestion.status == "failed"
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if failed is not None:
                    error = f"Indexing failed: {failed}"
            if error:
                doc.status, doc.error, doc.failed_at = "failed", error[:2000], utcnow()
            else:
                doc.status, doc.error, doc.failed_at = "ready", None, None
            return str(doc.status)

    @activity.defn(name="rag.finish_ingest")
    async def finish_ingest(self, project_id: str, document_id: str, kb_ids: list[str]) -> str:
        """`finish_document`, then `finish_kb` for each knowledge base the document went into:
        a knowledge base created while the document was parsed may otherwise stay `building`."""
        status = await self.finish_document(project_id, document_id, None)
        for kb in kb_ids:
            await self.finish_kb(project_id, kb)
        return status

    @activity.defn(name="rag.kb_documents")
    async def kb_documents(self, project_id: str, kb_id: str) -> list[str]:
        """Parsed documents of the knowledge base's collection, with an ingestion row each."""
        async with self._scope(project_id) as s:
            kb = await s.get(KnowledgeBase, uuid.UUID(kb_id))
            if kb is None:
                return []
            docs = list(
                (
                    await s.execute(
                        select(Document.id).where(
                            Document.collection_id == kb.collection_id,
                            Document.parsed_key.is_not(None),
                        )
                    )
                ).scalars()
            )
            for did in docs:
                await s.execute(
                    insert(Ingestion)
                    .values(document_id=did, kb_id=kb.id, project_id=kb.project_id)
                    .on_conflict_do_nothing()
                )
            return [str(d) for d in docs]

    @activity.defn(name="rag.finish_kb")
    async def finish_kb(self, project_id: str, kb_id: str) -> str:
        """Ready once every parsed document has been tried; failures stay visible per file."""
        async with self._scope(project_id) as s:
            kb = await s.get(KnowledgeBase, uuid.UUID(kb_id))
            if kb is None:
                return "deleted"
            pending = (
                await s.execute(
                    select(func.count())
                    .select_from(Ingestion)
                    .where(
                        Ingestion.kb_id == kb.id, Ingestion.status.in_(("pending", "processing"))
                    )
                )
            ).scalar_one()
            if not pending and kb.status != "ready":
                # A bulk build did not wait for the index to refresh after each document.
                await self.d.search.refresh()
                kb.status, kb.ready_at = "ready", utcnow()
            return str(kb.status)

    def all(self) -> list[Any]:
        return [
            self.parse_document,
            self.index_document,
            self.index_batch,
            self.mark_ingestion_failed,
            self.finish_document,
            self.finish_ingest,
            self.kb_documents,
            self.finish_kb,
        ]


def _reason(err: BaseException) -> str:
    cause = err.cause if isinstance(err, ActivityError) and err.cause else err
    return str(cause) or type(cause).__name__


_PROFILES: dict[str, tuple[timedelta, timedelta | None, int]] = {
    # name: (start-to-close timeout, heartbeat timeout, maximum attempts)
    "parse": (timedelta(minutes=30), timedelta(minutes=10), 3),
    "index": (timedelta(minutes=30), timedelta(minutes=5), 5),
    "quick": (timedelta(seconds=60), None, 10),
}


async def _run(name: str, args: list[Any], profile: str) -> Any:
    timeout, heartbeat, attempts = _PROFILES[profile]
    return await workflow.execute_activity(
        name,
        args=args,
        start_to_close_timeout=timeout,
        heartbeat_timeout=heartbeat,
        retry_policy=RetryPolicy(maximum_attempts=attempts, initial_interval=timedelta(seconds=2)),
    )


async def _index(project_id: str, document_id: str, kb_id: str, bulk: bool = False) -> None:
    try:
        args: list[Any] = [project_id, document_id, kb_id] + ([True] if bulk else [])
        await _run("rag.index_document", args, "index")
    except ActivityError as err:
        await _run(
            "rag.mark_ingestion_failed", [project_id, document_id, kb_id, _reason(err)], "quick"
        )


@workflow.defn(name="IngestDocument")
class IngestDocumentWorkflow:
    @workflow.run
    async def run(self, payload: dict[str, str]) -> str:
        pid, did = payload["project_id"], payload["document_id"]
        try:
            kb_ids: list[str] = await _run("rag.parse_document", [pid, did], "parse")
        except ActivityError as err:
            return str(await _run("rag.finish_document", [pid, did, _reason(err)], "quick"))
        await asyncio.gather(*(_index(pid, did, kb) for kb in kb_ids))
        if workflow.patched("finish-ingest"):
            # One round trip instead of one per knowledge base plus one for the document
            return str(await _run("rag.finish_ingest", [pid, did, kb_ids], "quick"))
        status = str(await _run("rag.finish_document", [pid, did, None], "quick"))
        # A knowledge base created while this document was being parsed starts its own workflow,
        # which may end before this index does and leave the base `building`: settle it here.
        for kb in kb_ids:
            await _run("rag.finish_kb", [pid, kb], "quick")
        return status


@workflow.defn(name="IndexKnowledgeBase")
class IndexKnowledgeBaseWorkflow:
    @workflow.run
    async def run(self, payload: dict[str, str]) -> str:
        pid, kid = payload["project_id"], payload["kb_id"]
        docs: list[str] = await _run("rag.kb_documents", [pid, kid], "quick")
        if workflow.patched("kb-batches"):
            gate = asyncio.Semaphore(KB_BATCHES)

            async def batch(ids: list[str]) -> None:
                async with gate:
                    await _run("rag.index_batch", [pid, kid, ids], "index")

            await asyncio.gather(
                *(batch(docs[i : i + KB_BATCH]) for i in range(0, len(docs), KB_BATCH))
            )
            return str(await _run("rag.finish_kb", [pid, kid], "quick"))
        window = asyncio.Semaphore(KB_PARALLELISM)

        async def one(d: str) -> None:
            async with window:
                await _index(pid, d, kid, bulk=True)
                # A failed index shows on the file too (status, reason, Retry).
                await _run("rag.finish_document", [pid, d, None], "quick")

        await asyncio.gather(*(one(d) for d in docs))
        return str(await _run("rag.finish_kb", [pid, kid], "quick"))


WORKFLOWS = [IngestDocumentWorkflow, IndexKnowledgeBaseWorkflow]
