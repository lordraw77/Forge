"""File type detection from content, never from the client's name or header (US-1.14.1 #3)."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

MAX_UPLOAD_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class FileKind:
    kind: str  # pdf, docx, pptx, xlsx, html, md, txt, png, jpg
    mime: str


ALLOWED = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "html": "text/html",
    "md": "text/markdown",
    "txt": "text/plain",
    "png": "image/png",
    "jpg": "image/jpeg",
}
_OOXML = {"word/": "docx", "ppt/": "pptx", "xl/": "xlsx"}
_ZIP_MAX_ENTRIES = 10_000
_ZIP_MAX_RATIO = 200  # uncompressed/compressed: zip bombs are refused (threat model TB5)


class UnsupportedFile(Exception):
    pass


def _ooxml(head: bytes, path: str | None, data: bytes | None) -> str:
    src = path if path is not None else io.BytesIO(data or head)
    try:
        with zipfile.ZipFile(src) as zf:
            infos = zf.infolist()
            if len(infos) > _ZIP_MAX_ENTRIES:
                raise UnsupportedFile("archive has too many entries")
            packed = sum(i.compress_size for i in infos) or 1
            unpacked = sum(i.file_size for i in infos)
            if unpacked / packed > _ZIP_MAX_RATIO:
                raise UnsupportedFile("archive expands too much (possible zip bomb)")
            names = {i.filename for i in infos}
    except zipfile.BadZipFile as exc:
        raise UnsupportedFile("corrupt archive") from exc
    if "[Content_Types].xml" in names:
        for prefix, kind in _OOXML.items():
            if any(n.startswith(prefix) for n in names):
                return kind
    raise UnsupportedFile("ZIP archives are not supported, except Office documents")


def _text_kind(head: bytes, filename: str) -> str:
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A multi-byte character cut at the end of the sample is still text.
        try:
            text = head[: exc.start].decode("utf-8") if exc.start > len(head) - 4 else ""
        except UnicodeDecodeError:
            text = ""
        if not text:
            raise UnsupportedFile("binary file of an unsupported type") from exc
    if "\x00" in text:
        raise UnsupportedFile("binary file of an unsupported type")
    lowered = text.lstrip().lower()
    if lowered.startswith(("<!doctype html", "<html")) or "<body" in lowered[:4096]:
        return "html"
    # Plain text: the extension only chooses between Markdown and text, both are text.
    return "md" if filename.lower().endswith((".md", ".markdown")) else "txt"


def detect(head: bytes, filename: str, *, path: str | None = None) -> FileKind:
    """`head` is the first bytes (≥ 8 KiB when available); `path` lets ZIPs be inspected."""
    if head.startswith(b"%PDF-"):
        kind = "pdf"
    elif head.startswith(b"\x89PNG\r\n\x1a\n"):
        kind = "png"
    elif head.startswith(b"\xff\xd8\xff"):
        kind = "jpg"
    elif head.startswith(b"PK\x03\x04"):
        kind = _ooxml(head, path, None)
    elif not head:
        raise UnsupportedFile("empty file")
    else:
        kind = _text_kind(head, filename)
    return FileKind(kind, ALLOWED[kind])
