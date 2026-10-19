"""Public interface of the rag module (ADR-001)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.core.settings import Settings
from forge.gateway.service import Gateway
from forge.prompts.service import PromptStudio
from forge.rag.collections import CollectionService, PrincipalsLookup, UploadFile
from forge.rag.ingest import WORKFLOWS, IngestActivities, IngestDeps
from forge.rag.parsing import docling_models_present, download_docling_models
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
from forge.rag.starter import IngestStarter
from forge.rag.storage import ObjectStore
from forge.rag.studio import AgentPassage, AgentPipeline, AgentSearch, GroupsLookup, RagStudio


@dataclass
class Rag:
    store: ObjectStore
    starter: IngestStarter
    collections: CollectionService
    studio: RagStudio

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
    ) -> Rag:
        store = ObjectStore(settings)
        starter = IngestStarter(settings)
        return cls(
            store=store,
            starter=starter,
            collections=CollectionService(sessions, store, search, starter, principals),
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
]
