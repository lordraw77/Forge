# Forge SDK and `forgectl`

A small Python client and command line for [Forge](../README.md). It needs only `httpx`.

```bash
pip install ./sdk                       # or: uv pip install ./sdk
export FORGE_URL=https://forge.example.com
export FORGE_API_KEY=fk_…               # a project API key
forgectl whoami
```

## Assets in Git

```bash
forgectl export forge/                  # prompts, RAG pipelines, agents, use cases, collections
git add forge && git commit -m "snapshot"

forgectl diff forge/ --exit-code        # CI: fails when the project and the files differ
forgectl apply forge/ --dry-run         # what an import would do
forgectl apply forge/                   # send only what changed
```

- One JSON file per item (`prompts/<name>.json`, …) plus `forge.json`; exporting twice gives
  identical files, so a diff shows real changes only.
- Items are matched by **name**. `apply` sends new items and items whose last version differs,
  as **new versions** (`--policy rename|skip` for the others). Nothing is ever deleted: items that
  exist only in the project are listed as `removed` by `diff`.
- Version numbers, notes, timestamps and tags are ignored when comparing; **tags (`prod`,
  `staging`) are never moved on an existing item**, so publishing stays a deliberate act in Forge.
- Files, chunks and embeddings are not part of an export (see ADR-018). No secrets are exported.
- `--config` adds model aliases, gateway limits, user groups and the guardrail policy; the key
  needs `project.manage`.

## What the key needs

| Command | Scope |
| --- | --- |
| `export`, `diff`, `apply` | `asset.write` (plus `project.manage` for `--config`) |
| `run` | `usecase.run` (and `run.read` to read the result) |
| `chat` | `llm.invoke` |

Create the key in Projects Studio → Project Info; its scopes are limited to what its creator
may do. `forgectl whoami` shows what a key really holds.

## From Python

```python
from forge_sdk import ForgeClient

with ForgeClient("https://forge.example.com", "fk_…") as forge:
    forge.chat("default-chat", [{"role": "user", "content": "Hello"}])
    run = forge.run_use_case("Contract review", {"contract": "…"})
    print(run["status"], run["outputs"])
    bundle = forge.export_bundle(versions="published")
    report = forge.import_bundle(bundle, dry_run=True)
```

For chat you can also point any OpenAI SDK at `https://forge.example.com/v1`.
