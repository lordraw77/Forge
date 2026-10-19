"""Exact token counting with the model's own tokenizer (PO decision 2026-10-19).

A model in the registry may name its tokenizer:

- `hf:<org>/<repo>`: the `tokenizer.json` of a Hugging Face repository (e.g. `hf:BAAI/bge-m3`),
  loaded with the `tokenizers` library;
- `tiktoken:<encoding>`: an OpenAI encoding (e.g. `tiktoken:o200k_base`).

Files come from the local cache (`FORGE_MODELS_DIR`): baked into the image (`forge models fetch`
at build time, the default and air-gapped variant) or downloaded on first use when downloads are
allowed (the `-slim` variant; through the corporate proxy if one is configured). A tokenizer that
cannot be loaded falls back to the estimate, and callers are told (`exact=False`).
"""

from __future__ import annotations

import math
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forge.core.logging import get_logger

log = get_logger(__name__)

# Baked into every image by default (`forge models fetch`): the recommended embedding model and
# the OpenAI encodings. Others are downloaded on first use where downloads are allowed.
DEFAULT_TOKENIZERS = ("hf:BAAI/bge-m3", "tiktoken:cl100k_base", "tiktoken:o200k_base")
MODELS_DIR = Path(os.environ.get("FORGE_MODELS_DIR", "/app/models"))
_WORD = re.compile(r"\S+")
_lock = threading.Lock()
_cache: dict[str, Callable[[str], int]] = {}
_failed: dict[str, float] = {}  # spec -> monotonic time of the last failure
RETRY_AFTER_S = 300


def estimate(text: str) -> int:
    """About 4 characters per token, never fewer than the word count."""
    return max(math.ceil(len(text) / 4), len(_WORD.findall(text)))


def cache_dirs() -> dict[str, Path]:
    return {"hf": MODELS_DIR / "hf", "tiktoken": MODELS_DIR / "tiktoken"}


def _offline() -> bool:
    return os.environ.get("HF_HUB_OFFLINE", "0").lower() in ("1", "true", "yes")


def _load_hf(repo: str) -> Callable[[str], int]:
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer

    path = hf_hub_download(
        repo, "tokenizer.json", cache_dir=str(cache_dirs()["hf"]), local_files_only=_offline()
    )
    tok: Any = Tokenizer.from_file(path)
    return lambda text: len(tok.encode(text, add_special_tokens=False).ids)


def _load_tiktoken(name: str) -> Callable[[str], int]:
    os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(cache_dirs()["tiktoken"]))
    import tiktoken

    enc = tiktoken.get_encoding(name)
    return lambda text: len(enc.encode(text, disallowed_special=()))


def load(spec: str) -> Callable[[str], int] | None:
    """The counting function for `spec`, or None when it cannot be loaded. Failures are retried
    after a few minutes (a download may succeed later)."""
    with _lock:
        if spec in _cache:
            return _cache[spec]
        if time.monotonic() - _failed.get(spec, -RETRY_AFTER_S) < RETRY_AFTER_S:
            return None
        kind, _, name = spec.partition(":")
        try:
            fn = (
                _load_hf(name)
                if kind == "hf"
                else _load_tiktoken(name)
                if kind == "tiktoken"
                else None
            )
        except Exception as exc:  # missing file offline, network, unknown encoding
            log.warning("tokenizer.unavailable", tokenizer=spec, error=str(exc)[:300])
            _failed[spec] = time.monotonic()
            return None
        if fn is None:
            _failed[spec] = time.monotonic()
            return None
        _cache[spec] = fn
        return fn


def fetch(spec: str) -> None:
    """Download a tokenizer into the cache (build time, `forge models fetch`); raises on error."""
    kind, _, name = spec.partition(":")
    if kind == "hf":
        from huggingface_hub import hf_hub_download

        hf_hub_download(name, "tokenizer.json", cache_dir=str(cache_dirs()["hf"]))
    elif kind == "tiktoken":
        _load_tiktoken(name)("warm up")
    else:
        raise ValueError(f"unknown tokenizer kind in {spec!r}")


@dataclass(frozen=True)
class TokenCounter:
    """Counts tokens for one model; `exact` is False when falling back to the estimate."""

    spec: str | None
    exact: bool
    _fn: Callable[[str], int]

    def count(self, text: str) -> int:
        return self._fn(text) if text else 0


def counter(spec: str | None) -> TokenCounter:
    fn = load(spec) if spec else None
    if fn is None:
        return TokenCounter(spec, False, estimate)
    return TokenCounter(spec, True, fn)
