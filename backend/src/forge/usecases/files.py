"""Files of use cases (M2.3d): form inputs and the requirements attachment uploaded by people,
output files generated when a run ends.

All of them live in object storage under `projects/<project>/usecases/files/<file>`. For every
uploaded file the text is extracted once, at upload, and stored next to it (`<key>.txt`): runs
read the text, never the original, and a file Forge cannot read is refused at the form instead of
failing a run later.
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
import re
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import IO, Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import audit
from forge.core.context import Actor
from forge.core.db import project_scope
from forge.core.errors import ProblemError
from forge.rag.service import MAX_UPLOAD_BYTES, ObjectStore
from forge.usecases import render
from forge.usecases.models import UseCase, UseCaseFile, UseCaseRun
from forge.usecases.schemas import RunFileOut

FILE_NOT_FOUND = ProblemError(404, "not_found", "File not found")
NAME_BAD = re.compile(r"[\x00-\x1f\\/:*?\"<>|]+")


def file_key(project_id: uuid.UUID, file_id: uuid.UUID) -> str:
    return f"projects/{project_id}/usecases/files/{file_id}"


def _bad_file(field: str) -> ProblemError:
    return ProblemError(
        422,
        "invalid_input",
        "The inputs do not match the use case form",
        [{"message": f"{field}: upload the file again"}],
    )


def clean_name(name: str) -> str:
    base = NAME_BAD.sub("_", os.path.basename(name or "").strip()).strip(" .")
    return base[:200] or "file"


@dataclass
class Download:
    name: str
    content_type: str
    size: int
    body: Iterator[bytes]


class UseCaseFiles:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], store: ObjectStore) -> None:
        self._sessions = sessions
        self._store = store

    @asynccontextmanager
    async def _session(self, actor: Actor, project_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            yield s

    # ------------------------------------------------------------------ upload
    async def upload(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        kind: str,
        filename: str,
        fh: IO[bytes],
        content_type: str | None,
        *,
        use_case_id: uuid.UUID | None = None,
    ) -> RunFileOut:
        """Store an input file or the requirements attachment of a use case (one per use
        case: a new one replaces the old)."""
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(0)
        if size == 0:
            raise ProblemError(422, "empty_file", "The file is empty")
        if size > MAX_UPLOAD_BYTES:
            raise ProblemError(
                413, "file_too_large", f"Files can be up to {MAX_UPLOAD_BYTES // 1024 // 1024} MB"
            )
        name = clean_name(filename)
        data = fh.read()
        try:
            text = await asyncio.to_thread(render.extract_text, name, data)
        except Exception as exc:  # unreadable PDF/DOCX/XLSX or binary content
            raise ProblemError(
                422,
                "unsupported_file",
                "Forge reads text, Markdown, CSV, JSON, PDF, DOCX and XLSX files",
            ) from exc
        file_id = uuid.uuid4()
        key = file_key(project_id, file_id)
        ctype = content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        replaced: list[str] = []
        async with self._session(actor, project_id) as s:
            if kind == "requirements":
                uc = (
                    await s.execute(select(UseCase).where(UseCase.id == use_case_id))
                ).scalar_one_or_none()
                if uc is None:
                    raise ProblemError(404, "not_found", "Use case not found")
                old = (
                    await s.execute(
                        select(UseCaseFile).where(
                            UseCaseFile.kind == "requirements",
                            UseCaseFile.use_case_id == use_case_id,
                        )
                    )
                ).scalars()
                replaced = [f.storage_key for f in old]
                await s.execute(
                    delete(UseCaseFile).where(
                        UseCaseFile.kind == "requirements", UseCaseFile.use_case_id == use_case_id
                    )
                )
            await self._store.put_bytes(key, data, ctype)
            await self._store.put_bytes(f"{key}.txt", text.encode(), "text/plain")
            row = UseCaseFile(
                id=file_id,
                project_id=project_id,
                kind=kind,
                use_case_id=use_case_id,
                name=name,
                content_type=ctype,
                size=size,
                sha256=hashlib.sha256(data).hexdigest(),
                storage_key=key,
                created_by=actor.id,
            )
            s.add(row)
            await s.flush()
            if kind == "requirements":
                await audit.record(
                    s,
                    actor,
                    "usecase.requirements_file",
                    "use_case",
                    use_case_id,
                    {"file": name, "size": size},
                    project_id=project_id,
                )
            out = RunFileOut.model_validate(row)
        if replaced:
            await self._store.delete(
                [k for old_key in replaced for k in (old_key, f"{old_key}.txt")]
            )
        return out

    async def requirements_file(
        self, actor: Actor, project_id: uuid.UUID, use_case_id: uuid.UUID
    ) -> RunFileOut | None:
        async with self._session(actor, project_id) as s:
            row = (
                await s.execute(
                    select(UseCaseFile).where(
                        UseCaseFile.kind == "requirements", UseCaseFile.use_case_id == use_case_id
                    )
                )
            ).scalar_one_or_none()
            return RunFileOut.model_validate(row) if row else None

    async def delete_requirements(
        self, actor: Actor, project_id: uuid.UUID, use_case_id: uuid.UUID
    ) -> None:
        async with self._session(actor, project_id) as s:
            rows = list(
                (
                    await s.execute(
                        select(UseCaseFile).where(
                            UseCaseFile.kind == "requirements",
                            UseCaseFile.use_case_id == use_case_id,
                        )
                    )
                ).scalars()
            )
            keys = [k for f in rows for k in (f.storage_key, f"{f.storage_key}.txt")]
            for f in rows:
                await s.delete(f)
        await self._store.delete(keys)

    async def requirements_text(
        self, actor: Actor, project_id: uuid.UUID, use_case_id: uuid.UUID, limit: int = 20_000
    ) -> str:
        """The text of the requirements attachment, for the Planner."""
        async with self._session(actor, project_id) as s:
            row = (
                await s.execute(
                    select(UseCaseFile).where(
                        UseCaseFile.kind == "requirements", UseCaseFile.use_case_id == use_case_id
                    )
                )
            ).scalar_one_or_none()
            key = row.storage_key if row else None
        if key is None:
            return ""
        return (await self._store.get_bytes(f"{key}.txt")).decode(errors="replace")[:limit]

    # ------------------------------------------------------------------ inputs of a run
    async def check_inputs(
        self, s: AsyncSession, actor: Actor, field_names: list[str], inputs: dict[str, Any]
    ) -> None:
        """Every file field of the form must name an input file the caller uploaded."""
        for name in field_names:
            raw = inputs.get(name)
            if raw in (None, ""):
                continue
            try:
                file_id = uuid.UUID(str(raw))
            except ValueError:
                raise _bad_file(name) from None
            row = (
                await s.execute(select(UseCaseFile).where(UseCaseFile.id == file_id))
            ).scalar_one_or_none()
            if row is None or row.kind != "input" or row.created_by != actor.id:
                raise _bad_file(name)

    async def file_texts(self, project_id: uuid.UUID, ids: list[uuid.UUID]) -> dict[str, str]:
        """Extracted text by file id (worker side; the run owner was checked at start)."""
        out: dict[str, str] = {}
        async with project_scope(self._sessions, project_id) as s:
            rows = (await s.execute(select(UseCaseFile).where(UseCaseFile.id.in_(ids)))).scalars()
            keys = {str(f.id): f.storage_key for f in rows}
        for fid, key in keys.items():
            out[fid] = (await self._store.get_bytes(f"{key}.txt")).decode(errors="replace")
        return out

    # ------------------------------------------------------------------ outputs
    async def store_output(
        self,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        created_by: uuid.UUID | None,
        kind: str,
        title: str,
        value: Any,
    ) -> None:
        """Generate and store the file of a finished run (worker side; a retry adds nothing)."""
        async with project_scope(self._sessions, project_id) as s:
            if await self.files_of(s, run_id):
                return
        data = await asyncio.to_thread(render.render, kind, title, value)
        file_id = uuid.uuid4()
        key = file_key(project_id, file_id)
        ctype = render.CONTENT_TYPES[kind]
        await self._store.put_bytes(key, data, ctype)
        stem = clean_name(title).replace(" ", "_")[:80]
        async with project_scope(self._sessions, project_id) as s:
            s.add(
                UseCaseFile(
                    id=file_id,
                    project_id=project_id,
                    kind="output",
                    run_id=run_id,
                    name=f"{stem}.{render.EXTENSIONS[kind]}",
                    content_type=ctype,
                    size=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                    storage_key=key,
                    created_by=created_by,
                )
            )

    async def files_of(self, s: AsyncSession, run_id: uuid.UUID) -> list[RunFileOut]:
        rows = (
            await s.execute(
                select(UseCaseFile)
                .where(UseCaseFile.run_id == run_id, UseCaseFile.kind == "output")
                .order_by(UseCaseFile.created_at)
            )
        ).scalars()
        return [RunFileOut.model_validate(r) for r in rows]

    # ------------------------------------------------------------------ download
    async def download(
        self, actor: Actor, project_id: uuid.UUID, file_id: uuid.UUID, *, see_all: bool
    ) -> Download:
        """Same visibility as the run the file belongs to; other files: their uploader."""
        async with self._session(actor, project_id) as s:
            row = (
                await s.execute(select(UseCaseFile).where(UseCaseFile.id == file_id))
            ).scalar_one_or_none()
            if row is None:
                raise FILE_NOT_FOUND
            if row.kind == "output":
                run = (
                    await s.execute(select(UseCaseRun).where(UseCaseRun.id == row.run_id))
                ).scalar_one_or_none()
                if run is None or (not see_all and run.created_by != actor.id):
                    raise FILE_NOT_FOUND
            elif row.created_by != actor.id and not see_all:
                raise FILE_NOT_FOUND
            name, ctype, size, key, kind = (
                row.name,
                row.content_type,
                row.size,
                row.storage_key,
                row.kind,
            )
        if kind != "output":  # what people upload is never served as an active type
            ctype = "application/octet-stream"
        return Download(name, ctype, size, await self._store.open_stream(key))
