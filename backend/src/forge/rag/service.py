"""Public interface of the rag module (ADR-001)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.core.crypto import Envelope
from forge.core.settings import Settings
from forge.gateway.service import Gateway
from forge.prompts.service import PromptStudio
from forge.rag.collections import CollectionService, PrincipalsLookup, UploadFile
from forge.rag.ingest import WORKFLOWS, IngestActivities, IngestDeps
from forge.rag.parsing import docling_models_present, download_docling_models
from forge.rag.reindex import reindex
from forge.rag.retrieval import Retriever
from forge.rag.schemas import (
    CollectionIn,
    CollectionOut,
    CollectionUpdateIn,
    DocumentOut,
    DocumentUpdateIn,
    KnowledgeBaseOut,
    PipelineCreateIn,
    PipelineDetail,
    PipelineDuplicateIn,
    PipelinePage,
    PipelinePublishIn,
    PipelineTagIn,
    PipelineUpdateIn,
    PipelineVersionIn,
    PipelineVersionOut,
    QueryIn,
    RagRunDetail,
    RagRunOut,
    UploadResult,
)
from forge.rag.search import SearchClient
from forge.rag.sniff import MAX_UPLOAD_BYTES
from forge.rag.sources.service import SourceService
from forge.rag.starter import IngestStarter
from forge.rag.storage import ObjectStore
from forge.rag.studio import AgentPassage, AgentPipeline, AgentSearch, GroupsLookup, RagStudio


@dataclass(frozen=True)
class SourceRoot:
    """A directory `filesystem` sources may read, and the projects allowed to read it."""

    path: Path
    projects: frozenset[uuid.UUID] | None  # None = every project (written `*`)

    def allows(self, project_id: uuid.UUID) -> bool:
        return self.projects is None or project_id in self.projects


def source_roots(settings: Settings) -> list[SourceRoot]:
    """`FORGE_SOURCE_ROOTS` = `path=<project id>,<project id>;path=*`. An entry without `=` is
    ignored: sharing a directory with every project must be written out (`=*`)."""
    roots: list[SourceRoot] = []
    for entry in settings.source_roots.split(";"):
        path, sep, who = entry.strip().rpartition("=")
        if not sep or not path.strip():
            continue
        who = who.strip()
        try:
            projects = (
                None
                if who == "*"
                else frozenset(uuid.UUID(p.strip()) for p in who.split(",") if p.strip())
            )
        except ValueError:
            continue
        if projects is None or projects:
            roots.append(SourceRoot(Path(path.strip()), projects))
    return roots


def roots_for(roots: list[SourceRoot], project_id: uuid.UUID) -> list[Path]:
    return [r.path for r in roots if r.allows(project_id)]


@dataclass
class Rag:
    store: ObjectStore
    starter: IngestStarter
    collections: CollectionService
    studio: RagStudio
    sources: SourceService

    @classmethod
    def build(
        cls,
        settings: Settings,
        sessions: async_sessionmaker[AsyncSession],
        search: SearchClient,
        gateway: Gateway,
        prompts: PromptStudio,
        reader_terms: GroupsLookup,
        principals: PrincipalsLookup,
        envelope: Envelope,
    ) -> Rag:
        store = ObjectStore(settings)
        starter = IngestStarter(settings)
        collections = CollectionService(sessions, store, search, starter, principals)
        return cls(
            store=store,
            starter=starter,
            collections=collections,
            sources=SourceService(sessions, envelope, collections, starter, source_roots(settings)),
            studio=RagStudio(
                sessions,
                gateway,
                prompts,
                Retriever(sessions, search, gateway),
                starter,
                reader_terms,
            ),
        )

    async def start(self, search: SearchClient) -> None:
        """Create the bucket and the BM25 index if they are missing."""
        await self.store.ensure_bucket()
        await search.ensure_index()


__all__ = [
    "MAX_UPLOAD_BYTES",
    "WORKFLOWS",
    "AgentPassage",
    "AgentPipeline",
    "AgentSearch",
    "CollectionIn",
    "CollectionOut",
    "CollectionUpdateIn",
    "DocumentOut",
    "DocumentUpdateIn",
    "IngestActivities",
    "IngestDeps",
    "IngestStarter",
    "KnowledgeBaseOut",
    "ObjectStore",
    "PipelineCreateIn",
    "PipelineDetail",
    "PipelineDuplicateIn",
    "PipelinePage",
    "PipelinePublishIn",
    "PipelineTagIn",
    "PipelineUpdateIn",
    "PipelineVersionIn",
    "PipelineVersionOut",
    "QueryIn",
    "Rag",
    "RagRunDetail",
    "RagRunOut",
    "RagStudio",
    "Retriever",
    "SearchClient",
    "UploadFile",
    "UploadResult",
    "docling_models_present",
    "download_docling_models",
    "reindex",
]
