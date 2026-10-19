"""Chunking (spec "RAG design": structure-aware, token-bounded, title/heading prefix).

Node parsers:

- `structure` (default): a chunk never crosses a heading; within a section, paragraphs are
  packed up to the chunk size and long paragraphs are split by sentence, then by word.
- `recursive`: the same packing over the whole document, headings ignored as boundaries.
- `fixed`: fixed windows of words.

Token counts use the embedding model's tokenizer when the registry names one (`count`); without
it they are estimated (about 4 characters per token, never fewer than the word count).
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from forge.rag.parsing import Block, ParsedDocument

_SENTENCE = re.compile(r"(?<=[.!?;:])\s+(?=\S)")
_WORD = re.compile(r"\S+")


def tokens(text: str) -> int:
    """The estimate used when no tokenizer is configured."""
    return max(math.ceil(len(text) / 4), len(_WORD.findall(text)))


Count = Callable[[str], int]


@dataclass(frozen=True)
class ChunkDraft:
    ordinal: int
    content: str
    heading_path: str
    page: int | None
    token_count: int
    embed_text: str
    content_hash: bytes


@dataclass
class _Piece:
    text: str
    page: int | None
    heading: str


def _split_long(text: str, limit: int, tokens: Count) -> list[str]:
    """Split one paragraph that exceeds `limit` tokens: sentences first, then words."""
    if tokens(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for sentence in _SENTENCE.split(text):
        if tokens(sentence) > limit:
            if current:
                parts.append(current)
                current = ""
            words = sentence.split()
            window: list[str] = []
            for word in words:
                if window and tokens(" ".join([*window, word])) > limit:
                    parts.append(" ".join(window))
                    window = []
                window.append(word)
            if window:
                current = " ".join(window)
            continue
        candidate = f"{current} {sentence}".strip()
        if current and tokens(candidate) > limit:
            parts.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def _tail(text: str, overlap: int, tokens: Count) -> str:
    """The last `overlap` tokens (approximately) of `text`, cut at a word boundary."""
    if overlap <= 0:
        return ""
    words = text.split()
    tail: list[str] = []
    for word in reversed(words):
        if tokens(" ".join([word, *tail])) > overlap:
            break
        tail.insert(0, word)
    return " ".join(tail)


def _sections(doc: ParsedDocument, by_heading: bool) -> Iterable[list[_Piece]]:
    path: list[tuple[int, str]] = []
    section: list[_Piece] = []
    for block in doc.blocks:
        if block.kind == "heading":
            level = max(block.level, 1)
            path = [(lvl, t) for lvl, t in path if lvl < level]
            path.append((level, block.text))
            if by_heading and section:
                yield section
                section = []
            continue
        heading = " > ".join(t for _, t in path)
        section.append(_Piece(block.text, block.page, heading))
    if section:
        yield section


def _pack(pieces: list[_Piece], size: int, overlap: int, tokens: Count) -> Iterable[_Piece]:
    current: list[_Piece] = []
    current_tokens = 0
    carry = ""
    for piece in pieces:
        for part in _split_long(piece.text, max(size - overlap, size // 2), tokens):
            n = tokens(part)
            if current and current_tokens + n > size:
                text = "\n\n".join(p.text for p in current)
                yield _Piece(text, current[0].page, current[0].heading)
                carry = _tail(text, overlap, tokens)
                current = [_Piece(carry, piece.page, piece.heading)] if carry else []
                current_tokens = tokens(carry) if carry else 0
            current.append(_Piece(part, piece.page, piece.heading))
            current_tokens += n
    if current and any(p.text != carry for p in current):
        yield _Piece("\n\n".join(p.text for p in current), current[0].page, current[0].heading)


def _fixed(doc: ParsedDocument, size: int, overlap: int, tokens: Count) -> Iterable[_Piece]:
    """Windows of `size` tokens (word-aligned), each starting `overlap` tokens before the end
    of the previous one."""
    words: list[tuple[str, int | None, int]] = []
    for block in doc.blocks:
        words += [(w, block.page, max(1, tokens(" " + w))) for w in block.text.split()]
    start = 0
    while start < len(words):
        end, used = start, 0
        while end < len(words) and (used + words[end][2] <= size or end == start):
            used += words[end][2]
            end += 1
        window = words[start:end]
        yield _Piece(" ".join(w for w, _, _ in window), window[0][1], "")
        if end >= len(words):
            break
        back, kept = end, 0
        while back > start + 1 and kept + words[back - 1][2] <= overlap:
            back -= 1
            kept += words[back][2]
        start = back


def chunk(
    doc: ParsedDocument,
    *,
    size: int = 512,
    overlap: int = 64,
    parser: str = "structure",
    count: Count = tokens,
) -> list[ChunkDraft]:
    if parser == "fixed":
        pieces = list(_fixed(doc, size, overlap, count))
    else:
        pieces = []
        for section in _sections(doc, by_heading=parser == "structure"):
            pieces += list(_pack(section, size, overlap, count))
    drafts: list[ChunkDraft] = []
    for piece in pieces:
        content = piece.text.strip()
        if not content:
            continue
        # Context enrichment: the title and heading path are embedded, not stored in content.
        prefix = "\n".join(p for p in (doc.title, piece.heading) if p)
        embed_text = f"{prefix}\n\n{content}" if prefix else content
        drafts.append(
            ChunkDraft(
                ordinal=len(drafts),
                content=content,
                heading_path=piece.heading,
                page=piece.page,
                token_count=count(content),
                embed_text=embed_text,
                content_hash=hashlib.sha256(embed_text.encode()).digest(),
            )
        )
    return drafts


__all__ = ["Block", "ChunkDraft", "chunk", "tokens"]
