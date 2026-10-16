# ADR-013 – Guardrail models as external endpoints

- Status: Accepted (PO, 2026-10-16)
- Date: 2026-10-16

## Context

"Forge does not host or run any model" conflicts with Docling, Presidio (spaCy), Llama Guard / Prompt Guard and toxicity classifiers.

## Decision

- Principle reworded: **Forge runs no generative, embedding, reranker or guard/classifier model.** Small CPU models bundled as library dependencies are allowed in-process: Docling layout/table/OCR models, spaCy NER models for Presidio.
- New model type in the registry: **`guard`** (classifier endpoint). Supported shapes: OpenAI-compatible chat with a guard model (Llama Guard, served e.g. by vLLM), and a generic classification HTTP endpoint (`{text} → {labels: [{name, score}]}`, e.g. Prompt Guard or a toxicity model served by TEI).
- Guardrail checks and their engine:

  | Check | Engine |
  | --- | --- |
  | PII detect/mask | Presidio + spaCy (in-process, ingest/guard worker pool) |
  | Prompt injection / jailbreak | `guard` endpoint + heuristic rules |
  | Toxicity | `guard` endpoint |
  | Banned topics | list (in-process) + `guard` endpoint or LLM classifier |
  | Malicious URLs, secret leakage | regex / allowlists (in-process) |
  | JSON schema | in-process |
  | Grounding | LLM-as-judge via gateway |

- A check whose endpoint is not configured is reported as unavailable on the policy screen; the policy decides fail-open or fail-closed per rule (default fail-closed for `block` rules).
- Air-gapped images ship the spaCy and Docling model files; no download at runtime.

## Consequences

- Specs text in *Goal and scope* must be updated (Docs task).
- AC8 depends on a configured guard endpoint; the test environment includes one.
