"""Document parsing to a neutral block list (E1.15, spec "RAG design" → Ingestion).

- TXT, Markdown and HTML: built-in parsers (fast, no model).
- PDF, DOCX, PPTX, XLSX, PNG/JPG: Docling, with layout, tables as Markdown and OCR. Docling and
  its CPU models ship only in the ingestion worker image (ADR-013); elsewhere these kinds fail
  with `parser_unavailable`.

The result is stored as JSON next to the file and shared by every knowledge base of the project
that indexes the same file content.
"""

from __future__ import annotations

import html
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

PARSER_VERSION = 1
DOCLING_KINDS = frozenset({"pdf", "docx", "pptx", "xlsx", "png", "jpg"})


class ParseError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Block:
    text: str
    kind: str = "text"  # text | heading | table | list
    level: int = 0  # heading level (1 = top)
    page: int | None = None


@dataclass
class ParsedDocument:
    title: str
    blocks: list[Block] = field(default_factory=list)
    pages: int | None = None
    parser: str = "builtin"
    version: int = PARSER_VERSION

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ParsedDocument:
        blocks = [Block(**b) for b in data.get("blocks", [])]
        return cls(
            title=data.get("title", ""),
            blocks=blocks,
            pages=data.get("pages"),
            parser=data.get("parser", "builtin"),
            version=data.get("version", PARSER_VERSION),
        )


def _stem(filename: str) -> str:
    return re.sub(r"\.[A-Za-z0-9]{1,5}$", "", filename).strip() or filename


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


# ------------------------------------------------------------------ text and Markdown
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_MD_FENCE = re.compile(r"^(```|~~~)")


def parse_markdown(data: bytes, filename: str) -> ParsedDocument:
    text = _decode(data)
    doc = ParsedDocument(title=_stem(filename))
    para: list[str] = []
    in_fence = False
    in_table = False

    def flush(kind: str = "text") -> None:
        if para:
            body = "\n".join(para).strip()
            if body:
                doc.blocks.append(Block(body, kind))
            para.clear()

    for line in text.splitlines():
        if _MD_FENCE.match(line.strip()):
            para.append(line)
            in_fence = not in_fence
            continue
        if in_fence:
            para.append(line)
            continue
        heading = _MD_HEADING.match(line)
        if heading:
            flush("table" if in_table else "text")
            in_table = False
            level, title = len(heading.group(1)), heading.group(2)
            doc.blocks.append(Block(title, "heading", level))
            if level == 1 and doc.title == _stem(filename):
                doc.title = title
            continue
        is_table_row = line.lstrip().startswith("|")
        if is_table_row != in_table and para:
            flush("table" if in_table else "text")
        in_table = is_table_row
        if not line.strip():
            flush("table" if in_table else "text")
            in_table = False
            continue
        para.append(line)
    flush("table" if in_table else "text")
    return doc


def parse_text(data: bytes, filename: str) -> ParsedDocument:
    doc = ParsedDocument(title=_stem(filename))
    for para in re.split(r"\n\s*\n", _decode(data)):
        if para.strip():
            doc.blocks.append(Block(para.strip()))
    return doc


# ------------------------------------------------------------------ HTML
class _Html(HTMLParser):
    BLOCKS = frozenset({"p", "li", "pre", "blockquote", "td", "th", "dd", "dt", "caption"})
    SKIP = frozenset({"script", "style", "noscript", "template", "svg", "head"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self.title = ""
        self._buf: list[str] = []
        self._skip = 0
        self._heading = 0
        self._in_title = False

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf.clear()
        if text:
            kind = "heading" if self._heading else "text"
            self.blocks.append(Block(text, kind, self._heading))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if re.fullmatch(r"h[1-6]", tag):
            self._flush()
            self._heading = int(tag[1])
        elif tag in self.BLOCKS or tag in ("br", "div", "section", "article", "tr"):
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        if tag == "title":
            self._in_title = False
        if re.fullmatch(r"h[1-6]", tag):
            self._flush()
            self._heading = 0
        elif tag in self.BLOCKS or tag in ("div", "section", "article", "tr"):
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self._buf.append(data)


def parse_html(data: bytes, filename: str) -> ParsedDocument:
    parser = _Html()
    parser.feed(_decode(data))
    parser.close()
    parser._flush()
    title = html.unescape(parser.title).strip() or _stem(filename)
    return ParsedDocument(title=title, blocks=parser.blocks)


# ------------------------------------------------------------------ Docling
_CONVERTER: Any = None
_CONVERTER_LOCK = threading.Lock()


def _converter() -> Any:  # pragma: no cover - ingestion image only
    """One Docling converter per process: loading the models takes seconds, so it is built
    once (and warmed up at worker start) and reused for every document."""
    global _CONVERTER
    with _CONVERTER_LOCK:
        if _CONVERTER is None:
            try:
                from docling.datamodel.accelerator_options import AcceleratorOptions
                from docling.datamodel.base_models import InputFormat
                from docling.datamodel.pipeline_options import PdfPipelineOptions
                from docling.document_converter import (
                    DocumentConverter,
                    ImageFormatOption,
                    PdfFormatOption,
                )
            except ImportError as exc:
                raise ParseError(
                    "parser_unavailable", "This file type needs the ingestion worker (Docling)"
                ) from exc
            threads = int(os.environ.get("FORGE_INGEST_THREADS") or os.cpu_count() or 2)
            # OCR runs on bitmap regions only (scans, images); text layers are read directly.
            opts = PdfPipelineOptions(
                do_ocr=True, accelerator_options=AcceleratorOptions(num_threads=threads)
            )
            _CONVERTER = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
                    InputFormat.IMAGE: ImageFormatOption(pipeline_options=opts),
                }
            )
            _CONVERTER.initialize_pipeline(InputFormat.PDF)
        return _CONVERTER


def docling_models_present(path: str | None = None) -> bool:
    root = Path(path or os.environ.get("DOCLING_ARTIFACTS_PATH") or "/app/models")
    return root.is_dir() and any(p.name.startswith("docling-project--") for p in root.iterdir())


def download_docling_models(path: str | None = None) -> str:  # pragma: no cover - image
    """Layout, table, figure, code/formula and RapidOCR models into the artifacts directory
    (build time for the embedded image, worker start for the `-slim` image)."""
    try:
        from docling.utils.model_downloader import download_models
    except ImportError as exc:
        raise ParseError("parser_unavailable", "Docling is not installed in this image") from exc
    root = Path(path or os.environ.get("DOCLING_ARTIFACTS_PATH") or "/app/models")
    root.mkdir(parents=True, exist_ok=True)
    download_models(output_dir=root, progress=False)
    return str(root)


def warm_up() -> bool:
    """Load the Docling models ahead of the first document; False when Docling is absent."""
    try:
        _converter()
        return True
    except ParseError:
        return False


def parse_with_docling(path: str, filename: str) -> ParsedDocument:  # pragma: no cover - image
    try:
        from docling_core.types.doc import DocItemLabel, SectionHeaderItem, TableItem, TextItem
    except ImportError as exc:
        raise ParseError(
            "parser_unavailable", "This file type needs the ingestion worker (Docling)"
        ) from exc
    converter = _converter()
    try:
        result = converter.convert(path)
    except Exception as exc:  # Docling raises many types; the message is the useful part
        raise ParseError("parse_failed", f"Docling could not read the file: {exc}") from exc
    dl = result.document
    doc = ParsedDocument(title=_stem(filename), parser="docling")
    doc.pages = len(dl.pages) if getattr(dl, "pages", None) else None
    for item, _level in dl.iterate_items():
        page = item.prov[0].page_no if getattr(item, "prov", None) else None
        if isinstance(item, TableItem):
            text = item.export_to_markdown(doc=dl).strip()
            if text:
                doc.blocks.append(Block(text, "table", page=page))
        elif isinstance(item, SectionHeaderItem):
            doc.blocks.append(Block(item.text.strip(), "heading", item.level or 1, page))
        elif isinstance(item, TextItem):
            if item.label == DocItemLabel.TITLE:
                doc.title = item.text.strip() or doc.title
                doc.blocks.append(Block(item.text.strip(), "heading", 1, page))
            elif item.label in (DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER):
                continue
            elif item.text.strip():
                kind = "list" if item.label == DocItemLabel.LIST_ITEM else "text"
                doc.blocks.append(Block(item.text.strip(), kind, page=page))
    return doc


def parse(kind: str, data: bytes | None, path: str | None, filename: str) -> ParsedDocument:
    """Parse one file. `path` is needed for Docling kinds, `data` for the built-in ones."""
    if kind == "md":
        doc = parse_markdown(data or b"", filename)
    elif kind == "txt":
        doc = parse_text(data or b"", filename)
    elif kind == "html":
        doc = parse_html(data or b"", filename)
    elif kind in DOCLING_KINDS:
        if path is None:
            raise ParseError("parser_unavailable", "Docling needs the file on disk")
        doc = parse_with_docling(path, filename)
    else:
        raise ParseError("unsupported_type", f"Unsupported file type '{kind}'")
    if not any(b.text.strip() for b in doc.blocks):
        raise ParseError("no_text", "No text could be extracted from the file")
    return doc
