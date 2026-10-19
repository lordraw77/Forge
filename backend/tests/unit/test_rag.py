"""RAG building blocks without services: sniffing, parsing, chunking, fusion, query builder."""

from __future__ import annotations

import io
import uuid
import zipfile
from typing import Any

import pytest

from forge.api.routes_data import BodyLimit
from forge.rag.answer import cited_numbers, context_block
from forge.rag.chunking import chunk, tokens
from forge.rag.ingest import chunk_id
from forge.rag.parsing import ParseError, parse
from forge.rag.retrieval import RetrievedChunk, rrf
from forge.rag.schemas import PipelineConfig
from forge.rag.search import PUBLIC, acl_terms, bm25_query, reader_groups
from forge.rag.sniff import UnsupportedFile, detect


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.mark.ac("US-1.14.1/1")
@pytest.mark.ac("US-1.14.1/3")
def test_sniffing_uses_content_not_names() -> None:
    assert detect(b"%PDF-1.7 ...", "report.txt").kind == "pdf"
    assert detect(b"\x89PNG\r\n\x1a\n....", "x.jpg").kind == "png"
    assert detect(b"\xff\xd8\xff\xe0", "photo").kind == "jpg"
    assert detect(b"<!DOCTYPE html><html>", "page.txt").kind == "html"
    assert detect(b"# Title\n\ntext", "readme.md").kind == "md"
    assert detect("caffè ☕".encode(), "notes").kind == "txt"
    docx = _zip({"[Content_Types].xml": b"<x/>", "word/document.xml": b"<w/>"})
    assert detect(docx[:8192], "a.bin").kind == "docx"
    xlsx = _zip({"[Content_Types].xml": b"<x/>", "xl/workbook.xml": b"<w/>"})
    assert detect(xlsx, "a").kind == "xlsx"
    for data, name in (
        (b"MZ\x90\x00\x03\x00\x00\x00" + bytes(range(200)), "tool.pdf"),
        (_zip({"a.txt": b"x"}), "archive.docx"),
        (b"", "empty.txt"),
    ):
        with pytest.raises(UnsupportedFile):
            detect(data, name)


def test_zip_bombs_are_refused() -> None:
    bomb = _zip({"[Content_Types].xml": b"<x/>", "word/document.xml": b"0" * 5_000_000})
    with pytest.raises(UnsupportedFile, match="zip bomb"):
        detect(bomb, "big.docx")


def test_markdown_and_html_parsing() -> None:
    doc = parse(
        "md",
        b"# Guide\n\nIntro.\n\n## Setup\n\n| a |\n|---|\n| 1 |\n\n```\n# not a heading\n```\n",
        None,
        "g.md",
    )
    assert doc.title == "Guide"
    kinds = [(b.kind, b.level) for b in doc.blocks]
    assert ("heading", 2) in kinds
    assert ("table", 0) in kinds
    assert not any(b.kind == "heading" and "not a heading" in b.text for b in doc.blocks)
    html = parse(
        "html",
        b"<title>T</title><script>evil()</script><h2>S</h2><p>Hi <b>there</b></p>",
        None,
        "x.html",
    )
    assert html.title == "T"
    assert [(b.kind, b.text) for b in html.blocks] == [("heading", "S"), ("text", "Hi there")]
    with pytest.raises(ParseError) as err:
        parse("pdf", None, None, "a.pdf")
    assert err.value.code == "parser_unavailable"
    with pytest.raises(ParseError):
        parse("txt", b"   \n\n  ", None, "blank.txt")


@pytest.mark.ac("US-1.15.1/2")
def test_structure_aware_chunks_with_overlap_and_prefix() -> None:
    body = " ".join(f"Sentence {i} about backups and restores." for i in range(80)).encode()
    doc = parse(
        "md", b"# Manual\n\n## Backups\n\n" + body + b"\n\n## TLS\n\nShort.\n", None, "m.md"
    )
    chunks = chunk(doc, size=120, overlap=20)
    assert all(c.token_count <= 120 + 20 for c in chunks)
    assert {c.heading_path for c in chunks} == {"Manual > Backups", "Manual > TLS"}
    assert chunks[-1].content == "Short."  # a chunk never crosses a heading
    assert chunks[0].embed_text.startswith("Manual\nManual > Backups\n\n")
    # Overlap: the next chunk starts with the tail of the previous one.
    head = " ".join(chunks[1].content.split()[:4])
    assert head in chunks[0].content[-200:]
    assert len({c.content_hash for c in chunks}) == len(chunks)
    fixed = chunk(doc, size=50, overlap=10, parser="fixed")
    assert all(c.heading_path == "" for c in fixed)
    assert tokens("abcd" * 10) == 10


def test_chunk_ids_are_deterministic() -> None:
    kb, doc = uuid.uuid4(), uuid.uuid4()
    assert chunk_id(kb, doc, 3, b"h") == chunk_id(kb, doc, 3, b"h")
    assert chunk_id(kb, doc, 3, b"h") != chunk_id(kb, doc, 4, b"h")


@pytest.mark.ac("US-1.16.1/1")
def test_rrf_rewards_agreement() -> None:
    a, b, c, d = (uuid.uuid4() for _ in range(4))
    fused = rrf([a, b, c], [c, d, a])
    assert fused[0] in (a, c)
    assert set(fused) == {a, b, c, d}
    assert fused.index(d) > fused.index(a)


@pytest.mark.ac("US-1.16.2/1")
def test_bm25_query_always_filters_project_kb_and_acl() -> None:
    project, kb = uuid.uuid4(), uuid.uuid4()
    q = bm25_query("hello", project, [kb], ["finance"])
    filters = q["query"]["bool"]["filter"]
    assert {"term": {"project_id": str(project)}} in filters
    assert {"terms": {"kb_id": [str(kb)]}} in filters
    assert {"terms": {"acl_groups": sorted(["finance", PUBLIC])}} in filters
    assert q["track_total_hits"] is False
    assert acl_terms([]) == [PUBLIC]
    assert reader_groups([]) == [PUBLIC]


def test_citations_and_context_block() -> None:
    assert cited_numbers("A [2] and [1][2], not [9] or [0].", 3) == [2, 1]
    chunk_ = RetrievedChunk(
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "m.pdf", "Body", "H", 4, 1, 0.5, 0.5
    )
    assert context_block([chunk_]) == "<context>\n[1] (m.pdf, page 4 — H)\nBody\n</context>"


def test_pipeline_defaults_follow_the_spec() -> None:
    cfg = PipelineConfig(collection_id=uuid.uuid4())
    assert (cfg.search_method, cfg.top_k, cfg.candidates, cfg.similarity_threshold) == (
        "hybrid",
        6,
        40,
        0.30,
    )
    assert (cfg.chunk_size, cfg.chunk_overlap, cfg.context_budget) == (512, 64, 6000)
    with pytest.raises(ValueError, match="overlap"):
        PipelineConfig(collection_id=uuid.uuid4(), chunk_size=100, chunk_overlap=100)


@pytest.mark.ac("US-1.14.1/2")
async def test_upload_body_limit_rejects_before_reading() -> None:
    called = False

    async def app(scope: Any, receive: Any, send: Any) -> None:
        nonlocal called
        called = True

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "path": "/api/v1/projects/x/collections/y/documents",
        "headers": [(b"content-length", b"200000000")],
    }
    await BodyLimit(app)(scope, receive, send)
    assert not called
    assert sent[0]["status"] == 413
