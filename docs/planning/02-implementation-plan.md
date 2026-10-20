# Forge – Implementation plan

_Inputs: [specs.md](../specs.md), [01-spec-analysis.md](01-spec-analysis.md), [ADRs](../adr/README.md). Status: approved baseline, 2026-10-16. Open: UI languages, pg_search AGPL._

---

## 1. Architecture baseline (proposed)

**Modular monolith + workers.** One backend codebase, deployed as several stateless processes; modules communicate through in-process interfaces, not HTTP. This keeps a 3–4 person team fast while meeting "stateless, horizontally scalable".

| Process | Role | Scales on |
| --- | --- | --- |
| `forge-api` | FastAPI: REST `/api/v1`, OpenAI-compatible `/v1`, SSE, auth | requests |
| `forge-worker-runs` | Temporal worker: use case / agent runs, HITL waits | concurrent runs |
| `forge-worker-ingest` | Temporal worker: parsing (Docling), chunking, embedding, re-sync | pages/s |
| `forge-sandbox` (Phase 3) | Python tool execution under gVisor | tool calls |
| `forge-web` | Angular SPA served by nginx | — |

Infrastructure: PostgreSQL 17 (pgvector image), OpenSearch for BM25 (ADR-016), Temporal (PG persistence, separate DBs), Valkey (rate limits, quotas, cache, SSE fan-out), S3-compatible object storage (SeaweedFS in Compose, ADR-015), OTel Collector → Prometheus/Loki/Grafana, optional Langfuse/OpenBao.

**Backend module layout** (`backend/src/forge/`)

```text
core/          config loader (DB + LISTEN/NOTIFY), db session + RLS, crypto, errors, ids (UUIDv7)
iam/           users, groups, auth (local/TOTP, LDAP, OIDC), sessions/JWT, RBAC, api keys
tenancy/       organization, projects, memberships, role types/names, quotas
admin/         admin console settings, audit log, EULA
gateway/       provider/model registry, LiteLLM adapter, routing, fallback, limits, cache
prompts/       templates, versions, render (sandboxed Jinja2), playground, help-me-write
rag/           collections, sources, ingestion pipeline, retriever, answer synthesis
tools/         tool registry, OpenAPI import, MCP client, SQL, built-in connectors
agents/        agent definitions/versions, agent loop, memory
usecases/      catalogs, use cases/versions, graph validation, graph engine, sessions, runs, approvals
guardrails/    policies, checks, pipeline
observability/ tracing, metrics, run/step cost accounting, feedback, dashboards API
mcp_server/    exposed MCP server (Phase 3)
```

**Repository layout**

```text
backend/   Python package, Alembic migrations, tests
frontend/  Angular workspace (app shell + feature libs per studio)
deploy/    compose/, helm/ (Phase 3), otel/, grafana/
scripts/   small idempotent helpers used by the Makefile
docs/      specs, planning, adr/, architecture, api, runbooks
sdk/       Python SDK + CLI (Phase 3)
```

## 2. ADRs

ADRs 001–013 are written and accepted (016 and 017 added on 2026-10-18, superseding the pg_search part of 008 and 014): see [docs/adr/](../adr/README.md). Key points: modular monolith (001), RLS with `SET LOCAL` (002), Super Admin + Role Types + Role Names (003), Temporal outside / LangGraph inside agent activities (004), gateway-only model access (005), envelope encryption (006), DB config + infra wiring via deployment env (007), pgvector + pg_search (008), Forge-stored traces with optional Langfuse (009), graph schema v1 (010), API conventions (011), Angular architecture (012), `guard` model type (013).

## 3. Phases

Durations assume 3–4 developers. Phase 1 is re-estimated (risk R1); the spec's 6–8 weeks holds only with the cuts listed under "Option B".

### Phase 0 – Inception (2 weeks)

Goal: decisions taken, skeleton running end to end, risky assumptions tested.

| Epic | Content | Owner roles |
| --- | --- | --- |
| E0.1 Requirements | ✅ PO answers analysis §6 (2026-10-16); user stories for Phase 1 ✅ ([phase-1.md](../requirements/phase-1.md)). Stories for the studios not specified in Part 2 (G2: Tools, MCP, Governance) move to the start of Phase 2, where those studios are built (PO confirmed 2026-10-17) | PO |
| E0.2 ADRs | ✅ ADR 001–013 written and accepted (2026-10-16); spec text updated for ADR-007 (infrastructure wiring) and ADR-013 (guard models) on 2026-10-17 | Architect, Docs |
| E0.3 Threat model | ✅ STRIDE over the logical architecture; security baseline (headers, CSRF, rate limits, dependency audit policy) ([threat-model.md](../security/threat-model.md)) | Security |
| E0.4 Skeleton | ✅ (2026-10-16) Monorepo, FastAPI health/readiness/version, Angular shell, Alembic baseline + RLS guard, Compose (ParadeDB, Valkey, SeaweedFS, Temporal), Makefile, CI (lint, types, tests, audits, image build, smoke, Trivy). Closed 2026-10-17: Compose profile `observability` (OTel Collector, Prometheus, Loki, Grafana; `make obs/up obs/smoke`) and licence gate in CI (`scripts/check-licenses.py`, policy `scripts/license-policy.json`) | DevOps, Engineer |
| E0.5 Spikes | (a) ✅ 2026-10-16, ADR-004 confirmed ([report](../spikes/e05a-temporal-langgraph.md)) — Temporal + LangGraph (ADR-004): kill worker during an agent tool loop → resume at the last LangGraph checkpoint; `interrupt()` → Temporal signal after a worker restart → `Command(resume)`; idempotent tool replay; (b) ✅ 1M-chunk hybrid retrieval with RLS + kb filter, measure p95 ([report](../spikes/e05b-hybrid-retrieval.md)); (c) ✅ Foblex Flow editor prototype ([report](../spikes/e05c-graph-editor.md)); (d) ✅ LiteLLM against vLLM / Ollama / OpenAI-compatible mocks ([report](../spikes/e05d-litellm-providers.md)); (e) ✅ 2026-10-18 BM25 on OpenSearch + pgvector hybrid at 1M chunks, ADR-016 confirmed ([report](../spikes/e05e-opensearch-bm25.md)) | Engineer, Optimizer |

Exit: ADRs accepted, spikes report numbers, `make run` starts the full stack locally. **Phase 0 closed on 2026-10-17**; the open PO decisions are listed in §6.

### Phase 1 – Foundations (10–12 weeks, approved by PO)

| Milestone | Epics | Delivers |
| --- | --- | --- |
| **M1.1 Platform core** (wk 1–3) ✅ 2026-10-17 | E1.1 Data foundation: org, project, user, membership, role, api_key, audit_event, settings; RLS on all project tables; tenant-isolation test harness<br>E1.2 Local auth: Argon2id, TOTP, JWT sessions, lockout, forced password change, EULA, bootstrap admin from `.env`<br>E1.3 Secrets & runtime config (ADR 006/007)<br>E1.4 UI shell: layout, side menu, project selector, login, profile | Super Admin creates projects and users; everything audited |
| **M1.2 LLM Gateway** (wk 3–6) ✅ 2026-10-17 ([design](../architecture/m1.2-llm-gateway.md)) | E1.5 Provider/model registry (all F1.2 fields), Test Connection, per-project enablement, aliases<br>E1.6 `/v1/chat/completions` + `/v1/embeddings`, SSE, fallback, retries, timeouts<br>E1.7 Quotas and rate limits (Redis reserve/reconcile), HTTP 429<br>E1.8 Exact-match response cache<br>E1.9 Admin Console: model providers; Projects Studio: Model Configuration | Acceptance criteria AC1 (first half), AC10 |
| **M1.3 Prompt Studio** (wk 5–8) ✅ 2026-10-18 ([design](../architecture/m1.3-prompt-studio.md)) | E1.10 Prompts + immutable versions, sandboxed Jinja2, variables, few-shot<br>E1.11 Test page with trace (latency, cost, usage), publish, library (Try/Edit/Duplicate/Delete)<br>E1.12 Playground side-by-side 2–4 models<br>E1.13 Help Me Write | Prompt lifecycle draft → test → publish |
| **M1.4 RAG on upload** (wk 6–10) ✅ 2026-10-19 ([design](../architecture/m1.4-rag.md)) | E1.14 Data Collections + S3 upload (SeaweedFS, ADR-015) (100 MB limit, type allowlist, per-file status/log/retry)<br>E1.15 Ingestion workflow on Temporal: Docling → structure-aware chunking → enrichment → embedding (batch 32) → index; dedup by SHA-256<br>E1.16 Retriever: vector / semantic / hybrid (RRF k=60), reranker, threshold, context budget, ACL filter<br>E1.17 Answer synthesis compact/refine with citations; RAG Studio New + Library | AC3, AC4 |
| **M1.5 Single agent + tracing** (wk 9–12) ✅ 2026-10-19 ([design](../architecture/m1.5-agent.md)) | E1.18 Agent definition/version (prompt, model alias, tools, KBs, limits); tool-calling loop; RAG retriever as tool; 1–2 built-in HTTP tools<br>E1.19 Chat UI with SSE, citations, feedback thumbs<br>E1.20 Run/step persistence, cost accounting, OTel spans, structured logs, Prometheus metrics, basic run view<br>E1.21 Injection test set v1 (prepares AC8) | AC1 (second half), AC9 (partial) |

E1.18 builds the agent as a LangGraph graph over a gateway-backed chat model adapter, executed by a Temporal activity (ADR-004), so the run path used by use cases in Phase 2 is the same from the start.

### Phase 2 – Agents and governance (8–10 weeks) → MVP

| Milestone | Epics |
| --- | --- |
| **M2.1 External identity** ✅ 2026-10-19 ([design](../architecture/m2.1-external-identity.md)) | E2.1 LDAP/AD (bind, filters, group sync), OIDC (Authlib, Entra/Keycloak/Google), group → Role Name mapping, Admin Console *Authentication* — AC2 |
| **M2.2 Tools** ✅ 2026-10-19 ([design](../architecture/m2.2-tools.md); E2.3 connectors low priority, not built) | E2.2 Tools Studio: OpenAPI import, HTTP tools with per-project credentials and egress allowlist; MCP client (stdio, HTTP/SSE); read-only SQL tool; test console + call log<br>E2.3 Built-in connectors (Jira, ServiceNow, GLPI, GitHub/GitLab, Slack, Teams, Zabbix, Prometheus, SMTP/IMAP) — prioritise with PO, not all needed for MVP |
| **M2.3 Use cases & graph engine** (a ✅ 2026-10-20: E2.4, E2.5, [design](../architecture/m2.3a-usecases.md); b ✅ 2026-10-20: E2.6, E2.7, [design](../architecture/m2.3b-graph-engine.md); c ✅ 2026-10-20: E2.8, [design](../architecture/m2.3c-builder.md); d ✅ 2026-10-20: E2.9, [design](../architecture/m2.3d-end-user.md)) | E2.4 Catalogs, categories, Role Name access control<br>E2.5 Use case model, versions, lifecycle, export/import bundle, rollback — AC13<br>E2.6 Graph JSON v1 validation + Forge graph engine on Temporal (for_each, while, router fan-out, joins, limits)<br>E2.7 Supervisor pattern<br>E2.8 Build Your Own: requirements page, Planner Agent, Foblex canvas, node drawer, test page with per-agent panes and timeline<br>E2.9 Catalog end-user view, sessions, run history, outputs download (DOCX/XLSX/PDF) — AC12 |
| **M2.4 HITL** (✅ 2026-10-20, [design](../architecture/m2.4-hitl.md)) | E2.10 Approval node + `requires_approval` tools + `hitl` flag; edit/reject/approve; email/webhook links with signed one-time tokens; notifications (SMTP, webhooks) — AC5, AC6 |
| **M2.5 Guardrails** | E2.11 Policy model, pipeline on input/output/tool args; Presidio PII masking; injection/jailbreak via Guard endpoint + heuristics; toxicity; banned topics; URLs; secret regex; JSON schema; grounding check; actions log/mask/block/rewrite/escalate; intervention log — AC7, AC8 |
| **M2.6 Analytics & connectors** | E2.12 Dashboards (usage, cost, errors, latency, guardrails, adoption), Model Insights for Super Admin — AC9<br>E2.13 Scheduled RAG sources: SMB/NFS, S3, Git, URL crawler, SQL, REST; incremental re-sync and propagated deletion<br>E2.14 Activity log (180 d), home recent activity |
| **M2.7 MVP hardening** | E2.15 Fresh-install test (AC11), full acceptance run, security review, performance baseline |

### Phase 3 – Enterprise (6–8 weeks)

E3.1 Helm chart (k3s, RKE2, OpenShift) + air-gapped bundle (image list, local registry) · E3.2 Prompt and RAG evaluations (datasets, evaluators, recall@k, faithfulness) · E3.3 Python SDK + CLI (GitOps import/export) · E3.4 Sandbox service (gVisor) + Python tools + `code` node · E3.5 Exposed MCP server + embeddable chat widget · E3.6 Long-term memory · E3.7 Load tests against NFR targets, PITR backups (RPO 15 min / RTO 1 h), DR runbook · E3.8 External vault (OpenBao) · E3.9 Async invocation with completion webhook, cron/queue triggers if not already done.

## 4. MVP acceptance criteria → epics

| AC | Criterion (short) | Epics | Test type |
| --- | --- | --- | --- |
| AC1 | New model from UI, Test Connection, alias switch between providers | E1.5, E1.6, E1.18 | E2E + integration with mock providers |
| AC2 | LDAP user sees only mapped projects | E2.1 | Integration (OpenLDAP container) |
| AC3 | Uploaded doc queryable < 60 s with page + source citations | E1.14–E1.17 | E2E, timed |
| AC4 | No chunks without ACL | E1.16 | Integration, negative tests |
| AC5 | Approval node stops, notifies, resumes via UI/API | E2.10 | E2E |
| AC6 | Worker killed mid-run resumes from last step | E2.6 (spike E0.5a) | Chaos test in CI |
| AC7 | PII masked before cloud model, logged | E2.11 | Integration |
| AC8 | ≥ 90% injection set blocked | E1.21, E2.11 | Evaluation job on the test set |
| AC9 | Steps/tokens/cost/latency per run; totals = sum | E1.20, E2.12 | Integration, reconciliation query |
| AC10 | 429 after quota | E1.7 | Integration |
| AC11 | Fresh install with only `.env` admin, rest via GUI, no restarts | E1.3, E2.15 | E2E on clean Compose |
| AC12 | Use case published, run from catalog, HITL, outputs, exact version | E2.4–E2.10 | E2E |
| AC13 | Editing published use case doesn't affect runs; rollback | E2.5 | Integration |

## 5. Working method (per epic)

The team workflow from `.claude/Claude.md` applies to every epic, in this order and with these artifacts:

1. **PO** – user stories + acceptance criteria in `docs/requirements/<epic>.md`
2. **Architect** – design notes / ADR updates in `docs/architecture/`
3. **DBA** – Alembic migrations (idempotent, zero-downtime) + RLS policies
4. **Security** – threat delta and controls checklist for the epic
5. **Engineer** – implementation
6. **Reviewer** → 7. **Engineer** fixes
8. **QA** – unit, integration, E2E; every AC mapped to a test
9. **Optimizer** – performance and resilience pass on hot paths
10. **SRE** – metrics, logs, spans, alerts, runbook entries
11. **DevOps** – Compose/Makefile/CI updates
12. **Docs** – README, API docs, operations docs updated

Definition of done: tests green in CI, tenant-isolation suite green, OpenAPI updated, docs updated, no high/critical findings from SCA.

## 6. Next steps

1. ~~PO answers~~ ✅ (Q7: Italian + English; Q8 AGPL still open).
2. ~~ADR 001–013~~ ✅
3. ~~Phase 1 user stories~~ ✅ [requirements/phase-1.md](../requirements/phase-1.md)
4. ~~Threat model v1~~ ✅ [security/threat-model.md](../security/threat-model.md)
5. ~~Skeleton (E0.4)~~ ✅
6. Spikes: ~~E0.5a Temporal + LangGraph~~ ✅, ~~E0.5b hybrid retrieval~~ ✅ (p95 39 ms at 1M), ~~E0.5d LiteLLM providers~~ ✅ (35/36, overhead 4 ms p95), ~~E0.5c graph editor~~ ✅ (Foblex Flow, 200 nodes in 0.65 s), ~~E0.5e OpenSearch BM25~~ ✅ (hybrid p95 159 ms at 1M, 0 isolation leaks). **Phase 0 spikes complete.**
7. PO decisions: ~~Q8 AGPL~~ → OpenSearch ([ADR-016](../adr/016-external-bm25-opensearch.md)), ~~ADR-014 PrimeNG~~ → NG-ZORRO on Angular 22 ([ADR-017](../adr/017-ng-zorro-angular-22.md)), both 2026-10-18; ~~ADR-015 object store~~ → SeaweedFS, external S3 or in-chart (2026-10-19).
8. ~~Phase 0 open items (OTel collector profile, licence check, spec text for ADR-007/013)~~ ✅ 2026-10-17.
9. ~~M1.2 LLM gateway~~ ✅ 2026-10-17.
10. ~~M1.3 Prompt Studio~~ ✅ 2026-10-18.
11. ~~M1.4 RAG on upload~~ ✅ 2026-10-19 (AC3: 38 s for a 20-page PDF), with the PO's additions of the same day: groups and roles for document access, exact tokenizers, embedded and download image variants, outbound/inbound proxy support, export/import bundles ([ADR-018](../adr/018-bundle-format.md)) and a dark theme. 
12. ~~M1.5 Single agent + tracing~~ ✅ 2026-10-19 ([design](../architecture/m1.5-agent.md)); PO decisions of the same day: Monaco for template editing (Phase 2), bundle format confirmed, M1.3 and M1.4 choices accepted, Langfuse as an optional profile (ADR-009 amended), Phase 1 at 10–12 weeks confirmed. Phase 1 exit review done 2026-10-19 ([report](03-phase-1-exit-review.md)). Next: Phase 2 (G2 user stories, then M2.1).

## 7. Backlog (small items, not tied to a milestone)

| Item | Origin | Notes |
| --- | --- | --- |
| **Agent chat: choose the version.** When an agent has more than one version, selecting it in the chat offers a version picker (default: the published `prod` version; Builders also see `draft` and older versions) | PO, 2026-10-19 | Today the chat runs the published version, or the latest for Builders ([m1.5-agent.md](../architecture/m1.5-agent.md)). Needs the version to travel with the conversation (`agent_run` already records it) and to be shown in the run view |
