# Phase 1 exit review

Date: 2026-10-19. Checked against the exit criteria in [phase-1.md](../requirements/phase-1.md#phase-1-exit-criteria).

## Verdict

Phase 1 meets its four exit criteria. Three items need the PO or QA before Phase 2 relies on them (see "Open items").

## Exit criteria

| # | Criterion | Result | Evidence |
| --- | --- | --- | --- |
| 1 | All **M** stories accepted, tests green | Met locally | Backend unit 134 passed; integration 74 passed (4 min 28 s), including agents, gateway, RAG, bundles; frontend 32 passed; ruff, import contracts, ng lint and mypy (108 files) clean. CI not run on this working tree: M1.5 is not committed yet |
| 2 | Demo script end to end | Met | `scripts/demo-m15.py` with Groq and Cloudflare: prompt, document, published RAG, agent with RAG and `current_time`, three chat runs with citations, run view with cost, alias switched to a second model with the same agent version ([m1.5-agent.md §11](../architecture/m1.5-agent.md)) |
| 3 | Tenant isolation and secret redaction suites green | Met | `test_tenant_isolation.py` (4) in the integration run above; redaction tests in the unit run |
| 4 | Spikes documented, NFR baselines measured | Met, with a caveat | See below |

## NFR baselines

| NFR | Target | Measured | Source |
| --- | --- | --- | --- |
| Gateway overhead | < 50 ms p95 | about 4 ms p95 (LiteLLM against a mock) | [E0.5d](../spikes/e05d-litellm-providers.md) |
| Hybrid retrieval, 1M chunks | < 300 ms p95 | 159 ms p95 with OpenSearch BM25 + pgvector, 0 isolation leaks | [E0.5e](../spikes/e05e-opensearch-bm25.md) (supersedes the 39 ms pg_search figure of [E0.5b](../spikes/e05b-hybrid-retrieval.md)) |
| Upload to answer (AC3) | < 60 s | 38.3 s for a 20-page PDF | [m1.4-rag.md §10](../architecture/m1.4-rag.md) |

Caveat: both latency figures come from spikes with synthetic load, not from the running product. Gateway overhead through the full `/v1` pipeline (auth, quota, cache, `llm_call` write) and retrieval at 1M chunks in the shipped schema are re-measured in E3.7.

## Acceptance criteria status after Phase 1

| AC | Status |
| --- | --- |
| AC1 (alias switch) | Done, demo |
| AC3, AC4 | Done, M1.4 tests |
| AC9 | Partial: per-run reconciliation tested; dashboards in M2.6 |
| AC10 | Done, M1.2 |
| AC8 | Prepared: dataset and heuristic baseline only (94% on the generated set, 2.5% on the hand-written holdout) |

## Open items

1. **AC8 holdout.** The heuristic detector blocks 2.5% of the hand-written holdout against a 90% target. Expected, since the Guard-model path arrives in M2.5, but QA must extend the holdout before any AC8 figure is quoted.
2. **Choices awaiting PO review** in [m1.5-agent.md §10](../architecture/m1.5-agent.md): agent prompt shape, memory of final answers only, default limits (8 calls, 50k tokens, 120 s, 1 USD), `http_get` off until hosts are listed, Builders chatting with drafts.
3. **Monaco timing** (chosen over textarea; recommended for Phase 2 start).
4. Langfuse profile validated by `docker compose config` only.
5. M1.5 work is uncommitted (94 changed paths); CI has not seen it.

## Next

Phase 2 starts with the G2 user stories (Tools, MCP, Governance), then M2.1 External identity and M2.2 Tools, per the [plan](02-implementation-plan.md).
