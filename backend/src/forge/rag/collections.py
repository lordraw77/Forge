"""Data Collections and documents (E1.14): upload, status, retry, ACL, download, delete.

Files go to object storage under the project prefix; the type is sniffed from the content.
Every query runs in the caller's project scope (RLS, ADR-002). Ingestion runs on Temporal
(`forge.rag.ingest`); this module only records documents and starts workflows.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import IO, Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import audit
from forge.core.context import Actor
from forge.core.db import project_scope
from forge.core.errors import ProblemError
from forge.core.ids import uuid7
from forge.core.logging import get_logger
from forge.rag import sniff
from forge.rag.models import Chunk, DataCollection, Document, Ingestion, KnowledgeBase
from forge.rag.schemas import (
    AclEntry,
    CollectionIn,
    CollectionOut,
    CollectionUpdateIn,
    DocumentOut,
    IngestionOut,
    KnowledgeBaseOut,
    StatusCounts,
    UploadResult,
)
from forge.rag.search import SearchClient
from forge.rag.starter import IngestStarter
from forge.rag.storage import ObjectStore, document_key

log = get_logger(__name__)
# project id -> object with `groups` and `roles` dicts (id -> name), from the tenancy module.
PrincipalsLookup = Callable[[uuid.UUID], Awaitable[Any]]
NOT_FOUND = ProblemError(404, "not_found", "Collection not found")
DOC_NOT_FOUND = ProblemError(404, "not_found", "Document not found")


@dataclass
class UploadFile:
    filename: str
    path: str  # folder path inside an uploaded folder, "" for single files
    fh: IO[bytes]
    size: int


def _clean_name(filename: str) -> tuple[str, str]:
    """Split a browser-supplied relative path into (folder path, file name), safely."""
    parts = [p for p in filename.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    if not parts:
        raise sniff.UnsupportedFile("missing file name")
    name = parts[-1][:255]
    path = "/".join(parts[:-1])[:1000]
    return path, name


def _sha256(fh: IO[bytes]) -> tuple[bytes, bytes]:
    fh.seek(0)
    digest = hashlib.sha256()
    head = b""
    while block := fh.read(1024 * 1024):
        if not head:
            head = block[:8192]
        digest.update(block)
    fh.seek(0)
    return digest.digest(), head


def _rollup(counts: StatusCounts) -> str:
    total = counts.pending + counts.processing + counts.ready + counts.failed
    if not total:
        return "empty"
    if counts.pending or counts.processing:
        return "in_progress"
    return "failed" if counts.failed else "success"


class CollectionService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        store: ObjectStore,
        search: SearchClient,
        starter: IngestStarter,
        principals: PrincipalsLookup,
    ) -> None:
        self._sessions = sessions
        self._store = store
        self._search = search
        self._starter = starter
        self._principals = principals
        self._ready = False

    async def acl(
        self, project_id: uuid.UUID, group_ids: list[uuid.UUID], role_ids: list[uuid.UUID]
    ) -> list[str]:
        """Validate groups and roles of the project; returns the stored ACL terms."""
        known = await self._principals(project_id)
        unknown = [str(g) for g in group_ids if g not in known.groups]
        unknown += [str(r) for r in role_ids if r not in known.roles]
        if unknown:
            raise ProblemError(422, "unknown_principal", "Unknown group or role", unknown)
        return sorted({f"group:{g}" for g in group_ids} | {f"role:{r}" for r in role_ids})

    async def _ensure_backends(self) -> None:
        """Bucket and BM25 index, if the API started before S3 or OpenSearch."""
        if not self._ready:
            await self._store.ensure_bucket()
            await self._search.ensure_index()
            self._ready = True

    @asynccontextmanager
    async def _session(self, actor: Actor, project_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            yield s

    @staticmethod
    async def _collection(s: AsyncSession, collection_id: uuid.UUID) -> DataCollection:
        row = await s.get(DataCollection, collection_id)
        if row is None:
            raise NOT_FOUND
        return row

    @staticmethod
    async def _document(
        s: AsyncSession, collection_id: uuid.UUID, document_id: uuid.UUID
    ) -> Document:
        row = await s.get(Document, document_id)
        if row is None or row.collection_id != collection_id:
            raise DOC_NOT_FOUND
        return row

    # ------------------------------------------------------------ collections
    async def _collection_out(
        self, s: AsyncSession, rows: list[DataCollection]
    ) -> list[CollectionOut]:
        ids = [r.id for r in rows]
        counts: dict[uuid.UUID, StatusCounts] = {i: StatusCounts() for i in ids}
        sizes: dict[uuid.UUID, int] = dict.fromkeys(ids, 0)
        kbs: dict[uuid.UUID, int] = dict.fromkeys(ids, 0)
        if ids:
            for cid, status, n, size in (
                await s.execute(
                    select(
                        Document.collection_id,
                        Document.status,
                        func.count(),
                        func.coalesce(func.sum(Document.size_bytes), 0),
                    )
                    .where(Document.collection_id.in_(ids))
                    .group_by(Document.collection_id, Document.status)
                )
            ).all():
                setattr(counts[cid], status, n)
                sizes[cid] += int(size)
            for cid, n in (
                await s.execute(
                    select(KnowledgeBase.collection_id, func.count())
                    .where(KnowledgeBase.collection_id.in_(ids))
                    .group_by(KnowledgeBase.collection_id)
                )
            ).all():
                kbs[cid] = n
        return [
            CollectionOut(
                id=r.id,
                name=r.name,
                description=r.description,
                status=_rollup(counts[r.id]),
                documents=counts[r.id],
                size_bytes=sizes[r.id],
                knowledge_bases=kbs[r.id],
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]

    async def list_collections(self, actor: Actor, project_id: uuid.UUID) -> list[CollectionOut]:
        async with self._session(actor, project_id) as s:
            rows = list(
                (
                    await s.execute(
                        select(DataCollection).order_by(func.lower(DataCollection.name))
                    )
                ).scalars()
            )
            return await self._collection_out(s, rows)

    async def get_collection(
        self, actor: Actor, project_id: uuid.UUID, collection_id: uuid.UUID
    ) -> CollectionOut:
        async with self._session(actor, project_id) as s:
            return (await self._collection_out(s, [await self._collection(s, collection_id)]))[0]

    async def create(
        self, actor: Actor, project_id: uuid.UUID, body: CollectionIn
    ) -> CollectionOut:
        async with self._session(actor, project_id) as s:
            row = DataCollection(
                project_id=project_id,
                name=body.name,
                description=body.description,
                created_by=actor.id,
                updated_by=actor.id,
            )
            s.add(row)
            try:
                await s.flush()
            except IntegrityError as exc:
                raise ProblemError(
                    409, "name_taken", f"A collection named '{body.name}' already exists"
                ) from exc
            await audit.record(
                s,
                actor,
                "collection.create",
                "data_collection",
                row.id,
                {"name": body.name},
                project_id=project_id,
            )
            return (await self._collection_out(s, [row]))[0]

    async def update(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        collection_id: uuid.UUID,
        body: CollectionUpdateIn,
    ) -> CollectionOut:
        async with self._session(actor, project_id) as s:
            row = await self._collection(s, collection_id)
            diff: dict[str, Any] = {}
            if body.name is not None and body.name != row.name:
                diff["name"] = [row.name, body.name]
                row.name = body.name
            if body.description is not None and body.description != row.description:
                diff["description"] = "changed"
                row.description = body.description
            if diff:
                row.updated_by = actor.id
                try:
                    await s.flush()
                except IntegrityError as exc:
                    raise ProblemError(
                        409, "name_taken", "A collection with this name exists"
                    ) from exc
                await audit.record(
                    s,
                    actor,
                    "collection.update",
                    "data_collection",
                    row.id,
                    diff,
                    project_id=project_id,
                )
            return (await self._collection_out(s, [row]))[0]

    async def delete(self, actor: Actor, project_id: uuid.UUID, collection_id: uuid.UUID) -> None:
        """Deletes files, chunks and embeddings (US-1.14.2), and the collection's indexes."""
        async with self._session(actor, project_id) as s:
            row = await self._collection(s, collection_id)
            from forge.rag.models import PipelineVersion

            used = (
                await s.execute(
                    select(func.count())
                    .select_from(PipelineVersion)
                    .join(KnowledgeBase, KnowledgeBase.id == PipelineVersion.kb_id)
                    .where(KnowledgeBase.collection_id == row.id)
                )
            ).scalar_one()
            if used:
                raise ProblemError(
                    409,
                    "collection_in_use",
                    "RAG pipelines use this collection; delete them first",
                )
            keys = list(
                (
                    await s.execute(
                        select(Document.object_key).where(Document.collection_id == row.id)
                    )
                ).scalars()
            )
            kb_ids = list(
                (
                    await s.execute(
                        select(KnowledgeBase.id).where(KnowledgeBase.collection_id == row.id)
                    )
                ).scalars()
            )
            await audit.record(
                s,
                actor,
                "collection.delete",
                "data_collection",
                row.id,
                {"name": row.name, "documents": len(keys)},
                project_id=project_id,
            )
            await s.delete(row)
        await self._cleanup(project_id, keys, kb_ids=kb_ids)

    async def _cleanup(
        self,
        project_id: uuid.UUID,
        keys: list[str],
        *,
        kb_ids: list[uuid.UUID] | None = None,
        document_ids: list[uuid.UUID] | None = None,
    ) -> None:
        """Best effort after the database commit: PostgreSQL is the system of record."""
        try:
            if kb_ids:
                await self._search.delete_where(project_id, "kb_id", kb_ids)
            if document_ids:
                await self._search.delete_where(project_id, "document_id", document_ids)
        except Exception as exc:
            log.error("rag.search_cleanup_failed", error=str(exc), project_id=str(project_id))
        try:
            await self._store.delete(keys)
        except Exception as exc:
            log.error("rag.storage_cleanup_failed", error=str(exc), project_id=str(project_id))

    # ------------------------------------------------------------ documents
    @staticmethod
    def _acl_out(terms: list[str], names: Any) -> list[AclEntry]:
        out: list[AclEntry] = []
        for term in terms:
            kind, _, raw = term.partition(":")
            if kind not in ("group", "role"):
                continue
            pid = uuid.UUID(raw)
            table = (names.groups if kind == "group" else names.roles) if names else {}
            out.append(AclEntry(kind=kind, id=pid, name=table.get(pid)))
        return out

    @classmethod
    def _doc_out(
        cls,
        doc: Document,
        ingestions: list[tuple[Ingestion, str]] | None = None,
        names: Any = None,
    ) -> DocumentOut:
        return DocumentOut(
            id=doc.id,
            collection_id=doc.collection_id,
            name=doc.name,
            path=doc.path,
            kind=doc.kind,
            mime_type=doc.mime_type,
            size_bytes=doc.size_bytes,
            sha256=doc.sha256.hex(),
            acl=cls._acl_out(doc.acl_groups, names),
            status=doc.status,
            error=doc.error,
            failed_at=doc.failed_at,
            pages=doc.pages,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
            ingestions=[
                IngestionOut(
                    kb_id=i.kb_id,
                    embedding_model=model,
                    status=i.status,
                    error=i.error,
                    chunks=i.chunks,
                    embedded=i.embedded,
                    reused=i.reused,
                    finished_at=i.finished_at,
                )
                for i, model in (ingestions or [])
            ],
        )

    async def list_documents(
        self, actor: Actor, project_id: uuid.UUID, collection_id: uuid.UUID
    ) -> list[DocumentOut]:
        async with self._session(actor, project_id) as s:
            await self._collection(s, collection_id)
            docs = list(
                (
                    await s.execute(
                        select(Document)
                        .where(Document.collection_id == collection_id)
                        .order_by(Document.path, func.lower(Document.name))
                    )
                ).scalars()
            )
            ingestions: dict[uuid.UUID, list[tuple[Ingestion, str]]] = {d.id: [] for d in docs}
            if docs:
                rows = await s.execute(
                    select(Ingestion, KnowledgeBase.embedding_model)
                    .join(KnowledgeBase, KnowledgeBase.id == Ingestion.kb_id)
                    .where(Ingestion.document_id.in_([d.id for d in docs]))
                )
                for ing, model in rows.all():
                    ingestions[ing.document_id].append((ing, model))
            names = await self._principals(project_id)
            return [self._doc_out(d, ingestions[d.id], names) for d in docs]

    async def upload(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        collection_id: uuid.UUID,
        files: list[UploadFile],
        acl_terms_: list[str],
    ) -> list[UploadResult]:
        async with self._session(actor, project_id) as s:
            await self._collection(s, collection_id)
        await self._ensure_backends()
        results: list[UploadResult] = []
        to_start: list[uuid.UUID] = []
        for f in files:
            try:
                folder, name = _clean_name(f.path + "/" + f.filename if f.path else f.filename)
                if f.size > sniff.MAX_UPLOAD_BYTES:
                    raise sniff.UnsupportedFile("file exceeds 100 MB")
                digest, head = _sha256(f.fh)
                kind = sniff.detect(head, name, path=getattr(f.fh, "name", None))
            except sniff.UnsupportedFile as exc:
                results.append(UploadResult(filename=f.filename, status="rejected", error=str(exc)))
                continue
            result = await self._store_one(
                actor, project_id, collection_id, folder, name, f, digest, kind, acl_terms_
            )
            results.append(result)
            if result.status == "accepted" and result.document:
                to_start.append(result.document.id)
        for doc_id in to_start:
            await self._starter.document(project_id, doc_id)
        return results

    async def _store_one(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        collection_id: uuid.UUID,
        folder: str,
        name: str,
        f: UploadFile,
        digest: bytes,
        kind: sniff.FileKind,
        acl: list[str],
    ) -> UploadResult:
        async with self._session(actor, project_id) as s:
            existing = (
                await s.execute(
                    select(Document).where(
                        Document.collection_id == collection_id,
                        Document.path == folder,
                        Document.name == name,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None and existing.sha256 == digest:
                # US-1.15.1 #3: identical content, nothing to re-parse or re-embed.
                return UploadResult(
                    filename=f.filename, status="unchanged", document=self._doc_out(existing)
                )
            doc_id = existing.id if existing else uuid7()
        key = document_key(project_id, collection_id, doc_id)
        await self._store.put_file(key, f.fh, kind.mime)
        async with self._session(actor, project_id) as s:
            doc = await s.get(Document, doc_id)
            if doc is None:
                doc = Document(
                    id=doc_id,
                    project_id=project_id,
                    collection_id=collection_id,
                    name=name,
                    path=folder,
                    created_by=actor.id,
                    object_key=key,
                    mime_type=kind.mime,
                    kind=kind.kind,
                    size_bytes=f.size,
                    sha256=digest,
                    acl_groups=acl,
                )
                s.add(doc)
            else:
                doc.mime_type, doc.kind, doc.size_bytes = kind.mime, kind.kind, f.size
                doc.sha256, doc.object_key = digest, key
                doc.parsed_key, doc.pages = None, None
            doc.status, doc.error, doc.failed_at = "pending", None, None
            await s.flush()
            await audit.record(
                s,
                actor,
                "document.upload",
                "rag_document",
                doc.id,
                {
                    "name": name,
                    "path": folder,
                    "kind": kind.kind,
                    "size": f.size,
                    "replaced": existing is not None,
                },
                project_id=project_id,
            )
            return UploadResult(filename=f.filename, status="accepted", document=self._doc_out(doc))

    async def retry(
        self, actor: Actor, project_id: uuid.UUID, collection_id: uuid.UUID, document_id: uuid.UUID
    ) -> DocumentOut:
        async with self._session(actor, project_id) as s:
            doc = await self._document(s, collection_id, document_id)
            doc.status, doc.error, doc.failed_at = "pending", None, None
            await s.execute(
                update(Ingestion)
                .where(Ingestion.document_id == doc.id, Ingestion.status == "failed")
                .values(status="pending", error=None)
            )
            out = self._doc_out(doc)
        await self._starter.document(project_id, document_id)
        return out

    async def set_acl(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        collection_id: uuid.UUID,
        document_id: uuid.UUID,
        group_ids: list[uuid.UUID],
        role_ids: list[uuid.UUID],
    ) -> DocumentOut:
        """ACL changes reach the chunks now (PostgreSQL) and the BM25 index via re-indexing."""
        from forge.rag.search import acl_terms

        groups = await self.acl(project_id, group_ids, role_ids)
        names = await self._principals(project_id)
        async with self._session(actor, project_id) as s:
            doc = await self._document(s, collection_id, document_id)
            before = doc.acl_groups
            doc.acl_groups = groups
            await s.execute(
                update(Chunk)
                .where(Chunk.document_id == doc.id)
                .values(acl_groups=acl_terms(groups))
            )
            await audit.record(
                s,
                actor,
                "document.acl",
                "rag_document",
                doc.id,
                {"before": before, "after": groups},
                project_id=project_id,
            )
            out = self._doc_out(doc, None, names)
        await self._starter.document(project_id, document_id)
        return out

    async def delete_document(
        self, actor: Actor, project_id: uuid.UUID, collection_id: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        async with self._session(actor, project_id) as s:
            doc = await self._document(s, collection_id, document_id)
            key = doc.object_key
            await audit.record(
                s,
                actor,
                "document.delete",
                "rag_document",
                doc.id,
                {"name": doc.name},
                project_id=project_id,
            )
            await s.delete(doc)
        await self._cleanup(project_id, [key], document_ids=[document_id])

    async def download(
        self, actor: Actor, project_id: uuid.UUID, collection_id: uuid.UUID, document_id: uuid.UUID
    ) -> tuple[DocumentOut, Iterator[bytes]]:
        async with self._session(actor, project_id) as s:
            doc = await self._document(s, collection_id, document_id)
            out = self._doc_out(doc)
            key = doc.object_key
        return out, await self._store.open_stream(key)

    async def knowledge_bases(
        self, actor: Actor, project_id: uuid.UUID, collection_id: uuid.UUID
    ) -> list[KnowledgeBaseOut]:
        async with self._session(actor, project_id) as s:
            await self._collection(s, collection_id)
            kbs = list(
                (
                    await s.execute(
                        select(KnowledgeBase)
                        .where(KnowledgeBase.collection_id == collection_id)
                        .order_by(KnowledgeBase.created_at.desc())
                    )
                ).scalars()
            )
            out: list[KnowledgeBaseOut] = []
            for kb in kbs:
                counts = StatusCounts()
                for status, n in (
                    await s.execute(
                        select(Ingestion.status, func.count())
                        .where(Ingestion.kb_id == kb.id)
                        .group_by(Ingestion.status)
                    )
                ).all():
                    setattr(counts, status, n)
                chunks = (
                    await s.execute(
                        select(func.count()).select_from(Chunk).where(Chunk.kb_id == kb.id)
                    )
                ).scalar_one()
                out.append(
                    KnowledgeBaseOut(
                        id=kb.id,
                        collection_id=kb.collection_id,
                        embedding_model=kb.embedding_model,
                        dims=kb.dims,
                        chunk_size=kb.chunk_size,
                        chunk_overlap=kb.chunk_overlap,
                        node_parser=kb.node_parser,
                        status=kb.status,
                        documents=counts,
                        chunks=chunks,
                        created_at=kb.created_at,
                        ready_at=kb.ready_at,
                    )
                )
            return out
