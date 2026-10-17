# Phase 1 – User stories and acceptance criteria

_Owner: Product Owner. Baseline: [specs](../specs.md), [plan](../planning/02-implementation-plan.md), [ADRs](../adr/README.md). Date: 2026-10-16._

Conventions:

- IDs `US-<epic>.<n>`; epics as in the plan (E1.1 … E1.21).
- Acceptance criteria (AC) are testable; QA maps each one to at least one automated test (`@pytest.mark.ac("US-1.2.3/1")` in backend tests, `test.describe('US-…')` in Playwright).
- Priority: **M** must (Phase 1 exit), **S** should (can slip to early Phase 2 with PO approval).
- Personas: **SA** Super Admin, **PA** Project Admin, **B** Builder, **O** Operator, **EU** End User, **V** Viewer, **API** an API client using a project key.

---

## M1.1 Platform core

### E1.1 Data foundation

**US-1.1.1 (M) – Project isolation.** As a PA, I want my project's data to be invisible to other projects, so that teams cannot see each other's work.

1. Any query by `forge_app` without `app.project_id` set returns zero project-scoped rows.
2. A user who is a member of project A only receives `404` for any resource ID of project B (no existence leak).
3. CI isolation suite fails if any table with `project_id` lacks `FORCE ROW LEVEL SECURITY` and a policy.

**US-1.1.2 (M) – Projects.** As an SA, I want to create, edit, archive and delete projects and assign a Project Admin.

1. Create requires name (unique within org, 3–80 chars) and Project Admin; description optional.
2. On creation a project API key with default scopes is generated and shown once; Project Info shows its prefix and allows rotation.
3. Archived project: read-only, run/ingestion endpoints return `409`.
4. Delete requires a reason and typing the project name; project enters `deleted`, disappears from lists, is purged after 30 days; memberships removed, users kept.

**US-1.1.3 (M) – Audit log.** As an SA, I want every administrative action recorded immutably.

1. Create/update/delete on users, projects, roles, settings, models, providers, API keys writes an `audit_event` with actor, action, target, diff (secrets redacted), IP, timestamp.
2. `forge_app` has no `UPDATE`/`DELETE` privilege on `audit_event` (verified by test).
3. SA can filter by actor, action, target type and time range.

**US-1.1.4 (M) – Project members and roles.** As a PA, I want to invite users and assign Role Names.

1. Invite by username or email, up to 500 per request; unknown local users are rejected with a per-row error.
2. Role Names are created with name, Role Type, status; default Role Names exist for each Role Type.
3. Only PA can change a member's Role Name or remove a member; others get `403`.
4. Members list shows Full Name, Username, Email, Role Name, Last Active; partial-name search.

### E1.2 Local authentication

**US-1.2.1 (M) – Bootstrap admin.** As an operator, I want the first start to create the admin from `.env`.

1. With an empty `user` table, start creates the admin with `super_admin` and `must_change_password = true`.
2. With existing users, `FORGE_ADMIN_*` are ignored; if `FORGE_ADMIN_PASSWORD` is still set after the admin changed it, a warning is logged at every start.

**US-1.2.2 (M) – Login.** As a user, I want to log in with username or email and password.

1. Passwords hashed with Argon2id (parameters in config, test asserts the stored prefix `$argon2id$`).
2. Wrong credentials return the same generic error whether or not the user exists; response time difference < 50 ms p95.
3. After 5 failed attempts in 15 minutes, the account locks for 15 minutes (values configurable); login endpoint rate-limited per IP.
4. Session expires after 30 minutes of inactivity (configurable).
5. First login forces password change and EULA acceptance before any other API call (`403` with code `password_change_required` / `eula_required`).

**US-1.2.3 (M) – TOTP.** As a user, I want to enable TOTP; as an SA, I want to enforce it.

1. Enrolment shows QR + secret, confirmed by a valid code; 10 single-use recovery codes are shown once.
2. With TOTP enabled, login requires a valid code (±1 step window); reused codes are rejected.
3. When the SA enforces TOTP, users without it must enrol at next login.

**US-1.2.4 (M) – Password policy.** As an SA, I want to set a password policy.

1. Min length (default 12), breached/common password blocklist, no reuse of last 5.
2. Change Password available in the profile for local users only.

### E1.3 Secrets and runtime configuration

**US-1.3.1 (M) – Encrypted secrets.** As an SA, I want credentials stored encrypted and never shown again.

1. Secrets stored per ADR-006; DB dump contains no plaintext secret (test greps a dump).
2. APIs return `configured: true` and never the value; logs never contain it (redaction test).
3. `forge secrets rotate-kek` re-wraps all DEKs; all secrets decrypt afterwards.

**US-1.3.2 (M) – Live settings.** As an SA, I want setting changes to apply without restart.

1. Changing a setting in one API instance is visible to another instance within 2 s (LISTEN/NOTIFY test with two processes).
2. Every change creates a version in `setting_history` and an audit event.
3. Invalid values are rejected with field-level errors.

### E1.4 UI shell

**US-1.4.1 (M) – App shell.** As a user, I want a consistent layout.

1. Left menu with the 9 entries of the spec; entries hidden when the user lacks permission.
2. Project selector at top with search; last project remembered per user.
3. Profile area: initials, name, role, email, Change Password (local only), Log Out, Super Admin Console link for SA.
4. Works at 1280 px and down to 375 px width (menu collapses).

**US-1.4.2 (M) – Home.** As a user, I want a home page with my recent activity.

1. Greeting with user name; actions "Explore Use Cases Catalog" and "Build Your Own Use Case" (disabled with tooltip in Phase 1).
2. Last 15 days of own activity: 4 tiles + View All; empty state "No Recent Activity".

**US-1.4.3 (M) – Languages.** As a user, I want the UI in Italian or English.

1. Every user-facing string is translatable; the Italian translation is complete (CI fails on missing units).
2. Language is chosen from the profile and remembered per user; default from the browser, fallback English.
3. Backend problem+json `title` messages are translated by `code` in the UI, not on the server.

---

## M1.2 LLM Gateway

### E1.5 Provider and model registry

**US-1.5.1 (M) – Add a model.** As an SA (platform) or PA (project), I want to register an external model with all F1.2 fields.

1. Supported provider types: vLLM, llama.cpp, NVIDIA NIM, Ollama, OpenAI, Azure OpenAI, Anthropic, Bedrock, Gemini/Vertex, Mistral, generic OpenAI-compatible.
2. Model types: chat, embedding, reranker, speech, guard.
3. Embedding models require dimensions; > 4000 rejected, > 2000 flagged as `halfvec`.
4. Credentials are write-only (US-1.3.1).

**US-1.5.2 (M) – Test Connection.** As an admin, I want to test a model before saving.

1. Sends a minimal request of the model type (1-token chat, 1-string embedding, 1-pair rerank) and returns success, latency, and provider error text on failure.
2. Timeout = model timeout, capped at 30 s.

**US-1.5.3 (M) – Aliases and enablement.** As a PA, I want to map aliases to models and choose which models my project can use.

1. Aliases `default-chat`, `fast`, `embedding`, `rerank` plus custom ones, per project, with platform defaults.
2. A disabled model returns `403 model_not_enabled` for the project.
3. Changing an alias takes effect on the next request, without restart.
4. _Confirmed by PO (2026-10-17):_ the Super Admin enables platform models per project (F1.2 "Enabled projects"); the Project Admin maps aliases to the models available to the project and may register project-only providers and models.

### E1.6 OpenAI-compatible API

**US-1.6.1 (M) – Chat completions.** As an API client, I want `POST /v1/chat/completions` compatible with OpenAI SDKs.

1. Official OpenAI Python SDK works with `base_url=<forge>/v1` and a project key, for both `model=<alias>` and `model=<model alias name>`.
2. `stream: true` returns SSE chunks ending with `data: [DONE]`; usage is reported in the final chunk.
3. Tool calling and JSON mode are passed through when the model declares the capability, otherwise `400 capability_not_supported`.

**US-1.6.2 (M) – Embeddings.** `POST /v1/embeddings` with the same compatibility guarantees.

**US-1.6.3 (M) – Fallback and retries.** As a PA, I want an ordered fallback chain per alias.

1. On timeout, 5xx or connection error, retries with exponential backoff up to the model's retry count, then the next model in the chain.
2. 4xx (except 408/429) are not retried.
3. The response header `x-forge-model` reports the model that answered; the trace records every attempt.

### E1.7 Quotas and rate limits

**US-1.7.1 (M) – Project and user limits.** As a PA, I want tokens/day, requests/minute and max_tokens per call limits.

1. Exceeding tokens/day or requests/minute returns `429` with `Retry-After` (AC10).
2. `max_tokens` above the limit is clamped (or rejected if configured) and reported in the response header.
3. Concurrent streaming requests cannot overshoot the daily quota by more than one request's `max_tokens` (reserve/reconcile).

### E1.8 Response cache

**US-1.8.1 (S) – Exact-match cache.** As a PA, I want optional caching of identical requests.

1. Off by default; when on, identical (model, messages, params) within TTL return the cached response with header `x-forge-cache: hit`, cost 0.

### E1.9 Model screens

**US-1.9.1 (M) – Admin Console → Model providers** and **Projects Studio → Model Configuration** (LLM, Embedding, Speech tabs) with list, add, edit, delete, Test Connection. Delete of a model referenced by an alias or agent is blocked with the list of references.

---

## M1.3 Prompt Studio

### E1.10 Prompts and versions

**US-1.10.1 (M) – Create a prompt.** As a B, I want to write a prompt with system/user text, variables and few-shot examples.

1. Fields per Part 2 table; `{name}` in User Prompt creates a variable inline.
2. Variables: Name, Value, Is File Input, Is Required.
3. Description asked on first save.
4. Templates are rendered with sandboxed Jinja2: attribute access to internals, imports and filesystem are blocked (security test with known payloads).

**US-1.10.2 (M) – Immutable versions.** Every save creates a version; older versions are viewable, never editable; tags `draft`, `staging`, `prod`.

### E1.11 Test, publish, library

**US-1.11.1 (M) – Test a prompt.** As a B, I want Save & Test → enter variables → Proceed → result.

1. Missing required variables block Proceed.
2. Result shows output, latency, cost, total tokens, and a trace link with input, output and metadata.

**US-1.11.2 (M) – Publish and library.** As a B, I want to publish prompts and manage them in the Prompt Library.

1. Library lists Published and Draft, filter by status, time range, name search.
2. Row actions: Try, Edit, Duplicate (asks a new name), Delete (confirmation); Add/Remove from MCP shown disabled until Phase 3.
3. Delete of a prompt used by an agent is blocked with the list of references.

### E1.12 Playground

**US-1.12.1 (M) – Side-by-side.** As a B, I want to run the same prompt on 2–4 models and compare output, latency, tokens and cost. Runs execute in parallel; one model failing does not hide the others.

### E1.13 Help Me Write

**US-1.13.1 (S) – Generate a prompt.** As a B, I want System and User Prompt generated from a description.

1. Framework dropdown (default Free Form) with tooltips; if none chosen, the result states the recommended framework used.
2. Actions Generate, Insert, Re-generate; generation uses the project's `default-chat` alias and is traced.

---

## M1.4 RAG on upload

### E1.14 Data Collections

**US-1.14.1 (M) – Upload files.** As a B, I want to create a collection and upload files or folders.

1. Allowed types: PDF, DOCX, PPTX, XLSX, HTML, MD, TXT, PNG/JPG (OCR); others rejected with a per-file error.
2. 100 MB per request; larger requests rejected before upload completes.
3. Files are stored in MinIO under a project prefix; MIME sniffed, not trusted from the client.
4. Per-file and per-collection status Success / In-progress / Failed; failed files show timestamp and reason; Retry re-runs ingestion for that file.

**US-1.14.2 (M) – Manage collections.** View, Rename, Delete (deletes files, chunks and embeddings), Download file, Delete file.

### E1.15 Ingestion pipeline

**US-1.15.1 (M) – Ingest.** As a B, I want uploaded documents parsed, chunked, embedded and indexed automatically.

1. A 20-page text PDF is queryable within 60 s of upload completing on the reference environment (AC3).
2. Chunks carry document, page, heading path, metadata; defaults: 512 tokens, overlap 64, structure-aware, title/heading prefix.
3. Re-uploading an identical file does not re-embed unchanged chunks (SHA-256).
4. A worker killed during ingestion resumes; no duplicate chunks result.

**US-1.15.2 (M) – Knowledge base.** A knowledge base has one embedding model; its partial HNSW index is created on KB creation. Changing the embedding model triggers a full re-index; queries keep using the old index until the new one is complete.

### E1.16 Retrieval

**US-1.16.1 (M) – Search methods.** Vector, Semantic and Hybrid retrieval as specified (top 40 candidates, RRF k = 60, reranker, threshold 0.30, top_k 6, context budget 6,000 tokens), all values editable per pipeline.

**US-1.16.2 (M) – ACL.** As a user without access to a document's ACL groups, I receive no chunks from it (AC4). Empty ACL = visible to the whole project.

### E1.17 RAG Studio

**US-1.17.1 (M) – Configure and test.** As a B, I want to configure a RAG pipeline (Part 2 parameters), Save Configuration & Test, enter a query and see the answer with citations and a trace.

1. Every answer lists cited chunks with document name, page and a link that opens the document at that page.
2. If no chunk passes the threshold, the answer says no relevant information was found.
3. Response mode `compact` (M), `refine` (S).

**US-1.17.2 (M) – RAG Library.** Published/Draft filter; Try, Edit, Duplicate, Delete.

---

## M1.5 Single agent and tracing

### E1.18 Agent

**US-1.18.1 (M) – Define an agent.** As a B, I want an agent = prompt (one) + model alias + tools + RAG pipelines + limits.

1. Saving creates an immutable agent version; references are pinned to versions.
2. Limits: max steps, max tokens, timeout, max cost; exceeding any ends the run with status `failed` and reason.

**US-1.18.2 (M) – Tool-calling run.** As a user, I want to chat with an agent that can call tools and RAG pipelines.

1. Agent runs as a LangGraph graph inside a Temporal activity, LLM calls via the gateway (ADR-004).
2. RAG pipelines are exposed to the agent as retriever tools; their citations appear in the chat.
3. Switching the alias's target model changes the provider with no change to the agent (AC1).
4. A worker killed mid-run resumes from the last checkpoint (Phase 1 check of AC6 at agent level).

**US-1.18.3 (S) – Built-in HTTP tools.** One or two simple HTTP tools (e.g. HTTP GET with egress allowlist) for demo and tests.

### E1.19 Chat

**US-1.19.1 (M) – Chat UI.** Streaming answers over SSE, tool-call steps shown collapsible, citations clickable, stop button cancels the run.

**US-1.19.2 (M) – Feedback.** Thumbs up/down + optional comment, linked to the run and trace.

### E1.20 Tracing and accounting

**US-1.20.1 (M) – Run view.** As a B/O/V, I want each run's steps, tokens, cost and latency.

1. Every LLM call records model, tokens in/out, latency, TTFT, cost, outcome.
2. Run totals equal the sum of steps; project daily totals equal the sum of runs (reconciliation test, AC9).
3. EU sees only own runs.

**US-1.20.2 (M) – Telemetry.** OTel traces across API → worker → gateway; JSON logs with `trace_id`; Prometheus `/metrics` on each process (internal port).

### E1.21 Injection test set

**US-1.21.1 (M) – Dataset.** QA delivers a versioned prompt-injection/jailbreak dataset (≥ 300 attacks, ≥ 300 benign, English + Italian) and a runner that reports block rate and false-positive rate, ready for Phase 2 guardrails (AC8).

---

## Phase 1 exit criteria

1. All **M** stories accepted, tests green in CI.
2. Demo script: SA configures two providers → PA enables models and aliases → B writes and publishes a prompt → B uploads documents and publishes a RAG → B builds an agent with that RAG and an HTTP tool → EU chats, sees citations → V reviews the run with costs → alias switched to the second provider, same agent works.
3. Tenant isolation suite and secret redaction tests green.
4. Spikes E0.5 results documented; NFR baselines measured (gateway overhead, retrieval p95).
