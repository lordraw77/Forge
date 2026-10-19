"""Retriever (E1.16, spec "RAG design" → Retrieval).

| Method   | Implementation                                                               |
| -------- | ---------------------------------------------------------------------------- |
| vector   | pgvector cosine, top_k directly                                              |
| semantic | pgvector top `candidates` → reranker → top_k                                 |
| hybrid   | pgvector + OpenSearch BM25 top `candidates`, concurrently → RRF (k = 60) →   |
|          | reranker → top_k                                                             |

Score used for the similarity threshold: the reranker's relevance (0 to 1) when a reranker ran,
otherwise the cosine similarity of the chunk to the query. Chunks are then trimmed by rank to
the context budget. ACL: the reader's groups plus project-wide chunks (US-1.16.2); the vector
query runs under RLS, the BM25 query through the single query builder in `search.py`.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.core.db import project_scope
from forge.core.errors import ProblemError
from forge.gateway.service import Caller, Gateway
from forge.rag.search import SearchClient, reader_groups

RRF_K = 60
EF_SEARCH = 80


@dataclass
class RetrievedChunk:
    id: uuid.UUID
    document_id: uuid.UUID
    collection_id: uuid.UUID
    document_name: str
    content: str
    heading_path: str
    page: int | None
    token_count: int
    cosine: float
    score: float
    ranks: dict[str, int] = field(default_factory=dict)


@dataclass
class Retrieval:
    chunks: list[RetrievedChunk]
    trace: dict[str, Any]
    cost: Decimal = Decimal(0)
    tokens: int = 0


@dataclass(frozen=True)
class RetrievalParams:
    kb_id: uuid.UUID
    embedding_model: str
    dims: int
    method: str
    reranker: str | None
    top_k: int
    candidates: int
    threshold: float
    context_budget: int


def rrf(*rankings: list[uuid.UUID], k: int = RRF_K) -> list[uuid.UUID]:
    scores: dict[uuid.UUID, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking, start=1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=scores.__getitem__, reverse=True)


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


class Retriever:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        search: SearchClient,
        gateway: Gateway,
    ) -> None:
        self._sessions = sessions
        self._search = search
        self._gateway = gateway

    async def _embed(
        self, caller: Caller, p: RetrievalParams, query: str
    ) -> tuple[list[float], Any]:
        response = await self._gateway.service.embeddings(caller, p.embedding_model, [query], {})
        vector: list[float] = response.body["data"][0]["embedding"]  # type: ignore[index]
        if len(vector) != p.dims:
            raise ProblemError(
                409,
                "embedding_dimension_changed",
                "The embedding model now returns another dimension; re-index the knowledge base",
                {"expected": p.dims, "got": len(vector)},
            )
        return vector, response.info

    async def _vector(
        self, project_id: uuid.UUID, p: RetrievalParams, qvec: str, groups: list[str], limit: int
    ) -> list[uuid.UUID]:
        dims = int(p.dims)  # interpolated: the cast must match the per-KB index expression
        sql = text(
            f"""
            SELECT id FROM rag_chunk
            WHERE kb_id = :kb AND acl_groups && CAST(:groups AS text[])
            ORDER BY (embedding::halfvec({dims})) <=> CAST(:q AS halfvec({dims}))
            LIMIT :limit
            """  # noqa: S608 - dims is an int from the knowledge base row
        )
        async with project_scope(self._sessions, project_id) as s:
            await s.execute(text(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}"))
            await s.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
            rows = await s.execute(
                sql, {"kb": p.kb_id, "groups": groups, "q": qvec, "limit": limit}
            )
            return [r[0] for r in rows.all()]

    async def _load(
        self,
        project_id: uuid.UUID,
        p: RetrievalParams,
        ids: list[uuid.UUID],
        qvec: str,
        groups: list[str],
    ) -> dict[uuid.UUID, RetrievedChunk]:
        if not ids:
            return {}
        dims = int(p.dims)
        sql = text(
            f"""
            SELECT c.id, c.document_id, d.collection_id, d.name, c.content, c.heading_path,
                   c.page, c.token_count,
                   1 - ((c.embedding::halfvec({dims})) <=> CAST(:q AS halfvec({dims}))) AS cosine
            FROM rag_chunk c JOIN rag_document d ON d.id = c.document_id
            WHERE c.id = ANY(:ids) AND c.kb_id = :kb AND c.acl_groups && CAST(:groups AS text[])
            """  # noqa: S608 - dims is an int from the knowledge base row
        )
        async with project_scope(self._sessions, project_id) as s:
            rows = await s.execute(sql, {"ids": ids, "kb": p.kb_id, "q": qvec, "groups": groups})
            return {
                r.id: RetrievedChunk(
                    id=r.id,
                    document_id=r.document_id,
                    collection_id=r.collection_id,
                    document_name=r.name,
                    content=r.content,
                    heading_path=r.heading_path,
                    page=r.page,
                    token_count=r.token_count,
                    cosine=float(r.cosine),
                    score=float(r.cosine),
                )
                for r in rows.all()
            }

    async def retrieve(
        self,
        caller: Caller,
        p: RetrievalParams,
        query: str,
        member_groups: list[str],
        count: Callable[[str], int] | None = None,
    ) -> Retrieval:
        """`count`: the LLM's token counter for the context budget (stored chunk counts, from the
        embedding tokenizer, otherwise)."""
        groups = reader_groups(member_groups)
        trace: dict[str, Any] = {
            "method": p.method,
            "kb_id": str(p.kb_id),
            "embedding_model": p.embedding_model,
            "acl_groups": groups,
            "candidates": p.candidates,
            "top_k": p.top_k,
            "threshold": p.threshold,
        }
        timings: dict[str, int] = {}
        cost = Decimal(0)
        used_tokens = 0
        t0 = time.perf_counter()
        vector, info = await self._embed(caller, p, query)
        if info:
            cost += info.cost
            used_tokens += info.tokens_in
        timings["embed_ms"] = int((time.perf_counter() - t0) * 1000)
        qvec = _vector_literal(vector)

        limit = p.top_k if p.method == "vector" else p.candidates
        t0 = time.perf_counter()
        if p.method == "hybrid":
            vec_ids, bm25 = await asyncio.gather(
                self._vector(caller.project_id, p, qvec, groups, limit),
                self._search.bm25(query, caller.project_id, [p.kb_id], member_groups, limit),
            )
            bm25_ids = [cid for cid, _ in bm25]
            fused = rrf(vec_ids, bm25_ids)[: p.candidates]
            trace["vector_hits"], trace["bm25_hits"] = len(vec_ids), len(bm25_ids)
        else:
            vec_ids = await self._vector(caller.project_id, p, qvec, groups, limit)
            bm25_ids = []
            fused = vec_ids
            trace["vector_hits"] = len(vec_ids)
        timings["search_ms"] = int((time.perf_counter() - t0) * 1000)

        loaded = await self._load(caller.project_id, p, fused, qvec, groups)
        chunks = [loaded[c] for c in fused if c in loaded]
        for c in chunks:
            if c.id in vec_ids:
                c.ranks["vector"] = vec_ids.index(c.id) + 1
            if c.id in bm25_ids:
                c.ranks["bm25"] = bm25_ids.index(c.id) + 1

        reranked = False
        if p.method != "vector" and p.reranker and chunks:
            t0 = time.perf_counter()
            try:
                result = await self._gateway.service.rerank(
                    caller, p.reranker, query, [c.content for c in chunks], None
                )
                for r in result["results"]:
                    chunks[int(r["index"])].score = float(r["relevance_score"])
                chunks.sort(key=lambda c: c.score, reverse=True)
                reranked = True
                trace["reranker"] = result["model"]
                if result.get("info"):
                    cost += result["info"].cost
                    used_tokens += result["info"].tokens_in
            except ProblemError as exc:
                # No reranker configured or reachable: fall back to cosine, say so in the trace.
                trace["rerank_skipped"] = f"{exc.code}: {exc.title}"
            timings["rerank_ms"] = int((time.perf_counter() - t0) * 1000)
        if not reranked:
            chunks.sort(key=lambda c: c.cosine, reverse=True)
        trace["score"] = "reranker" if reranked else "cosine"

        passed = [c for c in chunks if c.score >= p.threshold][: p.top_k]
        selected: list[RetrievedChunk] = []
        budget = 0
        for c in passed:
            size = count(c.content) if count else c.token_count
            if selected and budget + size > p.context_budget:
                break
            selected.append(c)
            budget += size
        trace.update(
            {
                "fused": len(chunks),
                "above_threshold": len([c for c in chunks if c.score >= p.threshold]),
                "selected": len(selected),
                "context_tokens": budget,
                "timings": timings,
                "ranking": [
                    {
                        "chunk_id": str(c.id),
                        "document": c.document_name,
                        "page": c.page,
                        "score": round(c.score, 4),
                        "cosine": round(c.cosine, 4),
                        "ranks": c.ranks,
                        "selected": c in selected,
                    }
                    for c in chunks[:20]
                ],
            }
        )
        return Retrieval(selected, trace, cost, used_tokens)
