"""Data Collections (E1.14): collections, upload, documents, knowledge bases.

Builders and Project Admins (`asset.write`). Uploads are limited to 100 MB per request
(US-1.14.1 #2) before the body is read: by `Content-Length`, and by `BodyLimit` for chunked
requests.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import StreamingResponse

from forge.api.deps import ProjectContext, project_permission, services
from forge.core.errors import ProblemError
from forge.rag.service import (
    MAX_UPLOAD_BYTES,
    CollectionIn,
    CollectionOut,
    CollectionUpdateIn,
    DocumentOut,
    DocumentUpdateIn,
    KnowledgeBaseOut,
    UploadResult,
)
from forge.rag.service import UploadFile as StoredFile

router = APIRouter(prefix="/api/v1/projects/{project_id}/collections", tags=["data"])

Reader = Annotated[ProjectContext, Depends(project_permission("asset.write"))]
Writer = Annotated[ProjectContext, Depends(project_permission("asset.write", writable=True))]
# Multipart framing on top of the files themselves.
REQUEST_LIMIT = MAX_UPLOAD_BYTES + 1024 * 1024
# Served inline only when the browser cannot execute them; everything else downloads.
INLINE = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "txt": "text/plain; charset=utf-8",
    "md": "text/plain; charset=utf-8",
    "html": "text/plain; charset=utf-8",
}

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]


class BodyLimit:
    """ASGI middleware: reject upload bodies above the limit while they stream in."""

    def __init__(self, app: Any, limit: int = REQUEST_LIMIT) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].endswith("/documents"):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        length = headers.get(b"content-length")
        if length is not None and length.isdigit() and int(length) > self.limit:
            await self._reject(send)
            return
        received = 0

        async def limited() -> MutableMapping[str, Any]:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    raise ProblemError(413, "upload_too_large", "Uploads are limited to 100 MB")
            return message

        await self.app(scope, limited, send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = (
            b'{"type":"https://forge.dev/problems/upload_too_large","title":"Uploads are '
            b'limited to 100 MB per request","status":413,"code":"upload_too_large"}'
        )
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/problem+json"),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


@router.get("", response_model=list[CollectionOut])
async def list_collections(ctx: Reader, request: Request) -> list[CollectionOut]:
    return await services(request).rag.collections.list_collections(ctx.actor, ctx.project_id)


@router.post("", response_model=CollectionOut, status_code=status.HTTP_201_CREATED)
async def create_collection(body: CollectionIn, ctx: Writer, request: Request) -> CollectionOut:
    return await services(request).rag.collections.create(ctx.actor, ctx.project_id, body)


@router.get("/{collection_id}", response_model=CollectionOut)
async def get_collection(collection_id: uuid.UUID, ctx: Reader, request: Request) -> CollectionOut:
    return await services(request).rag.collections.get_collection(
        ctx.actor, ctx.project_id, collection_id
    )


@router.patch("/{collection_id}", response_model=CollectionOut)
async def update_collection(
    collection_id: uuid.UUID, body: CollectionUpdateIn, ctx: Writer, request: Request
) -> CollectionOut:
    return await services(request).rag.collections.update(
        ctx.actor, ctx.project_id, collection_id, body
    )


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(collection_id: uuid.UUID, ctx: Writer, request: Request) -> None:
    """Deletes the files, chunks and embeddings (US-1.14.2)."""
    await services(request).rag.collections.delete(ctx.actor, ctx.project_id, collection_id)


@router.get("/{collection_id}/documents", response_model=list[DocumentOut])
async def list_documents(
    collection_id: uuid.UUID, ctx: Reader, request: Request
) -> list[DocumentOut]:
    return await services(request).rag.collections.list_documents(
        ctx.actor, ctx.project_id, collection_id
    )


@router.post("/{collection_id}/documents", response_model=list[UploadResult])
async def upload_documents(
    collection_id: uuid.UUID,
    ctx: Writer,
    request: Request,
    files: Annotated[list[UploadFile], File(description="Files, or the files of a folder")],
    paths: Annotated[
        list[str] | None, Form(description="Relative path of each file (folder uploads)")
    ] = None,
    group_ids: Annotated[
        list[uuid.UUID] | None, Form(description="Groups that may read the files")
    ] = None,
    role_ids: Annotated[
        list[uuid.UUID] | None, Form(description="Roles that may read the files")
    ] = None,
) -> list[UploadResult]:
    """Per-file results: accepted (ingestion started), unchanged (same SHA-256) or rejected.
    Without groups and roles the files are readable by the whole project."""
    collections = services(request).rag.collections
    groups = await collections.acl(ctx.project_id, group_ids or [], role_ids or [])
    if paths is not None and len(paths) != len(files):
        raise ProblemError(422, "invalid_paths", "Give one path per file, or none")
    stored = [
        StoredFile(
            filename=f.filename or "file",
            path=(paths[i] if paths else ""),
            fh=f.file,
            size=f.size or 0,
        )
        for i, f in enumerate(files)
    ]
    # Folder uploads send the full relative path in `paths`; keep only its folder part.
    for item in stored:
        if item.path.endswith("/" + item.filename) or item.path == item.filename:
            item.path = item.path[: -len(item.filename)].rstrip("/")
    return await services(request).rag.collections.upload(
        ctx.actor, ctx.project_id, collection_id, stored, groups
    )


@router.patch("/{collection_id}/documents/{document_id}", response_model=DocumentOut)
async def update_document(
    collection_id: uuid.UUID,
    document_id: uuid.UUID,
    body: DocumentUpdateIn,
    ctx: Writer,
    request: Request,
) -> DocumentOut:
    """Groups and roles that may read the document; none = the whole project (US-1.16.2)."""
    return await services(request).rag.collections.set_acl(
        ctx.actor, ctx.project_id, collection_id, document_id, body.group_ids, body.role_ids
    )


@router.post("/{collection_id}/documents/{document_id}/retry", response_model=DocumentOut)
async def retry_document(
    collection_id: uuid.UUID, document_id: uuid.UUID, ctx: Writer, request: Request
) -> DocumentOut:
    return await services(request).rag.collections.retry(
        ctx.actor, ctx.project_id, collection_id, document_id
    )


@router.delete("/{collection_id}/documents/{document_id}", status_code=204)
async def delete_document(
    collection_id: uuid.UUID, document_id: uuid.UUID, ctx: Writer, request: Request
) -> None:
    await services(request).rag.collections.delete_document(
        ctx.actor, ctx.project_id, collection_id, document_id
    )


@router.get("/{collection_id}/documents/{document_id}/content")
async def download_document(
    collection_id: uuid.UUID, document_id: uuid.UUID, ctx: Reader, request: Request
) -> StreamingResponse:
    """The original file. PDFs and images open inline (citations link to `#page=N`); text and
    HTML are shown as plain text; Office files download. Never executable in the browser."""
    doc, stream = await services(request).rag.collections.download(
        ctx.actor, ctx.project_id, collection_id, document_id
    )
    inline = INLINE.get(doc.kind)
    safe_name = doc.name.replace('"', "").replace("\r", "").replace("\n", "")
    disposition = "inline" if inline else "attachment"
    return StreamingResponse(
        stream,
        media_type=inline or doc.mime_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{safe_name}"',
            "X-Content-Type-Options": "nosniff",
            # Chrome's PDF viewer refuses sandboxed documents; PDFs cannot run page scripts.
            "Content-Security-Policy": (
                "default-src 'none'" if doc.kind == "pdf" else "sandbox; default-src 'none'"
            ),
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/{collection_id}/knowledge-bases", response_model=list[KnowledgeBaseOut])
async def list_knowledge_bases(
    collection_id: uuid.UUID, ctx: Reader, request: Request
) -> list[KnowledgeBaseOut]:
    return await services(request).rag.collections.knowledge_bases(
        ctx.actor, ctx.project_id, collection_id
    )
