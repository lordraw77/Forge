# Changelog

All notable changes to Forge are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/). Images are published as `lordraw/forge-backend:<version>` and `lordraw/forge-frontend:<version>`.

## [Unreleased]

Phase 1 milestones M1.2 (LLM gateway), M1.3 (Prompt Studio), M1.4 (RAG on upload) and M1.5 (single agent and tracing), the closing items of Phase 0, SeaweedFS confirmed as object store (ADR-015, 2026-10-19), and two PO decisions of 2026-10-18: NG-ZORRO on Angular 22 instead of PrimeNG (ADR-017), and OpenSearch for BM25 instead of pg_search (ADR-016).

### Added

**Tools Studio (M2.2)**
- Tool Library with immutable versions, publish, duplicate and delete (blocked while an agent uses the tool); tool types **HTTP**, **read-only SQL (PostgreSQL)** and **MCP**; input schema, timeout, `requires_approval` and a "can change data" flag.
- Import from an **OpenAPI 3.x** document (JSON or YAML, local `$ref` only): pick operations, get one draft tool each. **MCP servers** (Streamable HTTP, SSE, stdio for allowed commands): discover their tools and import the ones wanted.
- **Credentials** (bearer, basic, header, query) kept encrypted per project and never shown, and a per-project **egress allowlist**. A secret that a remote API echoes back is removed from the result before the model, the run view or the log see it.
- **Test console** with a form built from the schema, and a **call log** of every test and agent call (status, timing, redacted input and output).
- Agents can use library tools (pinned to the published version when the agent version is saved). Tools that need approval are refused until human approval ships.
- New deployment settings: `FORGE_TOOLS_ALLOW_PRIVATE_HOSTS`, `FORGE_MCP_STDIO_ALLOWED`.

**External identity (M2.1)**
- Admin Console → Authentication: LDAP/Active Directory and OIDC providers (Entra ID, Keycloak, Google, any OIDC), Test connection, Test user, Sync now, and a switch to turn local login off (Super Admins can always sign in locally).
- Directory users sign in on the normal login page; OIDC adds a button per provider (Authorization Code with PKCE, state and nonce, signature, issuer and audience checks).
- Group → project Role Name mappings: several mappings per user, the highest Role Type wins, mapped memberships follow the groups at every login and at each scheduled sync, manually added members are never changed. Users gone from the directory are disabled and signed out.
- A directory or IdP entry never takes over an existing account (no merge by email). Bind password and client secret are encrypted and never returned.
- Test directory for development: `docker compose --profile idp-test` (OpenLDAP); `make test/integration` starts it.

**Agentic AI Studio (M1.5)**
- New Agent: an agent is a Library prompt (system prompt and examples are its instructions) plus a model alias, tools, RAG pipelines and limits. Every save is an immutable version; the prompt and the pipelines are pinned to versions on save. Save Configuration, Save Configuration & Test Agent, Versions, Publish, and an Agent Library with the usual status, time and name filters, Try, Edit, Duplicate and Delete.
- Tools: each RAG pipeline becomes a search tool that applies the caller's document access (groups and roles); built-in `http_get` (only hosts the agent allows, never private addresses, redirects re-checked, size and time capped) and `current_time`.
- Limits per run: model calls, tokens, cost and time. A run that exceeds one ends as failed with the reason. Switching the alias's target model changes the provider with no change to the agent.
- Runs execute as a LangGraph loop inside a Temporal activity with PostgreSQL checkpoints: a worker killed mid-run resumes from the last checkpoint, and a tool does not run twice.
- Chat: streaming answers over SSE, collapsible tool steps, clickable citations, a Stop button that cancels the run, thumbs up/down with an optional comment, conversations kept per user. Builders can chat with drafts; others with published agents.
- Runs: a run view with every step (model, tokens, cost, latency, time to first token, input and output), totals that equal the sums of the steps and of the gateway's records, citations and the failure reason; a run list with filters and 30-day totals. End users see only their own runs; Viewers and Operators see all.
- Agents travel in export/import bundles with their prompt and pipelines by name.

**Telemetry**
- OpenTelemetry spans across API, workflow, activity, gateway call and tool call; JSON logs carry `trace_id` and `span_id`; Prometheus `/metrics` on an internal port of every process (`FORGE_METRICS_PORT`, default 9100) with run, step, tool, gateway and HTTP metrics; the `observability` profile scrapes them.
- Optional Langfuse profile (`make obs/langfuse`), off by default; set `FORGE_OTLP_ENDPOINT` to send spans to the collector.

**Prompt-injection test set (E1.21)**
- `forge injection run`: block rate and false-positive rate of a detector on a versioned dataset of 440 attacks and 440 benign texts in English and Italian (plus a hand-written held-out set), by language and category, with CI gates. Detectors: `none`, a `heuristic` baseline, and any command. The baseline blocks 94 % of the generated set but only 2.5 % of the held-out attacks.

**Export / Import**
- Projects Studio → Export / Import: prompts, RAG pipelines and collection definitions, with all versions, the published one or the latest, tags included; optionally model aliases, limits and group names. Dependencies (prompts and collections used by a pipeline) can be added automatically.
- Import shows a preview first. When a name exists: add the versions, import under a new name, or skip. Bundles from older Forge versions are upgraded automatically; bundles from newer versions are refused ([ADR-018](docs/adr/018-bundle-format.md)). No secrets, files or documents are exported.

**Dark theme**
- Light, dark or system theme from the profile menu, remembered per browser; accessible contrast in both (orange adjusted for dark backgrounds).

**Corporate networks**
- `FORGE_HTTP_PROXY`, `FORGE_HTTPS_PROXY`, `FORGE_NO_PROXY` for calls to model providers, S3 and model downloads; `FORGE_EXTRA_CA_FILE` adds a corporate root CA. Tested with a forwarding proxy.

**Exact token counts**
- A tokenizer per model (`hf:<repo>` or `tiktoken:<encoding>`) used for chunking, the context budget and quotas; without one, counts are estimated and the trace says so.
- Model files (Docling models, tokenizers) are baked into the images, or downloaded at start with the `-slim` images (`MODELS=download`); both are published.

**Groups and roles for documents**
- Projects Studio → Groups: named sets of members. A file lists the groups and roles allowed to read it; empty means the whole project. Deleting a group leaves its restricted files unreadable until their access is changed.
- Library time filters offer presets and a calendar range.

**Data Collections (Projects Studio → Data)**
- Collections of files per project; upload of files or whole folders (PDF, DOCX, PPTX, XLSX, HTML, Markdown, text, PNG/JPG with OCR), up to 100 MB per request. The type is detected from the content; other files are rejected one by one with the reason.
- Status per file and per collection (Success, In progress, Failed) with the failure time and reason, Retry, download, delete, rename; deleting a collection removes its files, chunks and embeddings.
- Access per file by group and role; a member only receives chunks of files they are allowed to read; files without groups or roles are visible to the whole project.

**Ingestion**
- Automatic parsing, chunking and embedding on a Temporal worker: Docling for PDF, Office files and images (layout, tables as Markdown, OCR), built-in parsers for text, Markdown and HTML; structure-aware chunks of 512 tokens with 64 of overlap, prefixed with the title and heading path; embedding in batches of 32 through the gateway.
- Re-uploading an identical file changes nothing; an edited file re-embeds only the chunks that changed. A worker stopped mid-way resumes without duplicate chunks.
- One index per collection, embedding model and chunking setting, shared by the pipelines that use it; changing these settings builds a new index in the background while queries keep using the previous one.
- New worker image `forge-backend-ingest` with Docling, CPU PyTorch and the parsing models included (works without internet access).

**RAG Studio**
- New RAG with every parameter of the spec: data source, LLM, embedding model, search method (vector, semantic, hybrid), reranker, optional prompt from the Prompt Library, chunk size and overlap, node parser, response mode (compact, refine), top_k, similarity threshold, candidates and context budget.
- Save Configuration & Test RAG: the answer cites its sources with document name, page and a link that opens the file at that page; if nothing relevant is found, the answer says so. The trace shows the retrieval ranking with scores, timings, the prompt, cost and usage.
- Hybrid retrieval: PostgreSQL vector search and OpenSearch BM25 in parallel, fused with RRF, then reranked.
- Versions, Publish and the RAG Library (status, time range and name filters; Try, Edit, Duplicate, Delete).
- A prompt used by a RAG pipeline cannot be deleted; the error names the pipeline.

**Prompt Studio**
- New Prompt editor: inline name, model (alias or model), System and User Prompt, variables (Name, Value, Is File Input, Is Required) created inline by typing `{name}`, few-shot examples, temperature and max tokens. The description is asked on first save; template errors show their line.
- Every save is an immutable version (`draft`, `staging` and `prod` tags); older versions are viewable and can be loaded into the editor.
- Test page: Proceed is enabled when the required variables are filled; file inputs take a text file. The result shows output, latency, cost and total usage, with a call trace (input messages, output, metadata).
- Compare models: the same version and input on 2 to 4 models in parallel; a failing model does not hide the others.
- Help Me Write: System and User Prompt from a description on the project's `default-chat` alias, with the CO-STAR, RISEN, RTF and CRISPE frameworks or Free Form (the model picks and names the framework).
- Prompt Library: Published and Draft prompts with status, time range and name filters; Try, Edit, Duplicate and Delete.
- REST API under `/api/v1/projects/{id}/prompts` (versions, tags, publish, test, playground, runs, parse, assist).

**Model registry**
- Admin Console → Model providers: providers of every F1 type (vLLM, llama.cpp, NVIDIA NIM, Ollama, OpenAI, Azure OpenAI, Anthropic, Bedrock, Gemini, Vertex AI, Mistral, generic OpenAI-compatible), models with all F1.2 fields (type chat, embedding, reranker, speech or guard; context window; costs per 1M tokens; capabilities; timeout, retries and rate limit), and per-project enablement.
- Test Connection before saving: a 1-token chat, a 1-string embedding (dimensions checked) or a 1-pair rerank, with latency and the provider's error text.
- Aliases (`default-chat`, `fast`, `embedding`, `rerank` and custom ones) with an ordered fallback chain. Project aliases override the platform defaults. Changes apply to the next request in every process, without restarts.
- Projects Studio → Models: available models, project-only providers and models, alias overrides, limits and cache, today's usage and cost.
- API keys are write-only and stored encrypted. A model used by an alias, or a provider with models, cannot be deleted; the error lists what blocks it.

**OpenAI-compatible API**
- `POST /v1/chat/completions` (streaming with SSE, tool calling, JSON mode), `POST /v1/embeddings` (float and base64), `GET /v1/models`, for project tokens with the new `llm.invoke` scope. Tested with the official OpenAI Python SDK.
- Retries with exponential backoff, then the next model in the chain on timeouts, 5xx, rate limits, credential, billing or retired-model errors. The answering model is reported in `x-forge-model`.
- Quotas: tokens per day and requests per minute, per project and per user, with `429` and `Retry-After`. A ceiling on `max_tokens` per call (clamped or rejected). Platform ceilings in the setting `gateway.limits`.
- Optional exact-match response cache per project (`x-forge-cache: hit`, cost 0).
- Every call is recorded with tokens, cost, latency, time to first token and each attempt.

**Operations**
- `forge providers import-env` / `make providers/import`: registers lab providers from the API keys in `.env`; `forge providers test` runs Test Connection on all platform models.
- Optional Compose profile `observability`: OpenTelemetry Collector, Prometheus, Loki and Grafana (`make obs/up`, `make obs/smoke`).
- Licence gate for backend and frontend dependencies in CI (`make licenses`), and the Italian translation check in CI.

### Changed
- **Web UI on Angular 22 and NG-ZORRO 22** (MIT) instead of Angular 21 and PrimeNG 21: every screen was ported, with the brand theme, Italian component texts and the same workflows. No commercial licence is needed now or for later Angular versions.
- **Full-text search moves to OpenSearch 3.9** (Apache-2.0), a new Compose service `search` with TLS and a least-privilege `forge` user. PostgreSQL now runs on the plain `pgvector/pgvector` image; pg_search (AGPL) is gone. `/readyz` checks the search engine too. Spike E0.5e measured hybrid retrieval at 159 ms p95 on 1M chunks, with no cross-project leaks.
- LiteLLM 1.105.1 is a runtime dependency of the backend image (only the gateway imports it).
- The project token created with a project now includes the `llm.invoke` scope; Project Admin and Builder roles gain the `llm.invoke` permission.
- Validation errors in the UI now show why a value was refused.

### Fixed
- The SeaweedFS shipped in Compose could not store objects (too few volume slots for a new bucket); it now uses 512 MB volumes and an explicit maximum.
- OpenSearch no longer creates `forge-*` indices implicitly; Forge creates its chunk index and alias at startup.

### Security
- Uploaded files are typed from their content; archives with too many entries or an extreme compression ratio are refused. Uploaded HTML, Markdown and text are never served as web pages (`text/plain`, `nosniff`, sandbox CSP).
- BM25 queries are built in one place and always carry the project, index and access-group filters; a test checks that another project gets nothing.
- Prompt templates render in a Jinja2 sandbox: no imports, includes or macros; allowlisted filters, globals and methods; capped `range`, repetition, concatenation and powers; a step, time and size budget per render. A suite of known injection and exhaustion payloads runs in CI.
- Prompt versions cannot be changed or deleted by the application's database roles.
- Provider base URLs: http(s) only; link-local, multicast and cloud metadata addresses refused; plain HTTP only with the explicit per-provider option (on-prem endpoints).
- Provider error messages are scrubbed of API keys before they are shown, logged or stored.

### Upgrade notes
- **Before** starting this version on a database volume created by 0.1.0-dev, run `scripts/db-from-paradedb.sh` with the old stack running. It removes the ParadeDB extensions and preloaded libraries that the new PostgreSQL image does not have.
- `make env` generates the OpenSearch certificates (`deploy/compose/search/certs/`), the users file and the `OPENSEARCH_ADMIN_PASSWORD` and `FORGE_SEARCH_PASSWORD` entries in `infra.env`.
- Migrations `0004`–`0008` run automatically with `make docker/run`. `0007` replaces the free-text document access groups of `0006` (never released) with managed groups; re-set the access of files uploaded with the earlier build.
- `make docker/build` also builds `forge-backend-ingest` (about 3.7 GB, models included); `worker-ingest` now uses it.
- Restart OpenSearch after updating (`docker compose … restart search`) so it reads the new `opensearch.yml`; recreate `s3` so SeaweedFS gets the new volume settings.
- Existing project tokens keep their scopes. Create a new token with `llm.invoke` to call `/v1`.
- `make env` adds `GRAFANA_ADMIN_PASSWORD` to an existing `deploy/compose/infra.env`.

## [0.1.0-dev] - 2026-10-17

First development preview: Phase 0 (inception) and Phase 1 milestone M1.1 (platform core). Not for production use.

Images: `lordraw/forge-backend:0.1.0-dev` (`sha256:10642e9b…`) and `lordraw/forge-frontend:0.1.0-dev` (`sha256:06626562…`), built from commit `5696551`.

### Added

**Authentication and accounts**
- Local sign-in with username or email. Passwords are hashed with Argon2id and must be at least 12 characters, not a common password and not reused.
- Two-factor authentication with TOTP (QR enrolment, 10 one-time recovery codes, codes cannot be replayed).
- Sessions in HTTP-only cookies: short-lived access token, rotating refresh token, reuse of a stolen refresh token revokes the whole session family. Double-submit CSRF protection on every state-changing request.
- Account lockout after repeated failures and per-IP rate limiting of sign-in attempts.
- First sign-in steps: forced password change, terms of use acceptance, TOTP enrolment when required by policy.
- Initial Super Admin created on first start from `FORGE_ADMIN_USERNAME`, `FORGE_ADMIN_EMAIL` and `FORGE_ADMIN_PASSWORD` in `.env`.
- `make admin/reset` (and `forge users ensure-superuser`) to recover a lost admin password or unlock an account from the host.

**Tenancy and projects**
- Projects isolated by PostgreSQL row-level security; the application role cannot read another project's rows even through a query bug.
- Project roles with permissions, member invitation by username or email (up to 500 per request), project API tokens (`fk_…`).
- Projects Studio: project information, members and roles, API tokens. A project selector in the top bar.

**Administration**
- Super Admin console: users (create with a temporary password, reset password, unlock, enable or disable), projects, runtime settings and audit log.
- Append-only audit log of security-relevant actions.
- Secrets stored encrypted with AES-256-GCM, with keys derived from `FORGE_MASTER_KEY`.
- Runtime settings stored in PostgreSQL and edited from the console; changes reach every service without a restart.

**User interface**
- Angular 21 and PrimeNG 21 web app in English and Italian, switchable from the profile menu.
- Forge brand: logo, favicon, home-screen icon and web manifest. Interface colours meet WCAG AA contrast. The brand kit is in `docs/brand/`.
- Graph editor preview for use cases at `/spikes/graph-editor` (Phase 0 spike, not a finished feature).

**Platform and operations**
- Docker Compose stack: API, web, workers for runs and ingestion, ParadeDB (PostgreSQL with pgvector and pg_search), Valkey, SeaweedFS (S3), Temporal. Containers run as non-root with read-only file systems.
- Health, readiness and version endpoints; errors as RFC 9457 problem responses; JSON logs with secret redaction.
- Configuration in three places only: `.env` for bootstrap values, `deploy/compose/infra.env` for infrastructure secrets, PostgreSQL for application settings.
- Makefile entry points for setup, build, run, smoke test, tests (unit, integration, Playwright E2E) and release.

**Documentation**
- Product specification analysis, implementation plan, Phase 1 user stories and STRIDE threat model.
- Architecture decisions ADR-001 to ADR-015 and the M1.1 architecture notes.
- Spike reports for Phase 0: Temporal and LangGraph durability with human approval, hybrid retrieval at one million chunks, LiteLLM provider coverage, graph editor.
- README sections on first sign-in and admin recovery.

### Security
- Content Security Policy, `X-Frame-Options: DENY`, `nosniff` and a strict referrer policy on the web app pages; static assets keep `nosniff`.
- Container images scanned with Trivy in CI.

### Fixed
- Without a session, the sign-in page no longer loops between the session check and the token refresh.
- The web container follows the API container after it is recreated instead of answering 502.
- Logos and icons are no longer cached for a year, so brand updates reach browsers.

### Upgrade notes
- Database migrations run automatically through the `migrate` service on `make docker/run`.
- After the first sign-in, remove `FORGE_ADMIN_PASSWORD` from `.env`; it is no longer used.
- Back up `FORGE_MASTER_KEY` separately from the database. Without it, stored secrets cannot be decrypted.

[Unreleased]: https://github.com/lordraw77/Forge/compare/5696551...HEAD
[0.1.0-dev]: https://github.com/lordraw77/Forge/commits/5696551
