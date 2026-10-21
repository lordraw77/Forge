"""Definition and control of scheduled sources: CRUD, credentials, run history, sync now (E2.13)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import audit, secrets
from forge.core.context import Actor
from forge.core.crypto import Envelope
from forge.core.db import project_scope
from forge.core.errors import ProblemError
from forge.core.models import utcnow
from forge.rag.collections import CollectionService
from forge.rag.models import DataCollection, Source, SourceItem, SourceRun
from forge.rag.sources.connectors import SECRET_KEYS, SOURCE_TYPES, SourceError, validate_config
from forge.rag.starter import IngestStarter

NOT_FOUND = ProblemError(404, "not_found", "Source not found")


class SourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    collection_id: uuid.UUID
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, Any] | None = None
    interval_minutes: int | None = Field(default=None, ge=5, le=525_600)
    enabled: bool = True
    delete_missing: bool = True
    group_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    role_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)


class SourceUpdateIn(BaseModel):
    """Only the fields present change. `credentials` replaces the stored ones; `{}` clears them."""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    config: dict[str, Any] | None = None
    credentials: dict[str, Any] | None = None
    interval_minutes: int | None = Field(default=None, ge=5, le=525_600)
    clear_schedule: bool = False
    enabled: bool | None = None
    delete_missing: bool | None = None
    group_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)
    role_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)


class SourceOut(BaseModel):
    id: uuid.UUID
    collection_id: uuid.UUID
    collection_name: str
    name: str
    type: str
    config: dict[str, Any]
    has_credentials: bool
    interval_minutes: int | None
    enabled: bool
    delete_missing: bool
    group_ids: list[uuid.UUID]
    role_ids: list[uuid.UUID]
    status: str
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_status: str | None
    last_error: str | None
    documents: int


class SourceRunOut(BaseModel):
    id: uuid.UUID
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    added: int
    updated: int
    unchanged: int
    deleted: int
    skipped: int
    failed: int
    message: str | None
    errors: list[dict[str, Any]]


class SyncIn(BaseModel):
    allow_mass_delete: bool = False


class SyncOut(BaseModel):
    started: bool


class SourceTypes(BaseModel):
    types: list[str]
    filesystem_enabled: bool
    secret_keys: dict[str, list[str]]


class SourceService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        envelope: Envelope,
        collections: CollectionService,
        starter: IngestStarter,
        roots: list[Any],
    ) -> None:
        self._sessions = sessions
        self._envelope = envelope
        self._collections = collections
        self._starter = starter
        self._roots = roots

    def _roots_for(self, project_id: uuid.UUID) -> list[Path]:
        return [r.path for r in self._roots if r.allows(project_id)]

    def types(self, project_id: uuid.UUID) -> SourceTypes:
        return SourceTypes(
            types=list(SOURCE_TYPES),
            filesystem_enabled=bool(self._roots_for(project_id)),
            secret_keys={k: list(v) for k, v in SECRET_KEYS.items()},
        )

    # ---- validation
    def _config(self, project_id: uuid.UUID, kind: str, config: dict[str, Any]) -> dict[str, Any]:
        if kind not in SOURCE_TYPES:
            raise ProblemError(
                422, "invalid_type", f"Type must be one of {', '.join(SOURCE_TYPES)}"
            )
        try:
            clean = validate_config(kind, config)
        except SourceError as exc:
            raise ProblemError(422, "invalid_config", str(exc)) from exc
        except ValueError as exc:  # pydantic's ValidationError
            raise ProblemError(422, "invalid_config", str(exc)[:500]) from exc
        if kind == "filesystem":
            roots = self._roots_for(project_id)
            if not roots:
                raise ProblemError(
                    422,
                    "filesystem_disabled",
                    "No directory is enabled for this project: the administrator must list it "
                    "in FORGE_SOURCE_ROOTS",
                )
            target = Path(clean["path"]).resolve()
            if not any(r.resolve() in (target, *target.parents) for r in roots):
                raise ProblemError(
                    422,
                    "path_not_allowed",
                    "The path is outside the directories enabled for sources",
                )
        return clean

    @staticmethod
    def _credentials(kind: str, creds: dict[str, Any]) -> dict[str, Any]:
        allowed = SECRET_KEYS[kind]
        bad = [k for k in creds if k not in allowed]
        if bad:
            raise ProblemError(
                422,
                "invalid_credentials",
                f"{kind} sources accept: {', '.join(allowed) or 'no credentials'}",
            )
        return {k: v for k, v in creds.items() if v not in (None, "", {})}

    # ---- queries
    async def _out(self, s: AsyncSession, src: Source) -> SourceOut:
        name = (
            await s.execute(
                select(DataCollection.name).where(DataCollection.id == src.collection_id)
            )
        ).scalar_one_or_none()
        docs = (
            await s.execute(
                select(func.count()).select_from(SourceItem).where(SourceItem.source_id == src.id)
            )
        ).scalar_one()
        groups = [uuid.UUID(t.split(":", 1)[1]) for t in src.acl_groups if t.startswith("group:")]
        roles = [uuid.UUID(t.split(":", 1)[1]) for t in src.acl_groups if t.startswith("role:")]
        return SourceOut(
            id=src.id,
            collection_id=src.collection_id,
            collection_name=name or "",
            name=src.name,
            type=src.type,
            config=src.config,
            has_credentials=src.secret_id is not None,
            interval_minutes=src.interval_minutes,
            enabled=src.enabled,
            delete_missing=src.delete_missing,
            group_ids=groups,
            role_ids=roles,
            status=src.status,
            next_run_at=src.next_run_at,
            last_run_at=src.last_run_at,
            last_status=src.last_status,
            last_error=src.last_error,
            documents=docs,
        )

    async def _get(self, s: AsyncSession, source_id: uuid.UUID) -> Source:
        src = await s.get(Source, source_id)
        if src is None:
            raise NOT_FOUND
        return src

    async def list_sources(self, actor: Actor, project_id: uuid.UUID) -> list[SourceOut]:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            rows = (await s.execute(select(Source).order_by(Source.name))).scalars().all()
            return [await self._out(s, r) for r in rows]

    async def get(self, actor: Actor, project_id: uuid.UUID, source_id: uuid.UUID) -> SourceOut:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            return await self._out(s, await self._get(s, source_id))

    async def create(self, actor: Actor, project_id: uuid.UUID, body: SourceIn) -> SourceOut:
        config = self._config(project_id, body.type, body.config)
        creds = self._credentials(body.type, body.credentials or {})
        acl = await self._collections.acl(project_id, body.group_ids, body.role_ids)
        async with project_scope(self._sessions, project_id, actor.id) as s:
            if await s.get(DataCollection, body.collection_id) is None:
                raise ProblemError(422, "unknown_collection", "Collection not found")
            secret_id = (
                await secrets.put(s, self._envelope, "rag.source", json.dumps(creds), project_id)
                if creds
                else None
            )
            src = Source(
                project_id=project_id,
                collection_id=body.collection_id,
                name=body.name.strip(),
                type=body.type,
                config=config,
                secret_id=secret_id,
                interval_minutes=body.interval_minutes,
                enabled=body.enabled,
                delete_missing=body.delete_missing,
                acl_groups=acl,
                next_run_at=utcnow() if body.interval_minutes and body.enabled else None,
                created_by=actor.id,
            )
            s.add(src)
            try:
                await s.flush()
            except IntegrityError as exc:
                raise ProblemError(409, "name_taken", "A source with this name exists") from exc
            await audit.record(
                s,
                actor,
                "source.create",
                "rag_source",
                src.id,
                {"name": src.name, "type": src.type, "config": config},
                project_id=project_id,
            )
            return await self._out(s, src)

    async def update(
        self, actor: Actor, project_id: uuid.UUID, source_id: uuid.UUID, body: SourceUpdateIn
    ) -> SourceOut:
        acl = None
        if body.group_ids is not None or body.role_ids is not None:
            acl = await self._collections.acl(project_id, body.group_ids or [], body.role_ids or [])
        async with project_scope(self._sessions, project_id, actor.id) as s:
            src = await self._get(s, source_id)
            changed: dict[str, Any] = {}
            if body.name is not None:
                src.name = body.name.strip()
                changed["name"] = src.name
            if body.config is not None:
                src.config = self._config(project_id, src.type, body.config)
                changed["config"] = src.config
            if body.credentials is not None:
                creds = self._credentials(src.type, body.credentials)
                if src.secret_id is not None:
                    await secrets.delete(s, src.secret_id)
                src.secret_id = (
                    await secrets.put(
                        s, self._envelope, "rag.source", json.dumps(creds), project_id
                    )
                    if creds
                    else None
                )
                changed["credentials"] = "replaced" if creds else "cleared"
            if body.clear_schedule:
                src.interval_minutes, src.next_run_at = None, None
                changed["interval_minutes"] = None
            elif body.interval_minutes is not None:
                src.interval_minutes = body.interval_minutes
                src.next_run_at = utcnow()
                changed["interval_minutes"] = body.interval_minutes
            if body.enabled is not None:
                src.enabled = body.enabled
                if body.enabled and src.interval_minutes and src.next_run_at is None:
                    src.next_run_at = utcnow()
                changed["enabled"] = body.enabled
            if body.delete_missing is not None:
                src.delete_missing = body.delete_missing
                changed["delete_missing"] = body.delete_missing
            if acl is not None:
                src.acl_groups = acl
                changed["acl"] = acl
            src.updated_at = utcnow()
            try:
                await s.flush()
            except IntegrityError as exc:
                raise ProblemError(409, "name_taken", "A source with this name exists") from exc
            await audit.record(
                s, actor, "source.update", "rag_source", src.id, changed, project_id=project_id
            )
            return await self._out(s, src)

    async def delete(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        source_id: uuid.UUID,
        delete_documents: bool,
    ) -> None:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            src = await self._get(s, source_id)
            collection_id = src.collection_id
            docs = list(
                (
                    await s.execute(
                        select(SourceItem.document_id).where(SourceItem.source_id == source_id)
                    )
                ).scalars()
            )
            if src.secret_id is not None:
                await secrets.delete(s, src.secret_id)
            await audit.record(
                s,
                actor,
                "source.delete",
                "rag_source",
                src.id,
                {"name": src.name, "documents": len(docs), "delete_documents": delete_documents},
                project_id=project_id,
            )
            await s.delete(src)
        if delete_documents:
            for doc_id in docs:
                await self._collections.delete_document(actor, project_id, collection_id, doc_id)

    async def sync(
        self, actor: Actor, project_id: uuid.UUID, source_id: uuid.UUID, body: SyncIn
    ) -> SyncOut:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            src = await self._get(s, source_id)
            await audit.record(
                s,
                actor,
                "source.sync",
                "rag_source",
                src.id,
                {"allow_mass_delete": body.allow_mass_delete},
                project_id=project_id,
            )
        started = await self._starter.source(
            project_id, source_id, "manual", allow_mass_delete=body.allow_mass_delete
        )
        return SyncOut(started=started)

    async def runs(
        self, actor: Actor, project_id: uuid.UUID, source_id: uuid.UUID, limit: int
    ) -> list[SourceRunOut]:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            await self._get(s, source_id)
            rows = (
                await s.execute(
                    select(SourceRun)
                    .where(SourceRun.source_id == source_id)
                    .order_by(SourceRun.started_at.desc())
                    .limit(min(limit, 50))
                )
            ).scalars()
            return [
                SourceRunOut(
                    id=r.id,
                    trigger=r.trigger,
                    status=r.status,
                    started_at=r.started_at,
                    finished_at=r.finished_at,
                    added=r.added,
                    updated=r.updated,
                    unchanged=r.unchanged,
                    deleted=r.deleted,
                    skipped=r.skipped,
                    failed=r.failed,
                    message=r.message,
                    errors=r.errors,
                )
                for r in rows
            ]
