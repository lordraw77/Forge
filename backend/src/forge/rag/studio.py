"""RAG Studio (E1.17, US-1.15.2): pipelines with immutable versions and tags, the knowledge
base behind each version, test queries with citations and traces.

A knowledge base is the set of chunks for (collection, embedding model, chunking). A pipeline
version points at one. Saving a version with new index parameters finds or creates the
matching knowledge base and indexes it in the background; until it is ready, queries use the
newest ready knowledge base of earlier versions of the same pipeline (US-1.15.2).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, exists, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import audit
from forge.core.context import Actor
from forge.core.db import project_scope
from forge.core.errors import ProblemError
from forge.core.ids import uuid7
from forge.core.models import utcnow
from forge.gateway.service import Caller, Gateway
from forge.prompts.service import PromptStudio, ReferenceOut, TemplateError, render
from forge.rag.answer import Synthesizer
from forge.rag.models import (
    DataCollection,
    KnowledgeBase,
    Pipeline,
    PipelineTag,
    PipelineVersion,
    RagRun,
)
from forge.rag.retrieval import RetrievalParams, Retriever
from forge.rag.schemas import (
    Citation,
    PipelineConfig,
    PipelineCreateIn,
    PipelineDetail,
    PipelineOut,
    PipelinePage,
    PipelineUpdateIn,
    PipelineVersionIn,
    PipelineVersionOut,
    QueryIn,
    RagRunDetail,
    RagRunOut,
)
from forge.rag.starter import IngestStarter

EMBED_KINDS = frozenset({"embedding"})
CHAT_KINDS = frozenset({"chat"})
RERANK_KINDS = frozenset({"reranker"})
TAG_ORDER = ("draft", "staging", "prod")
NOT_FOUND = ProblemError(404, "not_found", "RAG pipeline not found")
GroupsLookup = Callable[[uuid.UUID, uuid.UUID | None], Awaitable[list[str]]]
ReferenceCheck = Callable[[AsyncSession, uuid.UUID], Awaitable[list[ReferenceOut]]]


@dataclass(frozen=True)
class AgentPipeline:
    """A pipeline version as an agent uses it: pinned, with what a retriever tool needs."""

    id: uuid.UUID
    name: str
    description: str
    version: int
    collection_name: str | None


@dataclass
class AgentPassage:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_name: str
    page: int | None
    heading_path: str
    score: float
    content: str
    url: str


@dataclass
class AgentSearch:
    passages: list[AgentPassage]
    trace: dict[str, Any]
    tokens: int = 0
    cost: Decimal = Decimal(0)


def _cursor(at: datetime, pid: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{at.isoformat()}|{pid}".encode()).decode().rstrip("=")


def _uncursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        stamp, pid = raw.split("|", 1)
        return datetime.fromisoformat(stamp), uuid.UUID(pid)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise ProblemError(400, "invalid_cursor", "Invalid pagination cursor") from exc


class RagStudio:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        gateway: Gateway,
        prompts: PromptStudio,
        retriever: Retriever,
        starter: IngestStarter,
        groups: GroupsLookup,
    ) -> None:
        self._sessions = sessions
        self._gateway = gateway
        self._prompts = prompts
        self._retriever = retriever
        self._synth = Synthesizer(gateway)
        self._starter = starter
        self._groups = groups
        self._reference_checks: list[ReferenceCheck] = []
        prompts.add_reference_check(self.prompt_references)

    def add_reference_check(self, check: ReferenceCheck) -> None:
        """Other modules (agents, E1.18) list what uses a pipeline, so delete can refuse."""
        self._reference_checks.append(check)

    @asynccontextmanager
    async def _session(self, actor: Actor, project_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            yield s

    # ------------------------------------------------------------ helpers
    @staticmethod
    async def _pipeline(s: AsyncSession, pipeline_id: uuid.UUID, *, lock: bool = False) -> Pipeline:
        stmt = select(Pipeline).where(Pipeline.id == pipeline_id)
        if lock:
            stmt = stmt.with_for_update()
        row = (await s.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise NOT_FOUND
        return row

    @staticmethod
    async def _version(s: AsyncSession, pipeline: Pipeline, number: int | None) -> PipelineVersion:
        n = number or pipeline.latest_version
        row = (
            await s.execute(
                select(PipelineVersion).where(
                    PipelineVersion.pipeline_id == pipeline.id, PipelineVersion.version == n
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise ProblemError(404, "version_not_found", f"Version {n} not found")
        return row

    @staticmethod
    async def _tags(s: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, int]]:
        out: dict[uuid.UUID, dict[str, int]] = {i: {} for i in ids}
        if ids:
            rows = await s.execute(
                select(PipelineTag.pipeline_id, PipelineTag.tag, PipelineTag.version).where(
                    PipelineTag.pipeline_id.in_(ids)
                )
            )
            for pid, tag, version in rows.all():
                out[pid][tag] = version
        return out

    @staticmethod
    def _out(
        p: Pipeline, tags: dict[str, int], config: dict[str, Any], collection: str | None
    ) -> PipelineOut:
        return PipelineOut(
            id=p.id,
            name=p.name,
            description=p.description,
            status="published" if "prod" in tags else "draft",
            latest_version=p.latest_version,
            published_version=tags.get("prod"),
            tags=tags,
            collection_name=collection,
            search_method=config.get("search_method", "hybrid"),
            created_at=p.created_at,
            updated_at=p.updated_at,
        )

    async def _detail(
        self, s: AsyncSession, p: Pipeline, number: int | None = None
    ) -> PipelineDetail:
        v = await self._version(s, p, number)
        tags = (await self._tags(s, [p.id]))[p.id]
        kb = await s.get(KnowledgeBase, v.kb_id)
        coll = await s.get(DataCollection, kb.collection_id) if kb else None
        base = self._out(p, tags, v.config, coll.name if coll else None)
        return PipelineDetail(**base.model_dump(), version=self._version_out(v, tags, kb))

    @staticmethod
    def _version_out(
        v: PipelineVersion, tags: dict[str, int], kb: KnowledgeBase | None
    ) -> PipelineVersionOut:
        return PipelineVersionOut(
            version=v.version,
            config=PipelineConfig.model_validate(v.config),
            kb_id=v.kb_id,
            kb_status=kb.status if kb else "deleted",
            note=v.note,
            tags=[t for t in TAG_ORDER if tags.get(t) == v.version],
            created_at=v.created_at,
            created_by=v.created_by,
        )

    async def _move_tag(
        self, s: AsyncSession, actor: Actor, p: Pipeline, tag: str, version: int
    ) -> None:
        row = await s.get(PipelineTag, (p.id, tag))
        if row is None:
            s.add(
                PipelineTag(
                    pipeline_id=p.id,
                    project_id=p.project_id,
                    tag=tag,
                    version=version,
                    updated_by=actor.id,
                )
            )
        else:
            row.version, row.updated_by = version, actor.id
        await s.flush()

    def _check_models(
        self, project_id: uuid.UUID, cfg: PipelineConfig
    ) -> tuple[str, int, str | None]:
        """Validate the models; returns the concrete embedding model, its dimensions and its
        tokenizer."""
        self._gateway.registry.resolve(project_id, cfg.llm, CHAT_KINDS)
        embed = self._gateway.registry.resolve(project_id, cfg.embedding_model, EMBED_KINDS)
        model = embed.chain[0]
        if not model.embedding_dims:
            raise ProblemError(
                422, "embedding_dims_unknown", "The embedding model has no dimensions"
            )
        return model.name, model.embedding_dims, model.tokenizer

    async def _knowledge_base(
        self, s: AsyncSession, actor: Actor, project_id: uuid.UUID, cfg: PipelineConfig
    ) -> tuple[KnowledgeBase, bool]:
        """Find or create the knowledge base for the pipeline's index parameters."""
        if await s.get(DataCollection, cfg.collection_id) is None:
            raise ProblemError(404, "collection_not_found", "Data collection not found")
        model, dims, tokenizer = self._check_models(project_id, cfg)
        stmt = select(KnowledgeBase).where(
            KnowledgeBase.collection_id == cfg.collection_id,
            func.lower(KnowledgeBase.embedding_model) == model.lower(),
            KnowledgeBase.chunk_size == cfg.chunk_size,
            KnowledgeBase.chunk_overlap == cfg.chunk_overlap,
            KnowledgeBase.node_parser == cfg.node_parser,
        )
        kb = (await s.execute(stmt)).scalar_one_or_none()
        if kb is not None:
            if kb.dims != dims:
                raise ProblemError(
                    409,
                    "embedding_dimension_changed",
                    "This embedding model now has a different dimension than its index",
                )
            return kb, False
        kb = KnowledgeBase(
            project_id=project_id,
            collection_id=cfg.collection_id,
            embedding_model=model,
            dims=dims,
            chunk_size=cfg.chunk_size,
            chunk_overlap=cfg.chunk_overlap,
            node_parser=cfg.node_parser,
            tokenizer=tokenizer,
            created_by=actor.id,
        )
        s.add(kb)
        await s.flush()
        await s.execute(text("SELECT rag_kb_index(:kb)"), {"kb": kb.id})
        return kb, True

    async def _add_version(
        self, s: AsyncSession, actor: Actor, p: Pipeline, cfg: PipelineConfig, note: str
    ) -> tuple[PipelineVersion, KnowledgeBase | None]:
        if cfg.prompt_id:
            await self._prompts.for_use(actor, p.project_id, cfg.prompt_id)  # exists, readable
        kb, created = await self._knowledge_base(s, actor, p.project_id, cfg)
        p.latest_version += 1
        p.updated_by = actor.id
        v = PipelineVersion(
            project_id=p.project_id,
            pipeline_id=p.id,
            version=p.latest_version,
            config=cfg.model_dump(mode="json"),
            kb_id=kb.id,
            note=note,
            created_by=actor.id,
        )
        s.add(v)
        await s.flush()
        await self._move_tag(s, actor, p, "draft", v.version)
        return v, kb if created else None

    # ------------------------------------------------------------ library
    async def list_pipelines(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        *,
        status: str | None = None,
        q: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> PipelinePage:
        limit = max(1, min(limit, 200))
        published = exists().where(
            PipelineTag.pipeline_id == Pipeline.id, PipelineTag.tag == "prod"
        )
        stmt = (
            select(Pipeline, PipelineVersion.config, DataCollection.name)
            .join(
                PipelineVersion,
                and_(
                    PipelineVersion.pipeline_id == Pipeline.id,
                    PipelineVersion.version == Pipeline.latest_version,
                ),
            )
            .join(KnowledgeBase, KnowledgeBase.id == PipelineVersion.kb_id)
            .join(DataCollection, DataCollection.id == KnowledgeBase.collection_id)
            .order_by(Pipeline.updated_at.desc(), Pipeline.id.desc())
            .limit(limit + 1)
        )
        if status == "published":
            stmt = stmt.where(published)
        elif status == "draft":
            stmt = stmt.where(~published)
        if q:
            pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            stmt = stmt.where(Pipeline.name.ilike(pattern, escape="\\"))
        if since:
            stmt = stmt.where(Pipeline.updated_at >= since)
        if until:
            stmt = stmt.where(Pipeline.updated_at < until)
        if cursor:
            at, pid = _uncursor(cursor)
            stmt = stmt.where(
                or_(Pipeline.updated_at < at, and_(Pipeline.updated_at == at, Pipeline.id < pid))
            )
        async with self._session(actor, project_id) as s:
            rows = (await s.execute(stmt)).all()
            page = rows[:limit]
            tags = await self._tags(s, [p.id for p, _, _ in page])
        items = [self._out(p, tags[p.id], cfg, coll) for p, cfg, coll in page]
        nxt = _cursor(page[-1][0].updated_at, page[-1][0].id) if len(rows) > limit else None
        return PipelinePage(items=items, next_cursor=nxt)

    async def get(
        self, actor: Actor, project_id: uuid.UUID, pipeline_id: uuid.UUID
    ) -> PipelineDetail:
        async with self._session(actor, project_id) as s:
            return await self._detail(s, await self._pipeline(s, pipeline_id))

    async def _insert(self, s: AsyncSession, p: Pipeline) -> None:
        s.add(p)
        try:
            await s.flush()
        except IntegrityError as exc:
            raise ProblemError(409, "name_taken", "A RAG pipeline with this name exists") from exc

    async def _name_free(
        self, s: AsyncSession, name: str, exclude: uuid.UUID | None = None
    ) -> None:
        stmt = select(Pipeline.id).where(func.lower(Pipeline.name) == name.lower())
        if exclude:
            stmt = stmt.where(Pipeline.id != exclude)
        if (await s.execute(stmt)).first():
            raise ProblemError(409, "name_taken", f"A RAG pipeline named '{name}' already exists")

    async def create(
        self, actor: Actor, project_id: uuid.UUID, body: PipelineCreateIn
    ) -> PipelineDetail:
        async with self._session(actor, project_id) as s:
            await self._name_free(s, body.name)
            p = Pipeline(
                project_id=project_id,
                name=body.name,
                description=body.description,
                created_by=actor.id,
                updated_by=actor.id,
            )
            await self._insert(s, p)
            v, new_kb = await self._add_version(s, actor, p, body.config, "")
            await audit.record(
                s,
                actor,
                "rag_pipeline.create",
                "rag_pipeline",
                p.id,
                {"name": body.name, "kb_id": str(v.kb_id)},
                project_id=project_id,
            )
            detail = await self._detail(s, p)
        if new_kb:
            await self._starter.knowledge_base(project_id, new_kb.id)
        return detail

    async def save_version(
        self, actor: Actor, project_id: uuid.UUID, pipeline_id: uuid.UUID, body: PipelineVersionIn
    ) -> PipelineDetail:
        async with self._session(actor, project_id) as s:
            p = await self._pipeline(s, pipeline_id, lock=True)
            v, new_kb = await self._add_version(s, actor, p, body.config, body.note)
            await audit.record(
                s,
                actor,
                "rag_pipeline.version",
                "rag_pipeline",
                p.id,
                {"version": v.version, "kb_id": str(v.kb_id)},
                project_id=project_id,
            )
            detail = await self._detail(s, p)
        if new_kb:
            await self._starter.knowledge_base(project_id, new_kb.id)
        return detail

    async def update(
        self, actor: Actor, project_id: uuid.UUID, pipeline_id: uuid.UUID, body: PipelineUpdateIn
    ) -> PipelineDetail:
        async with self._session(actor, project_id) as s:
            p = await self._pipeline(s, pipeline_id, lock=True)
            diff: dict[str, Any] = {}
            if body.name is not None and body.name != p.name:
                await self._name_free(s, body.name, exclude=p.id)
                diff["name"] = [p.name, body.name]
                p.name = body.name
            if body.description is not None and body.description != p.description:
                diff["description"] = "changed"
                p.description = body.description
            if diff:
                p.updated_by = actor.id
                await audit.record(
                    s,
                    actor,
                    "rag_pipeline.update",
                    "rag_pipeline",
                    p.id,
                    diff,
                    project_id=project_id,
                )
                await s.flush()
            return await self._detail(s, p)

    async def delete(self, actor: Actor, project_id: uuid.UUID, pipeline_id: uuid.UUID) -> None:
        """Blocked while an agent uses the pipeline (US-1.11.2 #3); chunks stay with the KB."""
        async with self._session(actor, project_id) as s:
            p = await self._pipeline(s, pipeline_id, lock=True)
            refs: list[ReferenceOut] = []
            for check in self._reference_checks:
                refs += await check(s, p.id)
            if refs:
                raise ProblemError(
                    409,
                    "pipeline_in_use",
                    "The pipeline is used by other resources",
                    [r.model_dump(mode="json") for r in refs],
                )
            await audit.record(
                s,
                actor,
                "rag_pipeline.delete",
                "rag_pipeline",
                p.id,
                {"name": p.name},
                project_id=project_id,
            )
            await s.delete(p)

    async def duplicate(
        self, actor: Actor, project_id: uuid.UUID, pipeline_id: uuid.UUID, name: str
    ) -> PipelineDetail:
        async with self._session(actor, project_id) as s:
            src = await self._pipeline(s, pipeline_id)
            v = await self._version(s, src, None)
            await self._name_free(s, name)
            p = Pipeline(
                project_id=project_id,
                name=name,
                description=src.description,
                created_by=actor.id,
                updated_by=actor.id,
            )
            await self._insert(s, p)
            await self._add_version(
                s,
                actor,
                p,
                PipelineConfig.model_validate(v.config),
                f"Duplicated from {src.name} v{v.version}",
            )
            await audit.record(
                s,
                actor,
                "rag_pipeline.duplicate",
                "rag_pipeline",
                p.id,
                {"source": str(src.id), "name": name},
                project_id=project_id,
            )
            return await self._detail(s, p)

    async def versions(
        self, actor: Actor, project_id: uuid.UUID, pipeline_id: uuid.UUID
    ) -> list[PipelineVersionOut]:
        async with self._session(actor, project_id) as s:
            p = await self._pipeline(s, pipeline_id)
            tags = (await self._tags(s, [p.id]))[p.id]
            rows = (
                await s.execute(
                    select(PipelineVersion, KnowledgeBase)
                    .join(KnowledgeBase, KnowledgeBase.id == PipelineVersion.kb_id, isouter=True)
                    .where(PipelineVersion.pipeline_id == p.id)
                    .order_by(PipelineVersion.version.desc())
                )
            ).all()
            return [self._version_out(v, tags, kb) for v, kb in rows]

    async def set_tag(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        pipeline_id: uuid.UUID,
        tag: str,
        number: int | None,
    ) -> PipelineDetail:
        async with self._session(actor, project_id) as s:
            p = await self._pipeline(s, pipeline_id, lock=True)
            v = await self._version(s, p, number)
            await self._move_tag(s, actor, p, tag, v.version)
            p.updated_by, p.updated_at = actor.id, utcnow()
            action = "rag_pipeline.publish" if tag == "prod" else "rag_pipeline.tag"
            await audit.record(
                s,
                actor,
                action,
                "rag_pipeline",
                p.id,
                {"tag": tag, "version": v.version},
                project_id=project_id,
            )
            await s.flush()
            return await self._detail(s, p)

    async def remove_tag(
        self, actor: Actor, project_id: uuid.UUID, pipeline_id: uuid.UUID, tag: str
    ) -> PipelineDetail:
        async with self._session(actor, project_id) as s:
            p = await self._pipeline(s, pipeline_id, lock=True)
            row = await s.get(PipelineTag, (p.id, tag))
            if row is not None:
                await s.delete(row)
                await audit.record(
                    s,
                    actor,
                    "rag_pipeline.untag",
                    "rag_pipeline",
                    p.id,
                    {"tag": tag},
                    project_id=project_id,
                )
                await s.flush()
            return await self._detail(s, p)

    async def prompt_references(self, s: AsyncSession, prompt_id: uuid.UUID) -> list[ReferenceOut]:
        """Pipelines whose latest or tagged version uses the prompt (for prompt delete)."""
        tagged = select(PipelineTag.version).where(PipelineTag.pipeline_id == Pipeline.id)
        rows = (
            await s.execute(
                select(Pipeline.id, Pipeline.name)
                .join(PipelineVersion, PipelineVersion.pipeline_id == Pipeline.id)
                .where(
                    PipelineVersion.config["prompt_id"].astext == str(prompt_id),
                    or_(
                        PipelineVersion.version == Pipeline.latest_version,
                        PipelineVersion.version.in_(tagged.scalar_subquery()),
                    ),
                )
                .distinct()
            )
        ).all()
        return [ReferenceOut(kind="rag_pipeline", id=i, name=n) for i, n in rows]

    # ------------------------------------------------------------ export / import (bundles)
    async def export_pipeline(
        self, actor: Actor, project_id: uuid.UUID, pipeline_id: uuid.UUID, scope: str = "all"
    ) -> tuple[dict[str, Any], set[uuid.UUID], set[uuid.UUID]]:
        """The pipeline with the chosen versions; collections and prompts are referenced by
        name. Returns the item and the ids of the collections and prompts it depends on."""
        async with self._session(actor, project_id) as s:
            p = await self._pipeline(s, pipeline_id)
            tags = (await self._tags(s, [p.id]))[p.id]
            rows = list(
                (
                    await s.execute(
                        select(PipelineVersion)
                        .where(PipelineVersion.pipeline_id == p.id)
                        .order_by(PipelineVersion.version)
                    )
                ).scalars()
            )
            if scope == "published":
                rows = [r for r in rows if r.version == tags.get("prod")] or rows[-1:]
            elif scope == "latest":
                rows = rows[-1:]
            names = {c.id: c.name for c in (await s.execute(select(DataCollection))).scalars()}
        collections: set[uuid.UUID] = set()
        prompts: set[uuid.UUID] = set()
        versions = []
        for r in rows:
            cfg = dict(r.config)
            cid = uuid.UUID(cfg.pop("collection_id"))
            collections.add(cid)
            cfg["collection"] = names.get(cid)
            prompt_id = cfg.pop("prompt_id", None)
            cfg["prompt"] = None
            if prompt_id:
                prompts.add(uuid.UUID(prompt_id))
                cfg["prompt_ref"] = prompt_id  # replaced by the prompt's name in the bundle
            versions.append({"version": r.version, "config": cfg, "note": r.note})
        numbers = {r.version for r in rows}
        item = {
            "kind": "rag_pipeline",
            "name": p.name,
            "description": p.description,
            "versions": versions,
            "tags": {t: v for t, v in tags.items() if v in numbers and t != "draft"},
        }
        return item, collections, prompts

    async def export_collection(
        self, actor: Actor, project_id: uuid.UUID, collection_id: uuid.UUID
    ) -> dict[str, Any]:
        """The collection's definition only: files are data, not configuration."""
        async with self._session(actor, project_id) as s:
            c = await s.get(DataCollection, collection_id)
            if c is None:
                raise ProblemError(404, "not_found", "Collection not found")
            return {"kind": "data_collection", "name": c.name, "description": c.description}

    async def import_collection(
        self, actor: Actor, project_id: uuid.UUID, item: dict[str, Any], *, dry_run: bool = False
    ) -> dict[str, Any]:
        """Collections are matched by name; an existing one is reused, never duplicated."""
        name = str(item["name"])[:120]
        async with self._session(actor, project_id) as s:
            found = (
                await s.execute(
                    select(DataCollection.id).where(func.lower(DataCollection.name) == name.lower())
                )
            ).scalar_one_or_none()
            if found is not None:
                return {
                    "kind": "data_collection",
                    "name": name,
                    "action": "reused",
                    "id": str(found),
                    "warnings": [],
                }
            if dry_run:
                return {
                    "kind": "data_collection",
                    "name": name,
                    "action": "created",
                    "warnings": ["empty: upload its files after the import"],
                }
            c = DataCollection(
                project_id=project_id,
                name=name,
                description=str(item.get("description") or "")[:2000],
                created_by=actor.id,
                updated_by=actor.id,
            )
            s.add(c)
            await s.flush()
            await audit.record(
                s,
                actor,
                "collection.import",
                "data_collection",
                c.id,
                {"name": name},
                project_id=project_id,
            )
            return {
                "kind": "data_collection",
                "name": name,
                "action": "created",
                "id": str(c.id),
                "warnings": ["empty: upload its files after the import"],
            }

    async def import_pipeline(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        item: dict[str, Any],
        policy: str,
        *,
        collections: dict[str, uuid.UUID],
        prompts: dict[str, uuid.UUID],
        dry_run: bool = False,
        source: str = "bundle",
        assume_models: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        """Same policies as prompts. Collections and prompts are resolved by name in this
        project; models must exist here (they are checked like on save)."""
        name = str(item["name"])
        result: dict[str, Any] = {
            "kind": "rag_pipeline",
            "name": name,
            "versions": 0,
            "warnings": [],
        }
        configs: list[PipelineConfig] = []
        for v in item.get("versions", []):
            raw = {
                k: val
                for k, val in dict(v.get("config") or {}).items()
                if k in PipelineConfig.model_fields or k in ("collection", "prompt")
            }
            collection = raw.pop("collection", None)
            prompt = raw.pop("prompt", None)
            if not collection or collection.lower() not in collections:
                return result | {
                    "action": "error",
                    "error": f"collection '{collection}' not in this project",
                }
            raw["collection_id"] = str(collections[collection.lower()])
            if prompt:
                if prompt.lower() not in prompts:
                    return result | {"action": "error", "error": f"prompt '{prompt}' not found"}
                raw["prompt_id"] = str(prompts[prompt.lower()])
            try:
                cfg = PipelineConfig.model_validate(raw)
                if not {cfg.llm.lower(), cfg.embedding_model.lower()} <= assume_models:
                    self._check_models(project_id, cfg)
            except ProblemError as exc:
                return result | {"action": "error", "error": f"{exc.code}: {exc.title}"}
            except ValueError as exc:
                return result | {"action": "error", "error": str(exc)[:300]}
            configs.append(cfg)
        if not configs:
            return result | {"action": "error", "error": "no versions in the bundle"}
        async with self._session(actor, project_id) as s:
            existing = (
                await s.execute(select(Pipeline).where(func.lower(Pipeline.name) == name.lower()))
            ).scalar_one_or_none()
        target, action = name, "created"
        if existing is not None:
            if policy == "skip":
                return result | {"action": "skipped", "target": name}
            if policy == "rename":
                target, action = await self._free_copy_name(actor, project_id, name), "renamed"
            else:
                action = "new_versions"
        result |= {"action": action, "target": target, "versions": len(configs)}
        if dry_run:
            return result
        new_kbs: list[uuid.UUID] = []
        async with self._session(actor, project_id) as s:
            if action == "new_versions":
                assert existing is not None
                p = await self._pipeline(s, existing.id, lock=True)
            else:
                p = Pipeline(
                    project_id=project_id,
                    name=target,
                    description=str(item.get("description") or "Imported")[:2000],
                    created_by=actor.id,
                    updated_by=actor.id,
                )
                await self._insert(s, p)
            mapping: dict[int, int] = {}
            for v, cfg in zip(item["versions"], configs, strict=True):
                note = str(v.get("note") or "") + f" (imported from {source} v{v.get('version')})"
                row, kb = await self._add_version(s, actor, p, cfg, note.strip()[:500])
                mapping[int(v.get("version", 0))] = row.version
                if kb is not None:
                    new_kbs.append(kb.id)
            if action != "new_versions":
                for tag, old in (item.get("tags") or {}).items():
                    if tag in ("staging", "prod") and old in mapping:
                        await self._move_tag(s, actor, p, tag, mapping[old])
            elif item.get("tags"):
                result["warnings"].append("tags not applied to an existing pipeline; publish it")
            await audit.record(
                s,
                actor,
                "rag_pipeline.import",
                "rag_pipeline",
                p.id,
                {"source": source, "versions": sorted(mapping.values()), "action": action},
                project_id=project_id,
            )
            result["id"] = str(p.id)
        for kb_id in new_kbs:
            await self._starter.knowledge_base(project_id, kb_id)
        return result

    async def _free_copy_name(self, actor: Actor, project_id: uuid.UUID, name: str) -> str:
        async with self._session(actor, project_id) as s:
            taken = {n.lower() for n in (await s.execute(select(Pipeline.name))).scalars()}
        for n in range(1, 1000):
            candidate = f"{name} (imported{'' if n == 1 else f' {n}'})"[:120]
            if candidate.lower() not in taken:
                return candidate
        raise ProblemError(409, "name_taken", "No free name for the imported pipeline")

    async def find_pipeline(
        self, actor: Actor, project_id: uuid.UUID, name: str
    ) -> uuid.UUID | None:
        async with self._session(actor, project_id) as s:
            return (
                await s.execute(
                    select(Pipeline.id).where(func.lower(Pipeline.name) == name.lower())
                )
            ).scalar_one_or_none()

    async def collection_ids(self, actor: Actor, project_id: uuid.UUID) -> dict[str, uuid.UUID]:
        async with self._session(actor, project_id) as s:
            return {
                n.lower(): i
                for i, n in (await s.execute(select(DataCollection.id, DataCollection.name))).all()
            }

    # ------------------------------------------------------------ query
    async def _serving_kb(
        self, s: AsyncSession, p: Pipeline, v: PipelineVersion
    ) -> tuple[KnowledgeBase, str | None]:
        kb = await s.get(KnowledgeBase, v.kb_id)
        if kb is None:
            raise ProblemError(409, "knowledge_base_missing", "The knowledge base was deleted")
        if kb.status == "ready":
            return kb, None
        older = (
            await s.execute(
                select(KnowledgeBase)
                .join(PipelineVersion, PipelineVersion.kb_id == KnowledgeBase.id)
                .where(
                    PipelineVersion.pipeline_id == p.id,
                    PipelineVersion.version < v.version,
                    KnowledgeBase.status == "ready",
                )
                .order_by(PipelineVersion.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if older is not None:
            return older, f"The new index is still {kb.status}; using the previous ready index"
        return kb, f"The index is still {kb.status}; results may be incomplete"

    async def query(
        self, actor: Actor, project_id: uuid.UUID, pipeline_id: uuid.UUID, body: QueryIn
    ) -> RagRunDetail:
        started = time.perf_counter()
        async with self._session(actor, project_id) as s:
            p = await self._pipeline(s, pipeline_id)
            v = await self._version(s, p, body.version)
            kb, warning = await self._serving_kb(s, p, v)
            cfg = PipelineConfig.model_validate(v.config)
            kb_snapshot = (kb.id, kb.embedding_model, kb.dims)
        groups = await self._groups(project_id, actor.id)
        run_id = uuid7()
        caller = Caller(project_id=project_id, user_id=actor.id, trace_id=f"rag_run:{run_id}")
        run = RagRun(
            id=run_id,
            project_id=project_id,
            pipeline_id=p.id,
            version=v.version,
            kb_id=kb_snapshot[0],
            query=body.query,
            created_by=actor.id,
        )
        try:
            system, user_template = await self._prompt_parts(actor, project_id, cfg)
            # The LLM's tokenizer measures the context budget (loading it may read files).
            llm_counter = await asyncio.to_thread(
                self._gateway.token_counter, project_id, cfg.llm, CHAT_KINDS
            )
            retrieval = await self._retriever.retrieve(
                caller,
                RetrievalParams(
                    kb_id=kb_snapshot[0],
                    embedding_model=kb_snapshot[1],
                    dims=kb_snapshot[2],
                    method=cfg.search_method,
                    reranker=cfg.reranker,
                    top_k=cfg.top_k,
                    candidates=cfg.candidates,
                    threshold=cfg.similarity_threshold,
                    context_budget=cfg.context_budget,
                ),
                body.query,
                groups,
                llm_counter.count,
            )
            retrieval.trace["context_tokens_exact"] = llm_counter.exact
            if warning:
                retrieval.trace["warning"] = warning
            synth = await self._synth.answer(
                caller,
                model=cfg.llm,
                query=body.query,
                chunks=retrieval.chunks,
                mode=cfg.response_mode,
                temperature=cfg.temperature,
                system=system,
                user_template=user_template,
            )
        except ProblemError as exc:
            run.status, run.error_code = "error", exc.code
            run.error = (exc.detail if isinstance(exc.detail, str) else exc.title)[:2000]
            run.latency_ms = int((time.perf_counter() - started) * 1000)
            async with self._session(actor, project_id) as s:
                s.add(run)
            return self._run_out(run, detail=True)  # type: ignore[return-value]
        chunks = retrieval.chunks
        numbers = synth.cited or list(range(1, len(chunks) + 1))
        run.citations = (
            [
                Citation(
                    index=n,
                    chunk_id=chunks[n - 1].id,
                    document_id=chunks[n - 1].document_id,
                    document_name=chunks[n - 1].document_name,
                    page=chunks[n - 1].page,
                    heading_path=chunks[n - 1].heading_path,
                    score=round(chunks[n - 1].score, 4),
                    excerpt=chunks[n - 1].content[:500],
                    url=(
                        f"/api/v1/projects/{project_id}/collections/{chunks[n - 1].collection_id}"
                        f"/documents/{chunks[n - 1].document_id}/content"
                        + (f"#page={chunks[n - 1].page}" if chunks[n - 1].page else "")
                    ),
                ).model_dump(mode="json")
                for n in numbers
            ]
            if chunks
            else []
        )
        run.status = "ok" if chunks else "no_context"
        run.answer = synth.answer
        run.retrieval = retrieval.trace | {
            "cited_from_answer": bool(synth.cited),
            "synthesis_calls": synth.calls,
            "response_mode": cfg.response_mode,
            # What retrieval returned, in rank order (evaluations measure recall@k on it).
            "retrieved": [
                {
                    "chunk_id": str(c.id),
                    "document_id": str(c.document_id),
                    "document_name": c.document_name,
                    "score": round(c.score, 4),
                }
                for c in chunks
            ],
        }
        run.messages = synth.messages
        run.model_name = synth.model_name
        run.tokens_in = synth.tokens_in + retrieval.tokens
        run.tokens_out = synth.tokens_out
        run.cost = synth.cost + retrieval.cost
        run.latency_ms = int((time.perf_counter() - started) * 1000)
        async with self._session(actor, project_id) as s:
            s.add(run)
        return self._run_out(run, detail=True)  # type: ignore[return-value]

    async def agent_pipeline(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        pipeline_id: uuid.UUID,
        version: int | None = None,
    ) -> AgentPipeline:
        """Resolve the version an agent pins: the given one, else `prod`, else the latest."""
        async with self._session(actor, project_id) as s:
            p = await self._pipeline(s, pipeline_id)
            if version is None:
                tag = await s.get(PipelineTag, (p.id, "prod"))
                version = tag.version if tag else p.latest_version
            v = await self._version(s, p, version)
            kb = await s.get(KnowledgeBase, v.kb_id)
            coll = await s.get(DataCollection, kb.collection_id) if kb else None
            name = coll.name if coll else None
            return AgentPipeline(p.id, p.name, p.description, v.version, name)

    async def collection_search(
        self,
        caller: Caller,
        collection_id: uuid.UUID,
        query: str,
        top_k: int = 5,
    ) -> AgentSearch:
        """Retrieval over a whole data collection, for MCP clients (E3.5): the newest ready
        knowledge base, hybrid search, no reranker. Readers are matched against document ACLs
        as `caller.user_id` says; None sees project-wide documents only (API keys)."""
        actor = Actor(id=caller.user_id, label="mcp")
        project_id = caller.project_id
        async with self._session(actor, project_id) as s:
            coll = await s.get(DataCollection, collection_id)
            if coll is None:
                raise ProblemError(404, "not_found", "Data collection not found")
            kb = (
                await s.execute(
                    select(KnowledgeBase)
                    .where(
                        KnowledgeBase.collection_id == collection_id,
                        KnowledgeBase.status == "ready",
                    )
                    .order_by(KnowledgeBase.ready_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if kb is None:
                raise ProblemError(409, "no_ready_index", "The collection has no ready index yet")
            kb_snapshot = (kb.id, kb.embedding_model, kb.dims)
        groups = await self._groups(project_id, caller.user_id)
        retrieval = await self._retriever.retrieve(
            caller,
            RetrievalParams(
                kb_id=kb_snapshot[0],
                embedding_model=kb_snapshot[1],
                dims=kb_snapshot[2],
                method="hybrid",
                reranker=None,
                top_k=top_k,
                candidates=max(20, top_k * 4),
                threshold=0.0,
                context_budget=6000,
            ),
            query,
            groups,
            None,
        )
        return AgentSearch(
            passages=[
                AgentPassage(
                    chunk_id=c.id,
                    document_id=c.document_id,
                    document_name=c.document_name,
                    page=c.page,
                    heading_path=c.heading_path,
                    score=round(c.score, 4),
                    content=c.content,
                    url=(
                        f"/api/v1/projects/{project_id}/collections/{c.collection_id}"
                        f"/documents/{c.document_id}/content"
                        + (f"#page={c.page}" if c.page else "")
                    ),
                )
                for c in retrieval.chunks
            ],
            trace=retrieval.trace,
            tokens=retrieval.tokens,
            cost=retrieval.cost,
        )

    async def agent_search(
        self,
        caller: Caller,
        pipeline_id: uuid.UUID,
        version: int,
        query: str,
    ) -> AgentSearch:
        """Retrieval only, for an agent's retriever tool: the pinned pipeline version, the
        caller's groups and roles (AC4), no synthesis (the agent writes the answer)."""
        actor = Actor(id=caller.user_id, label="agent")
        project_id = caller.project_id
        async with self._session(actor, project_id) as s:
            p = await self._pipeline(s, pipeline_id)
            v = await self._version(s, p, version)
            kb, warning = await self._serving_kb(s, p, v)
            cfg = PipelineConfig.model_validate(v.config)
            kb_snapshot = (kb.id, kb.embedding_model, kb.dims)
        groups = await self._groups(project_id, caller.user_id)
        llm_counter = await asyncio.to_thread(
            self._gateway.token_counter, project_id, cfg.llm, CHAT_KINDS
        )
        retrieval = await self._retriever.retrieve(
            caller,
            RetrievalParams(
                kb_id=kb_snapshot[0],
                embedding_model=kb_snapshot[1],
                dims=kb_snapshot[2],
                method=cfg.search_method,
                reranker=cfg.reranker,
                top_k=cfg.top_k,
                candidates=cfg.candidates,
                threshold=cfg.similarity_threshold,
                context_budget=cfg.context_budget,
            ),
            query,
            groups,
            llm_counter.count,
        )
        if warning:
            retrieval.trace["warning"] = warning
        return AgentSearch(
            passages=[
                AgentPassage(
                    chunk_id=c.id,
                    document_id=c.document_id,
                    document_name=c.document_name,
                    page=c.page,
                    heading_path=c.heading_path,
                    score=round(c.score, 4),
                    content=c.content,
                    url=(
                        f"/api/v1/projects/{project_id}/collections/{c.collection_id}"
                        f"/documents/{c.document_id}/content"
                        + (f"#page={c.page}" if c.page else "")
                    ),
                )
                for c in retrieval.chunks
            ],
            trace=retrieval.trace,
            tokens=retrieval.tokens,
            cost=retrieval.cost,
        )

    async def _prompt_parts(
        self, actor: Actor, project_id: uuid.UUID, cfg: PipelineConfig
    ) -> tuple[str | None, Callable[[str, str], str] | None]:
        """The Library prompt, if chosen: its system prompt replaces the default instructions;
        its user prompt, if any, receives `context` and `question`."""
        if not cfg.prompt_id:
            return None, None
        _, v = await self._prompts.for_use(actor, project_id, cfg.prompt_id)

        def values(ctx: str, question: str) -> dict[str, str]:
            return {var.name: var.value for var in v.variables} | {
                "context": ctx,
                "question": question,
                "query": question,
            }

        try:
            system = (
                render(v.system_template, values("", ""), limit_values=False)
                if v.system_template
                else None
            )
        except TemplateError as exc:
            raise ProblemError(
                422,
                "prompt_render_failed",
                "The selected prompt could not be rendered",
                exc.message,
            ) from exc
        if not v.user_template.strip():
            return system, None

        def user(ctx: str, question: str) -> str:
            try:
                rendered = render(v.user_template, values(ctx, question), limit_values=False)
            except TemplateError as exc:
                raise ProblemError(
                    422,
                    "prompt_render_failed",
                    "The selected prompt could not be rendered",
                    exc.message,
                ) from exc
            return rendered if "<context>" in rendered else f"{ctx}\n\n{rendered}"

        return system, user

    @staticmethod
    def _run_out(run: RagRun, detail: bool = False) -> RagRunOut:
        base: dict[str, Any] = {
            "id": run.id,
            "pipeline_id": run.pipeline_id,
            "version": run.version,
            "query": run.query,
            "status": run.status,
            "error_code": run.error_code,
            "error": run.error,
            "answer": run.answer,
            "citations": run.citations,
            "model_name": run.model_name,
            "tokens_in": run.tokens_in,
            "tokens_out": run.tokens_out,
            "total_tokens": run.tokens_in + run.tokens_out,
            "cost": run.cost or Decimal(0),
            "latency_ms": run.latency_ms,
            "created_at": run.created_at or utcnow(),
        }
        if not detail:
            return RagRunOut(**base)
        return RagRunDetail(**base, retrieval=run.retrieval or {}, messages=run.messages or [])

    async def runs(
        self, actor: Actor, project_id: uuid.UUID, pipeline_id: uuid.UUID, limit: int = 20
    ) -> list[RagRunOut]:
        async with self._session(actor, project_id) as s:
            await self._pipeline(s, pipeline_id)
            rows = await s.execute(
                select(RagRun)
                .where(RagRun.pipeline_id == pipeline_id)
                .order_by(RagRun.created_at.desc())
                .limit(max(1, min(limit, 200)))
            )
            return [self._run_out(r) for r in rows.scalars()]

    async def run(self, actor: Actor, project_id: uuid.UUID, run_id: uuid.UUID) -> RagRunDetail:
        async with self._session(actor, project_id) as s:
            row = await s.get(RagRun, run_id)
            if row is None:
                raise ProblemError(404, "not_found", "Run not found")
            return self._run_out(row, detail=True)  # type: ignore[return-value]
