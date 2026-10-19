"""Exact token counting with a model's own tokenizer (PO decision 2026-10-19)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from forge.gateway import tokenizers
from forge.rag.chunking import chunk, tokens
from forge.rag.parsing import parse


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tokenizers, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(tokenizers, "_cache", {})
    monkeypatch.setattr(tokenizers, "_failed", {})
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path / "tiktoken"))


def test_missing_tokenizer_falls_back_to_the_estimate_and_says_so() -> None:
    counter = tokenizers.counter("hf:org/not-downloaded")
    assert counter.exact is False
    assert counter.count("one two three four five six") == tokens("one two three four five six")
    assert tokenizers.counter(None).exact is False
    assert tokenizers.counter("hf:org/x").count("") == 0


def test_failed_loads_are_retried_later(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake(name: str) -> object:
        calls.append(name)
        if len(calls) == 1:
            raise OSError("network down")
        return lambda text: len(text.split())

    monkeypatch.setattr(tokenizers, "_load_hf", fake)
    assert tokenizers.load("hf:a/b") is None
    assert tokenizers.load("hf:a/b") is None
    assert calls == ["a/b"]  # not hammered while it keeps failing
    monkeypatch.setattr(tokenizers, "RETRY_AFTER_S", 0)
    fn = tokenizers.load("hf:a/b")
    assert fn is not None
    assert fn("a b c") == 3
    assert time.monotonic() > 0


def test_unknown_kind_is_not_loadable() -> None:
    assert tokenizers.load("gguf:thing") is None


def test_chunking_uses_the_given_counter() -> None:
    doc = parse("md", b"# T\n\n" + b"word " * 400, None, "t.md")
    by_word = chunk(doc, size=100, overlap=10, count=lambda t: len(t.split()))
    assert all(c.token_count <= 100 + 10 for c in by_word)
    by_char = chunk(doc, size=100, overlap=10)  # default estimate: ~4 characters per token
    assert len(by_word) != len(by_char)
