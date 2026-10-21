"""Sync engine: bring a source's items into its collection, incrementally (E2.13).

One run lists the source once. An item whose version equals the one seen last time is not
fetched. A changed item is fetched and handed to the collection's normal upload path, which
skips identical content and re-ingests the rest. Items the source no longer lists are deleted
from the collection (propagated deletion), unless the listing failed or the deletion looks
like an accident (more than half of what was known, or an empty listing).
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import secrets
from forge.core.context import SYSTEM
from forge.core.crypto import Envelope
from forge.core.db import project_scope
from forge.core.logging import get_logger
from forge.core.models import utcnow
from forge.rag import sniff
from forge.rag.collections import CollectionService, UploadFile
from forge.rag.models import Source, SourceItem, SourceRun
from forge.rag.sources.connectors import MAX_ITEM_BYTES, SourceContext, SourceError, build

log = get_logger(__name__)

MAX_ERRORS = 50
KEEP_RUNS = 50
MASS_DELETE_RATIO = 0.5
STUCK_AFTER = timedelta(hours=2)

ContextFactory = Callable[[uuid.UUID, dict[str, Any]], Awaitable[SourceContext]]
Heartbeat = Callable[[], None]


@dataclass
class Counts:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)
    message: str | None = None

    def error(self, item: str, text: str) -> None:
        if len(self.errors) < MAX_ERRORS:
            self.errors.append({"item": item[:300], "error": text[:300]})


class SyncEngine:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        collections: CollectionService,
        envelope: Envelope,
        context: ContextFactory,
    ) -> None:
        self._sessions = sessions
        self._collections = collections
        self._envelope = envelope
        self._context = context

    async def run(
        self,
        project_id: uuid.UUID,
        source_id: uuid.UUID,
        trigger: str,
        *,
        allow_mass_delete: bool = False,
        heartbeat: Heartbeat | None = None,
    ) -> dict[str, Any]:
        beat = heartbeat or (lambda: None)
        async with project_scope(self._sessions, project_id) as s:
            src = await s.get(Source, source_id, with_for_update=True)
            if src is None:
                return {"status": "gone"}
            if src.status == "running" and utcnow() - src.updated_at < STUCK_AFTER:
                return {"status": "already_running"}
            run = SourceRun(project_id=project_id, source_id=source_id, trigger=trigger)
            s.add(run)
            src.status = "running"
            src.updated_at = utcnow()
            await s.flush()
            run_id = run.id
            kind, config, collection_id = src.type, dict(src.config), src.collection_id
            acl, delete_missing, secret_id = list(src.acl_groups), src.delete_missing, src.secret_id
            secret: dict[str, Any] = {}
            if secret_id is not None:
                import json

                secret = json.loads(await secrets.get(s, self._envelope, secret_id))
        counts = Counts()
        status = "success"
        connector = None
        try:
            ctx = await self._context(project_id, secret)
            connector = build(kind, config, ctx)
            await self._pull(
                connector, project_id, source_id, collection_id, run_id, acl, counts, beat
            )
            if delete_missing:
                await self._delete_missing(
                    project_id, source_id, collection_id, run_id, counts, allow_mass_delete
                )
            if counts.failed or counts.message:
                status = "partial"
        except SourceError as exc:
            status, counts.message = "failed", str(exc)
        except Exception as exc:  # unexpected: the run is recorded, the next one retries
            log.exception("rag.source_failed", source=str(source_id))
            status, counts.message = "failed", f"{type(exc).__name__}: {str(exc)[:200]}"
        finally:
            if connector is not None:
                await connector.close()
        await self._finish(project_id, source_id, run_id, status, counts)
        return {
            "status": status,
            "added": counts.added,
            "updated": counts.updated,
            "unchanged": counts.unchanged,
            "deleted": counts.deleted,
            "skipped": counts.skipped,
            "failed": counts.failed,
            "message": counts.message,
        }

    async def _pull(
        self,
        connector: Any,
        project_id: uuid.UUID,
        source_id: uuid.UUID,
        collection_id: uuid.UUID,
        run_id: uuid.UUID,
        acl: list[str],
        counts: Counts,
        beat: Heartbeat,
    ) -> None:
        async with project_scope(self._sessions, project_id) as s:
            known = {
                r.external_id: r.version
                for r in (
                    await s.execute(select(SourceItem).where(SourceItem.source_id == source_id))
                ).scalars()
            }
        seen: set[str] = set()
        async for item in connector.items():
            beat()
            if item.external_id in seen:
                continue
            seen.add(item.external_id)
            if known.get(item.external_id) == item.version:
                counts.unchanged += 1
                continue
            try:
                outcome = await self._store(
                    project_id, source_id, collection_id, item, acl, item.external_id in known
                )
            except sniff.UnsupportedFile as exc:
                counts.skipped += 1
                counts.error(item.external_id, f"skipped: {exc}")
                seen.discard(item.external_id)  # unsupported now, so not held in the collection
                continue
            except Exception as exc:
                counts.failed += 1
                counts.error(item.external_id, f"{type(exc).__name__}: {exc}")
                continue
            setattr(counts, outcome, getattr(counts, outcome) + 1)
        # Seen now: everything the listing returned that is held in the collection.
        async with project_scope(self._sessions, project_id) as s:
            await s.execute(
                update(SourceItem)
                .where(SourceItem.source_id == source_id, SourceItem.external_id.in_(seen))
                .values(seen_run=run_id)
            )

    async def _store(
        self,
        project_id: uuid.UUID,
        source_id: uuid.UUID,
        collection_id: uuid.UUID,
        item: Any,
        acl: list[str],
        existed: bool,
    ) -> str:
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as fh:
            await item.fetch(fh)
            size = fh.tell()
            if size == 0:
                raise sniff.UnsupportedFile("empty file")
            if size > MAX_ITEM_BYTES:
                raise sniff.UnsupportedFile("file exceeds 100 MB")
            fh.seek(0)
            [result] = await self._collections.upload(
                SYSTEM,
                project_id,
                collection_id,
                [UploadFile(filename=item.name, path=item.folder, fh=fh, size=size)],
                acl,
            )
        if result.status == "rejected":
            raise sniff.UnsupportedFile(result.error or "rejected")
        assert result.document is not None
        async with project_scope(self._sessions, project_id) as s:
            row = await s.get(SourceItem, (source_id, item.external_id))
            if row is None:
                s.add(
                    SourceItem(
                        source_id=source_id,
                        external_id=item.external_id,
                        project_id=project_id,
                        document_id=result.document.id,
                        version=item.version,
                    )
                )
            else:
                row.document_id, row.version = result.document.id, item.version
        if result.status == "unchanged":
            return "unchanged"
        return "updated" if existed else "added"

    async def _delete_missing(
        self,
        project_id: uuid.UUID,
        source_id: uuid.UUID,
        collection_id: uuid.UUID,
        run_id: uuid.UUID,
        counts: Counts,
        allow_mass_delete: bool,
    ) -> None:
        async with project_scope(self._sessions, project_id) as s:
            total = (
                await s.execute(
                    select(func.count())
                    .select_from(SourceItem)
                    .where(SourceItem.source_id == source_id)
                )
            ).scalar_one()
            stale = list(
                (
                    await s.execute(
                        select(SourceItem.external_id, SourceItem.document_id).where(
                            SourceItem.source_id == source_id,
                            SourceItem.seen_run.is_distinct_from(run_id),
                        )
                    )
                ).all()
            )
        if not stale:
            return
        if not allow_mass_delete and len(stale) / total > MASS_DELETE_RATIO:
            counts.message = (
                f"{len(stale)} of {total} documents are no longer in the source; nothing was "
                "deleted. Check the source, then run the sync again with deletion confirmed."
            )
            return
        for external_id, document_id in stale:
            try:
                await self._collections.delete_document(
                    SYSTEM, project_id, collection_id, document_id
                )
                counts.deleted += 1
            except Exception as exc:
                counts.failed += 1
                counts.error(external_id, f"delete failed: {exc}")
        async with project_scope(self._sessions, project_id) as s:
            await s.execute(
                delete(SourceItem).where(
                    SourceItem.source_id == source_id,
                    SourceItem.external_id.in_([e for e, _ in stale]),
                )
            )

    async def _finish(
        self,
        project_id: uuid.UUID,
        source_id: uuid.UUID,
        run_id: uuid.UUID,
        status: str,
        counts: Counts,
    ) -> None:
        async with project_scope(self._sessions, project_id) as s:
            run = await s.get(SourceRun, run_id)
            if run is not None:
                run.status, run.finished_at, run.message = status, utcnow(), counts.message
                run.added, run.updated, run.unchanged = (
                    counts.added,
                    counts.updated,
                    counts.unchanged,
                )
                run.deleted, run.skipped, run.failed = counts.deleted, counts.skipped, counts.failed
                run.errors = counts.errors
            src = await s.get(Source, source_id)
            if src is not None:
                src.status, src.last_run_at, src.last_status = "idle", utcnow(), status
                src.last_error = counts.message or (
                    counts.errors[0]["error"] if status != "success" and counts.errors else None
                )
                src.updated_at = utcnow()
            old = list(
                (
                    await s.execute(
                        select(SourceRun.id)
                        .where(SourceRun.source_id == source_id)
                        .order_by(SourceRun.started_at.desc())
                        .offset(KEEP_RUNS)
                    )
                ).scalars()
            )
            if old:
                await s.execute(delete(SourceRun).where(SourceRun.id.in_(old)))
