# Forge – Specification analysis

_Input: [docs/specs.md](../specs.md) (version 2026-10-16). Date of analysis: 2026-10-16._

This document lists what the specification leaves ambiguous, where it contradicts itself, and what must be decided before architecture work can be frozen. Each item carries a **proposed decision** so work can proceed; items marked **[BLOCKING]** need explicit sign-off from the product owner before the related epic starts.

---

## 1. Summary

The specification is solid on *what* (feature scope, data model sketch, RAG defaults, acceptance criteria) and on the technology envelope (Python/FastAPI, Angular/PrimeNG, PostgreSQL). The main problems are:

1. **Three different role models** and two names for the tenant (project/workspace) across Part 1 and Part 2.
2. **"Forge runs no model" is contradicted** by Docling, Presidio, Llama Guard/Prompt Guard, which all run ML models locally.
3. **Orchestration overlap**: LangGraph checkpoints *and* Temporal both claim durability for runs.
4. **Three different node vocabularies** for workflows (F5.3, use case JSON, Part 2 Graphical pattern), and two overlapping entities (`workflow` vs `use_case`).
5. **`.env` "complete list" is not complete**: Redis, MinIO, Temporal and Langfuse need connection info that the spec does not place anywhere.
6. **Duplicate/conflicting entity definitions** (`run`, `run_step`, `approval`) and an impossible rule ("every entity has `project_id`").
7. **Schedule risk**: Phase 1 (6–8 weeks, 3–4 devs) contains IAM with LDAP+OIDC+TOTP, a multi-provider gateway, prompt studio, RAG, an agent runtime, tracing *and* the Angular shell. Realistic estimate is 10–12 weeks.

---

## 2. Contradictions

| # | Topic | Where | Conflict | Proposed decision |
| --- | --- | --- | --- | --- |
| C1 **[BLOCKING]** | Role model | F8.3, *Workspace roles*, Part 2 *Role hierarchy* | F8: Org Admin, Project Admin, Builder, Operator, User, Viewer. Workspace table: Workspace Admin, Builder, Operator, End User, Viewer. Part 2: Super Admin, Project Admin, custom Role Names bound to Role Types. | Two levels. **Platform**: `super_admin` (single org in MVP, see C2). **Project**: Role Types = `project_admin`, `builder`, `operator`, `end_user`, `viewer` (the workspace table is the permission matrix). Role Names = project-defined labels bound to one Role Type, carrying catalog access. "User" in Part 2 = `end_user`. |
| C2 | Organization level | F8.1 vs Part 2 | F8 has Organization → Project; Part 2 and Admin Console only know Platform → Project. | Keep `organization` table with exactly one row in MVP (schema ready for multi-org later). Super Admin = org admin. |
| C3 **[BLOCKING]** | "Forge does not host or run any model" | Goal & scope vs stack | Docling (layout model, TableFormer, OCR), Presidio (spaCy NER), Llama Guard / Prompt Guard (LLMs), "classifier" for banned topics/toxicity all run models. | Rephrase the principle: *Forge runs no generative, embedding or reranker model.* Allowed in-process: small CPU models bundled in images (Docling layout/OCR, spaCy for Presidio). Llama Guard / Prompt Guard / toxicity classifiers are **external endpoints** registered as a new model type `Guard/Classifier`. |
| C4 **[BLOCKING]** | Durable execution | Stack table, *Execution* | LangGraph (checkpoints in PG, HITL) and Temporal (durable jobs) both provide durability; using both doubles state and failure modes. | **Decided (PO): keep both**, with separate jobs, see [ADR-004](../adr/004-temporal-and-langgraph.md). Temporal owns run lifecycle, the use case graph, HITL waits, schedules and ingestion; LangGraph runs the agent reasoning loop inside one Temporal activity, with PostgreSQL checkpoints and `interrupt()` bridged to Temporal signals. |
| C5 | Workflow node types | F5.3, use case JSON, Part 2 Graphical | F5.3: LLM, tool, agent, condition, loop, parallel, human approval, code. JSON: start, agent, tool, llm, router, for_each, while, human_approval, end. Part 2: Start, Agentic, Router, For Loop, While Loop, Tool. HITL is both a node and an `hitl: true` flag. | Canonical set = JSON list + `code` (Phase 3, needs sandbox). `condition` ≡ `router`; `loop` ≡ `for_each`/`while`; `parallel` is implicit (nodes with the same `after`). Keep both HITL forms: `human_approval` node, and `hitl: true` on agent/tool nodes. |
| C6 | `workflow` vs `use_case` | Data model, F5 | Both are versioned graph containers. | Single concept: **use case** (`use_case` / `use_case_version`). F5's "workflow" is a use case with `pattern=graph`. Drop the `workflow` tables. |
| C7 | `run`, `run_step`, `approval` | Data model vs *Data model additions* | Two definitions with different fields. | Merge: `run(project_id, session_id NULL, use_case_version_id NULL, agent_version_id NULL, trigger, status, inputs, outputs, tokens_in, tokens_out, cost, trace_id, started_at, ended_at)`; `run_step(run_id, node_id, attempt, type, status, input, output, tokens, cost, span_id)`; `approval(run_step_id, assignee, decision, edited_output, comment, decided_by, decided_at, token_hash)`. Partition `run_step` by month. |
| C8 | "Every entity has `project_id`" | Data model | `organization`, `user`, `group`, platform-level `provider`/`model`, built-in `catalog`, `audit_event` (platform actions) cannot belong to a project. | Three scopes: *platform* (no project_id), *dual* (`project_id NULL` = platform-wide: provider, model, guardrail_policy, connector, catalog), *project* (NOT NULL + RLS). |
| C9 | Project deletion | Part 1 *Lifecycle* vs Part 2 *Project creation* | Part 1: soft delete 30 days then purge. Part 2: "deletes the project's users, data and operations". | Soft delete 30 days; users are platform entities, only memberships are removed. Purge job deletes data, embeddings, objects. |
| C10 | Public URL | `.env` vs Admin Console *System* | Both hold the public URL. | `.env` value is the boot default; the GUI value overrides it once set. |
| C11 | "Semantic cache" | F1.7 | Defined as prompt + parameter **hash**, which is an exact-match cache. | MVP: exact-match cache (hash). True semantic cache (embedding similarity) → backlog. |
| C12 | Project token vs API keys | Part 2 *Project creation* vs F8.6 | One auto-generated project token vs many scoped, expiring keys. | The "project token" is the first API key, created automatically with default scopes; same table, same rules. |
| C13 | Speech model registry | Part 2 *Model Configuration* vs Out of scope | Speech registry required, voice input out of scope. | Registry supports type `speech` (CRUD + Test Connection) but no feature consumes it in MVP. |
| C14 | API prefix | F1.3 vs *Execution* | `/v1/chat/completions` vs `/api/v1/use-cases/...`. | Management/public REST under `/api/v1/...`; OpenAI-compatible surface under `/v1/...` (so SDKs work with `base_url=https://forge/v1`). |

## 3. Gaps (not specified)

| # | Gap | Impact | Proposed handling |
| --- | --- | --- | --- |
| G1 **[BLOCKING]** | `.env` lacks Redis, MinIO, Temporal, Langfuse/OTLP connection info, yet "no other setting is read from `.env`". | Cannot boot. | Infrastructure endpoints of *bundled* services (Redis, MinIO, Temporal) are **deployment wiring**, provided by Compose/Helm as env vars with fixed defaults (`redis://redis:6379`, …), documented as "infra, not application config". Observability endpoints (Langfuse/OTLP) stay GUI-configured as specified. Needs PO sign-off because it relaxes the "complete list". |
| G2 | Tools Studio, Agentic AI Studio, MCP Studio, Governance & Evaluation Studio: in the menu, not described in Part 2. | UI scope undefined. | Derive screens from F4, F5, F6, F7 using the generic New/Library pattern; PO to write user stories (planning task PO-3). |
| G3 | Planner Agent (Build Your Own) | Core of use-case builder, no design. | Planner = system agent using a configured LLM with structured output (JSON schema of the use-case definition); its catalog = published agents visible to the project (own + catalog-published), **not** every agent on the platform (would break isolation). |
| G4 | Agent Garden | Mentioned, not defined. | = Agent Library filtered to published agents + catalog-shared agents. |
| G5 | Router fan-out + `after_mode` joins | Several router sections can be true; joins with `all` on skipped branches deadlock. | Dead-path elimination: a branch not taken marks its nodes `skipped`; `after_mode=all` is satisfied by completed-or-skipped parents, with at least one completed. |
| G6 | Built-in catalogs Forge.Software / ITOps / BizOps vs vertical modules out of scope | Empty catalogs at launch. | Seed them empty (or with 1–2 demo use cases); verticals fill them later. |
| G7 | Master key rotation | Secrets unrecoverable or never rotated. | Envelope encryption: per-secret DEK encrypted with KEK; `key_id` stored with ciphertext; rotation command re-wraps DEKs. |
| G8 | `FORGE_ADMIN_PASSWORD` stays in `.env` after first boot | Credential at rest in plain text. | Forced change on first login (already specified) + startup warning if the variable is still set after the admin has changed password. |
| G9 | Quota enforcement with streaming | Tokens unknown until the stream ends. | Reserve `max_tokens` at request start in Redis, reconcile on completion; daily counters in Redis, persisted to PG. |
| G10 | HNSW with filters (kb_id, ACL, RLS) | Post-filtering loses recall. | Per-KB partial HNSW index + pgvector ≥ 0.8 `hnsw.iterative_scan = relaxed_order`. Benchmark early (risk R3). |
| G11 | Embedding dimension limits | pgvector HNSW on `vector` max 2000 dims. | Use `halfvec` for > 2000 dims (up to 4000); validate at model registration. |
| G12 | RLS with pooled connections | `current_setting('app.project_id')` leaks across requests if set at session level. | `SET LOCAL app.project_id` inside every transaction; app DB role without `BYPASSRLS`; separate `forge_admin` role for cross-project jobs (purge, platform dashboards). Use `current_setting(..., true)` so missing values deny instead of erroring. |
| G13 | Injection test set (≥ 90% blocked) | Acceptance criterion with no dataset. | QA builds an internal set (public corpora + Italian/English handcrafted) in Phase 1; versioned in repo. |
| G14 | EULA text and acceptance tracking | Part 2 requires EULA on first login. | `user.eula_accepted_version`, text in Admin Console *System*. |
| G15 | i18n | Not mentioned; user base likely Italian/English. | Angular i18n from day 1, EN default, IT second. |
| G16 | Data Collections vs RAG `source` | Two ingestion paths. | A Data Collection is a `source` of type `collection`; RAG pipelines reference collections; file upload in RAG Studio creates an implicit collection. |

## 4. Technology concerns

| # | Concern | Detail | Recommendation |
| --- | --- | --- | --- |
| T1 | Langfuse v3 self-hosted needs ClickHouse, Redis and S3 | Conflicts with "PostgreSQL as the single database"; heavy for single-node/air-gapped. | Make Langfuse **optional**: traces go to OTLP; Forge stores its own per-run/step trace data in PG (needed anyway for the run views and cost totals). Langfuse/Phoenix are external sinks configured in the GUI. |
| T2 | pg_search (ParadeDB) | Needs ParadeDB image or extension build; license AGPL-3.0; one BM25 index per table. | Use ParadeDB PostgreSQL image in Compose/Helm; keep a native FTS fallback behind the same retriever interface (spec already lists it). Legal check on AGPL for distribution (Security/PO). |
| T3 | Temporal | Adds a server cluster + its own schema to operate. | Accept (strong fit for HITL waits, schedules, crash recovery); run with PostgreSQL persistence in a separate database on the same cluster. |
| T4 | gVisor | Needs `runsc` on host/nodes; not available on every managed K8s/OpenShift. | Phase 3 as specified; design the sandbox as a separate runner service with a pluggable backend (gVisor, Firecracker, plain container with seccomp for dev). |
| T5 | Versions | Spec says Angular 19+; current Angular is newer. | Pin the current Angular LTS and matching PrimeNG at project start; same for Python 3.12+ (consider 3.13). |
| T7 | PrimeNG licence | PrimeNG ≥ 22, `@primeuix/themes` ≥ 3, PrimeIcons ≥ 8 moved to a commercial PrimeUI licence (found 2026-10-16). Conflicts with "everything is open source". | Angular 21 + PrimeNG 21 (MIT), see ADR-014. PO decision. |
| T8 | MinIO images | MinIO community images not available from Docker Hub / quay.io. | S3 API only; SeaweedFS in Compose, see ADR-015. PO decision. |
| T6 | LiteLLM as a library | Large dependency tree, very frequent releases: high supply-chain exposure. | Pin exact versions, hash-locked; wrap behind an internal `ProviderAdapter` interface so it can be replaced. |

## 5. Risks

| # | Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- | --- |
| R1 | Phase 1 scope vs 6–8 weeks | High | High | Re-plan to ~10–12 weeks or move OIDC/LDAP and Help Me Write to Phase 2 (see plan). |
| R2 | RLS mistakes → cross-tenant data leak | Medium | Critical | RLS on every project table from the first migration; automated tenant-isolation test suite run in CI on every table. |
| R3 | Hybrid retrieval < 300 ms p95 on 1M chunks with filters | Medium | Medium | Benchmark spike in Phase 0 on synthetic 1M-chunk corpus. |
| R4 | Gateway overhead < 50 ms p95 with guardrails (Presidio) in the path | Medium | Medium | Guardrails measured separately; Presidio in a worker pool; budget per check. |
| R5 | Visual graph editor (Foblex Flow) maturity | Medium | Medium | UI spike in Phase 0. |
| R6 | Orchestration choice reversed late | Low | High | Decide ADR-004 in Phase 0 with a working spike (kill worker mid-run → resume). |

## 6. Questions for the Product Owner

**Answers (2026-10-16)**

| Q | Answer | Recorded in |
| --- | --- | --- |
| 1 Role model | Approved | ADR-003 |
| 2 "No models in Forge" reworded, `guard` model type | Approved | ADR-013 |
| 3 Infra wiring outside `.env` | Approved | ADR-007 |
| 4 Durability | **Keep both Temporal and LangGraph** (proposal was Temporal only) | ADR-004: Temporal = run/graph durability, LangGraph = agent loop inside activities |
| 5 Langfuse | Optional sink (interpreted from "yes" as acceptance of the proposal) | ADR-009 |
| 6 Phase 1 | 10–12 weeks (interpreted from "yes" as acceptance of the first option) | Plan §3 |
| 7 UI languages | **Italian and English** (confirmed 2026-10-16) | ADR-012, US-1.4.3 |
| 8 pg_search AGPL | Open | ADR-008 |

**Original questions**

1. Approve the role model in C1 (Super Admin + 5 project Role Types + custom Role Names)?
2. Approve the reworded "no models in Forge" principle (C3) and the new `Guard/Classifier` model type?
3. Approve infra wiring via Compose/Helm env vars outside the `.env` "complete list" (G1)?
4. Approve Temporal as the single durability layer, without LangGraph (C4)?
5. Is Langfuse a hard requirement, or acceptable as an optional external sink (T1)?
6. Phase 1: extend to ~10–12 weeks, or cut scope (move LDAP/OIDC, Help Me Write, Playground multi-model to Phase 2)?
7. Required UI languages (G15)?
8. Is the AGPL license of pg_search acceptable for how Forge will be distributed (T2)?
