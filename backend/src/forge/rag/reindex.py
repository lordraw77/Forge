"""Rebuild the BM25 index from PostgreSQL (disaster recovery, E3.7).

PostgreSQL is the source of truth for chunks. After restoring it to a point in time the search
index can hold chunks written after that moment or miss ones from before it: this deletes each
project's index entries and writes them again from `rag_chunk`. BM25 results are incomplete
for that project while its turn runs; vector search is not affected.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.core.db import admin_scope
from forge.rag.models import Chunk, KnowledgeBase
from forge.rag.search import SearchClient

BATCH = 500


async def reindex(
    sessions: async_sessionmaker[AsyncSession],
    search: SearchClient,
    project_id: uuid.UUID | None = None,
    progress: Callable[[uuid.UUID, int], None] | None = None,
) -> dict[uuid.UUID, int]:
    """Returns the number of chunks written per project."""
    await search.ensure_index()
    async with admin_scope(sessions) as s:
        q = select(KnowledgeBase.project_id).distinct()
        if project_id:
            q = q.where(KnowledgeBase.project_id == project_id)
        projects = list((await s.execute(q)).scalars())
    done: dict[uuid.UUID, int] = {}
    for pid in projects:
        async with admin_scope(sessions) as s:
            kbs = list(
                (await s.execute(select(KnowledgeBase.id).where(KnowledgeBase.project_id == pid)))
                .scalars()
                .all()
            )
        await search.delete_where(pid, "kb_id", kbs)
        total, last = 0, None
        while True:
            async with admin_scope(sessions) as s:
                q2 = select(Chunk).where(Chunk.project_id == pid).order_by(Chunk.id).limit(BATCH)
                if last is not None:
                    q2 = q2.where(Chunk.id > last)
                rows = list((await s.execute(q2)).scalars())
            if not rows:
                break
            await search.index_chunks(
                pid,
                [
                    {
                        "id": c.id,
                        "project_id": str(pid),
                        "kb_id": str(c.kb_id),
                        "document_id": str(c.document_id),
                        "acl_groups": c.acl_groups,
                        "heading_path": c.heading_path,
                        "content": c.content,
                        "page": c.page,
                    }
                    for c in rows
                ],
            )
            total += len(rows)
            last = rows[-1].id
        done[pid] = total
        if progress:
            progress(pid, total)
    return done
