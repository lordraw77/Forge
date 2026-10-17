"""`forge providers import-env`: register lab providers from well-known environment variables.

An explicit operator action for labs and demos, not configuration: the running platform never
reads these variables (ADR-007). Each run is idempotent: existing providers, models and aliases
(by name) are kept as they are. Paid providers are registered but left out of every alias, so
they are only used when called by name.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pydantic import SecretStr

from forge.core.context import SYSTEM
from forge.core.errors import ProblemError
from forge.gateway.catalog import Catalog
from forge.gateway.schemas import ModelIn, ProviderIn, ProviderSettings

CHAT_CAPS = ["streaming", "tools", "json"]


@dataclass(frozen=True)
class SeedModel:
    name: str
    display_name: str
    model_id: str
    model_type: str = "chat"
    capabilities: tuple[str, ...] = tuple(CHAT_CAPS)
    context_window: int | None = None
    embedding_dims: int | None = None
    params: dict[str, Any] = field(default_factory=dict)
    cost_input: str = "0"
    cost_output: str = "0"
    timeout_s: int = 60


@dataclass(frozen=True)
class SeedProvider:
    name: str
    provider_type: str
    env: tuple[str, ...]  # all must be set; the first one is the API key unless keyless
    models: tuple[SeedModel, ...]
    base_url: str | None = None  # may use {ENV_VAR} placeholders
    keyless: bool = False
    paid: bool = False


CATALOGUE: tuple[SeedProvider, ...] = (
    SeedProvider(
        "Ollama (lab)",
        "ollama",
        ("OLLAMA_HOST",),
        (
            SeedModel("ollama-qwen2.5-1.5b", "Qwen 2.5 1.5B (Ollama)", "qwen2.5:1.5b",
                      context_window=32768, timeout_s=120),
            SeedModel("ollama-qwen3-8b", "Qwen 3 8B tools (Ollama)", "qwen3:8b-tool",
                      context_window=40960, timeout_s=300),
            SeedModel("ollama-nomic-embed", "nomic-embed-text (Ollama)", "nomic-embed-text:latest",
                      "embedding", (), embedding_dims=768, timeout_s=120),
            SeedModel("ollama-bge-m3", "BGE-M3 (Ollama)", "bge-m3:latest", "embedding", (),
                      embedding_dims=1024, timeout_s=120),
        ),
        base_url="{OLLAMA_HOST}",
        keyless=True,
    ),
    SeedProvider(
        "Groq",
        "openai_compatible",
        ("GROQ_API_KEY",),
        (
            SeedModel("groq-gpt-oss-20b", "GPT-OSS 20B (Groq)", "openai/gpt-oss-20b",
                      context_window=131072),
            SeedModel("groq-gpt-oss-120b", "GPT-OSS 120B (Groq)", "openai/gpt-oss-120b",
                      context_window=131072),
            SeedModel("groq-prompt-guard-86m", "Llama Prompt Guard 2 86M (Groq)",
                      "meta-llama/llama-prompt-guard-2-86m", "guard", ()),
        ),
        base_url="https://api.groq.com/openai/v1",
    ),
    SeedProvider(
        "Cerebras",
        "openai_compatible",
        ("CEREBRAS_API_KEY",),
        (
            SeedModel("cerebras-gpt-oss-120b", "GPT-OSS 120B (Cerebras)", "gpt-oss-120b",
                      context_window=131072),
            SeedModel("cerebras-qwen-3.8-27b", "Qwen 3.8 27B (Cerebras)", "qwen-3.8-27b"),
        ),
        base_url="https://api.cerebras.ai/v1",
    ),
    SeedProvider(
        "Mistral",
        "mistral",
        ("MISTRAL_API_KEY",),
        (
            SeedModel("mistral-small", "Mistral Small", "mistral-small-latest",
                      capabilities=("streaming", "tools", "json", "vision"),
                      context_window=131072),
            SeedModel("ministral-8b", "Ministral 8B", "ministral-8b-latest",
                      context_window=131072),
            SeedModel("mistral-embed", "Mistral Embed", "mistral-embed", "embedding", (),
                      embedding_dims=1024),
        ),
    ),
    SeedProvider(
        "OpenRouter (free models)",
        "openai_compatible",
        ("OPENROUTER_API_KEY",),
        (
            SeedModel("openrouter-nemotron-lightning", "Nemotron 3.5 Lightning (OpenRouter)",
                      "nvidia/nemotron-3.5-lightning:free", capabilities=("streaming",)),
            SeedModel("openrouter-gemma-4-26b", "Gemma 4 26B (OpenRouter)",
                      "google/gemma-4-26b-a4b-it:free", capabilities=("streaming",)),
        ),
        base_url="https://openrouter.ai/api/v1",
    ),
    SeedProvider(
        "NVIDIA NIM (cloud)",
        "nim",
        ("NVIDIA_API_KEY",),
        (
            SeedModel("nim-gpt-oss-20b", "GPT-OSS 20B (NIM)", "openai/gpt-oss-20b",
                      context_window=131072),
            SeedModel("nim-content-safety", "Nemotron 3.5 Content Safety (NIM)",
                      "nvidia/nemotron-3.5-content-safety", "guard", ()),
            SeedModel("nim-nemotron-embed-1b", "Nemotron 3 Embed 1B (NIM)",
                      "nvidia/nemotron-3-embed-1b", "embedding", (), embedding_dims=2048,
                      params={"extra_body": {"input_type": "query", "truncate": "END"}}),
        ),
    ),
    SeedProvider(
        "Cloudflare Workers AI",
        "openai_compatible",
        ("CLOUDFLARE_API_KEY", "CLOUDFLARE_ACCOUNT_ID"),
        (
            SeedModel("cf-llama-3.1-8b", "Llama 3.1 8B (Cloudflare)",
                      "@cf/meta/llama-3.1-8b-instruct-fp8", capabilities=("streaming",)),
            SeedModel("cf-bge-m3", "BGE-M3 (Cloudflare)", "@cf/baai/bge-m3", "embedding", (),
                      embedding_dims=1024),
        ),
        base_url="https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1",
    ),
    SeedProvider(
        "Google Gemini (paid)",
        "gemini",
        ("GEMINI_API_KEY",),
        (
            # Published list price per 1M tokens for 2.5 Flash-Lite at registration time.
            SeedModel("gemini-flash-lite", "Gemini 2.5 Flash-Lite (paid)",
                      "gemini-2.5-flash-lite",
                      capabilities=("streaming", "tools", "json", "vision"),
                      context_window=1048576, cost_input="0.10", cost_output="0.40"),
        ),
        paid=True,
    ),
)  # fmt: skip

# Platform defaults; each chain keeps only the models that were registered. Embedding chains
# hold one model family only (same vector space), never just the same dimensions.
ALIASES: dict[str, tuple[str, ...]] = {
    "default-chat": ("groq-gpt-oss-20b", "nim-gpt-oss-20b", "mistral-small",
                     "ollama-qwen2.5-1.5b"),
    "fast": ("ministral-8b", "cf-llama-3.1-8b", "ollama-qwen2.5-1.5b"),
    "embedding": ("ollama-bge-m3", "cf-bge-m3"),
    "guard": ("groq-prompt-guard-86m", "nim-content-safety"),
}  # fmt: skip


def _ollama_models(base_url: str) -> set[str] | None:
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/api/tags", timeout=5) as resp:  # noqa: S310
            return {m["name"] for m in json.load(resp)["models"]}
    except Exception:
        return None


async def import_env(catalog: Catalog, env: dict[str, str] | None = None) -> list[str]:
    """Create what is missing; return a report line per provider, model and alias."""
    env = dict(os.environ if env is None else env)
    report: list[str] = []
    providers = {p.name.lower(): p for p in await catalog.list_providers(SYSTEM, None)}
    models = {m.name.lower(): m for m in await catalog.list_models(SYSTEM, None)}
    for seed in CATALOGUE:
        if not all(env.get(v) for v in seed.env):
            report.append(f"skip     {seed.name}: {', '.join(seed.env)} not set")
            continue
        base_url = seed.base_url.format(**env) if seed.base_url else None
        provider = providers.get(seed.name.lower())
        if provider is None:
            provider = await catalog.create_provider(
                SYSTEM,
                None,
                ProviderIn(
                    name=seed.name,
                    provider_type=seed.provider_type,
                    base_url=base_url,
                    api_key=None if seed.keyless else SecretStr(env[seed.env[0]]),
                    config=ProviderSettings(
                        allow_http=bool(base_url and base_url.startswith("http://"))
                    ),
                ),
            )
            report.append(f"create   provider {seed.name}")
        else:
            report.append(f"keep     provider {seed.name}")
        available = (
            _ollama_models(base_url) if seed.provider_type == "ollama" and base_url else None
        )
        for m in seed.models:
            if m.name.lower() in models:
                report.append(f"keep     model {m.name}")
                continue
            if available is not None and m.model_id not in available:
                report.append(f"skip     model {m.name}: {m.model_id} not pulled on Ollama")
                continue
            try:
                created = await catalog.create_model(
                    SYSTEM,
                    None,
                    ModelIn(
                        name=m.name,
                        display_name=m.display_name,
                        provider_id=provider.id,
                        model_type=m.model_type,
                        model_id=m.model_id,
                        params=m.params,
                        context_window=m.context_window,
                        embedding_dims=m.embedding_dims,
                        cost_input=Decimal(m.cost_input),
                        cost_output=Decimal(m.cost_output),
                        capabilities=list(m.capabilities),
                        timeout_s=m.timeout_s,
                        max_retries=1,
                        all_projects=True,
                    ),
                )
            except ProblemError as exc:
                report.append(f"error    model {m.name}: {exc.title} {exc.detail or ''}")
                continue
            models[created.name.lower()] = created
            report.append(
                f"create   model {m.name}" + (" (paid: in no alias)" if seed.paid else "")
            )
    existing = {a.name.lower() for a in await catalog.list_aliases(SYSTEM, None) if a.model_ids}
    for alias, names in ALIASES.items():
        chain = [models[n].id for n in names if n in models]
        if alias in existing:
            report.append(f"keep     alias {alias}")
        elif chain:
            await catalog.set_alias(SYSTEM, None, alias, chain)
            report.append(
                f"create   alias {alias} -> {' > '.join(n for n in names if n in models)}"
            )
    return report
