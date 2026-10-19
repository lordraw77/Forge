<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/svg/forge-logo-dark.svg">
    <img src="docs/brand/svg/forge-logo-light.svg" alt="Forge" width="280">
  </picture>
</p>

# Forge

Self-hosted, LLM-agnostic platform to build and run agents, RAG pipelines and governed prompts, with multi-tenancy and observability. All models are external endpoints; Forge never hosts a model.

**Status:** Phase 1, milestone M1.4 (RAG on upload) done; next is M1.5 (single agent and tracing). Phase 0 delivered the specification, the plan, the architecture decisions, the spikes, a running skeleton, the observability profile and the licence gate. M1.1 added tenant isolation, local authentication, secrets, runtime settings, the Super Admin console and the Projects Studio. M1.2 adds the model registry (providers, models, aliases with fallback chains, per-project enablement), Test Connection, the OpenAI-compatible `/v1` API with streaming, quotas and rate limits, and an optional response cache. M1.3 adds Prompt Studio: versioned prompts with variables and few-shot examples in a sandboxed template engine, a test page with call traces, side-by-side model comparison, Help Me Write and the Prompt Library. M1.4 adds Data Collections with file and folder upload, automatic ingestion (Docling parsing, chunking, embedding) on Temporal, hybrid retrieval with access groups, and RAG Studio with cited answers.

| Document | Content |
| --- | --- |
| [docs/specs.md](docs/specs.md) | Product specification |
| [docs/planning/01-spec-analysis.md](docs/planning/01-spec-analysis.md) | Gaps, contradictions, risks, PO decisions |
| [docs/planning/02-implementation-plan.md](docs/planning/02-implementation-plan.md) | Phases, epics, acceptance-criteria mapping |
| [docs/adr/](docs/adr/README.md) | Architecture decision records |
| [docs/requirements/phase-1.md](docs/requirements/phase-1.md) | Phase 1 user stories and acceptance criteria |
| [docs/security/threat-model.md](docs/security/threat-model.md) | STRIDE threat model and security requirements |
| [CHANGELOG.md](CHANGELOG.md) | Changes per released version |

## Repository layout

```text
backend/    Python 3.12, FastAPI, SQLAlchemy async, Alembic, Temporal workers (one image, several roles)
frontend/   Angular 22 + NG-ZORRO 22 (MIT), served by nginx
deploy/     compose/ (single node / lab); helm/ arrives in Phase 3
scripts/    small helpers called by the Makefile
docs/       specification, planning, ADRs, requirements, security
```

## Prerequisites

- Docker with Compose v2, GNU Make, OpenSSL
- For backend development on the host: [uv](https://docs.astral.sh/uv/) (installs Python 3.12 itself if missing)
- Node is **not** needed on the host: frontend commands run in a `node:24.22.0` container

## Quick start

```bash
make env          # create .env (bootstrap) and deploy/compose/infra.env (infra secrets); keeps existing files
make docker/build # build forge-backend and forge-web images
make docker/run   # start the stack and wait until every service is healthy
make docker/smoke # end-to-end check: UI, API proxy, readiness, a workflow on each worker queue
```

Open <http://localhost:8080> and sign in as described in [First sign-in](#first-sign-in).

`make help` lists every target.

## First sign-in

On first start, with an empty user table, the API creates a Super Admin from `FORGE_ADMIN_USERNAME`, `FORGE_ADMIN_EMAIL` and `FORGE_ADMIN_PASSWORD` in `.env`.

1. Sign in with that username and password.
2. Set a new password when asked (at least 12 characters, not a common password), then accept the terms of use.
3. Remove `FORGE_ADMIN_PASSWORD` from `.env`. It is read only to create the first admin; changing it later has no effect, and the API logs a warning while it is still set.

Create the other users from the Super Admin console (profile menu → Super Admin Console → Users). Each one gets a temporary password and goes through the same first sign-in steps.

### Lost admin password or locked account

```bash
make admin/reset                    # the admin from .env
make admin/reset ADMIN_USER=alice   # any username; creates a Super Admin if it does not exist
```

It prompts for a temporary password, clears the lockout and makes the account a Super Admin. At the next sign-in the user must set a new password and accept the terms again. The reset is recorded in the audit log as `user.ensure_superuser`. It needs shell access to the host running the stack, so it works even when nobody can sign in.

## Configuration

Forge reads configuration from three places only (ADR-007):

| Kind | Where | Contents |
| --- | --- | --- |
| Bootstrap | `.env` ([.env.example](.env.example)) | Initial admin, `FORGE_DATABASE_URL`, `FORGE_MASTER_KEY`, `FORGE_PUBLIC_URL` |
| Infrastructure wiring | `deploy/compose/infra.env` and `deploy/compose/search/` (certificates, users), injected by Compose | Database role passwords, Valkey password, S3 keys, OpenSearch passwords; Compose derives `FORGE_REDIS_URL`, `FORGE_S3_*`, `FORGE_TEMPORAL_ADDRESS`, `FORGE_SEARCH_*` |
| Application settings | PostgreSQL, edited from the GUI | Models, authentication, SMTP, connectors, guardrails, … (from E1.3) |

### Models

Models are configured in **Admin Console → Model providers**: add a provider (endpoint and API key), then its models, then point the aliases (`default-chat`, `fast`, `embedding`, `rerank`, or your own) at an ordered fallback chain. Enable each platform model for the projects that may use it. Project Admins see the enabled models in **Projects Studio → Models**, can override aliases, add project-only models and set the project's limits within the platform ceilings (setting `gateway.limits`).

Clients call the OpenAI-compatible API with a project token that has the `llm.invoke` scope:

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8080/v1", api_key="fk_…")
client.chat.completions.create(model="default-chat", messages=[{"role": "user", "content": "Hi"}])
```

For a lab, `make providers/import` registers the providers whose keys are present in `.env` (`OLLAMA_HOST`, `GROQ_API_KEY`, `MISTRAL_API_KEY`, `CEREBRAS_API_KEY`, `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`, `CLOUDFLARE_API_KEY` + `CLOUDFLARE_ACCOUNT_ID`, `GEMINI_API_KEY`). It is idempotent and keeps whatever already exists. Paid models (cost > 0) are in no alias. `docker compose … exec api forge providers test [--include-paid]` runs Test Connection on every platform model. The running platform never reads these variables; they only feed the import.

### Documents and RAG

In **Projects Studio → Data**, create a Data Collection and upload files or a whole folder (PDF, Office, HTML, Markdown, text, images with OCR; up to 100 MB per upload). Each file shows its status; failed files show the reason and can be retried. Access groups on a file limit it to members who have those groups (Projects Studio → Users).

In **RAG Studio**, choose the collection, the models (`default-chat`, `embedding` and, optionally, `rerank` aliases by default) and the retrieval settings, then **Save Configuration & Test RAG**: answers cite the documents and pages they come from. Parsing runs on the `worker-ingest` service, whose image includes Docling and its models.

### Export and import

**Projects Studio → Export / Import** moves prompts, RAG pipelines and collection definitions between projects and Forge installations as one JSON bundle, with all versions, only the published one or only the latest. Project administrators can include model aliases, limits and group names. Bundles hold no secrets, files or documents. On import, a preview shows what will be created, extended with new versions, renamed or skipped (your choice when a name exists), and bundles from older Forge versions are upgraded automatically ([ADR-018](docs/adr/018-bundle-format.md)).

### Corporate networks

Behind a proxy, set `FORGE_HTTP_PROXY`, `FORGE_HTTPS_PROXY` and, optionally, `FORGE_NO_PROXY` in `.env`; Forge's own services are always excluded. Calls to model providers, S3 and model downloads then use the proxy. If it inspects TLS, put the corporate root CA (PEM) in `deploy/compose/extra-ca/` and set `FORGE_EXTRA_CA_FILE=/etc/forge/extra-ca/<file>.pem`; it is added to the default trust store. In production, put Forge behind your reverse proxy or load balancer with TLS terminated there and `FORGE_PUBLIC_URL` set to the public HTTPS address. List that proxy's addresses in `FORGE_TRUSTED_PROXIES` (comma-separated CIDRs): only from them does Forge believe `X-Forwarded-For` and `X-Forwarded-Proto`, so clients cannot choose the address used for login rate limiting and the audit log. With none listed (the default) the connection's own address is used.

### Model files and air-gapped installs

The Docling models and the default tokenizers are baked into the images (`MODELS=embedded`, the default), which then work without internet access. For smaller images that download them on first start, build with `make docker/build docker/run MODELS=download` (`-slim` tags). Add a tokenizer to a model in the model dialog (`hf:BAAI/bge-m3` or `tiktoken:o200k_base`) for exact token counts; `forge models fetch --tokenizer <spec>` adds it to an image.

### Appearance

The profile menu switches between light, dark and system themes; the choice is remembered in the browser.

### Prompts

**Prompt Studio** (Builder or Project Admin) keeps a project's prompts:

- Write the System and User Prompt. Type `{name}` to create a variable; Jinja2 syntax (`{{ name|upper }}`, `{% if %}`, `{% for %}`) also works, inside a sandbox.
- Add few-shot examples, or let **Help Me Write** draft the prompt on the project's `default-chat` alias.
- Every save is an immutable version. **Save & Test Prompt** opens the test page: fill the variables, **Proceed**, and see the output with latency, cost, tokens and the call trace. Compare 2–4 models side by side, then **Publish** a version to the **Prompt Library**.

The same operations are available under `/api/v1/projects/{id}/prompts` (OpenAPI at `/api/docs`).

**Back up `FORGE_MASTER_KEY` separately.** It encrypts every secret stored in the database, and if it is lost those secrets cannot be recovered.

Both generated files are git-ignored and created with mode `600`.

## Services (Compose)

| Service | Image | Purpose | Health |
| --- | --- | --- | --- |
| `web` | `forge-web` | Angular UI; proxies `/api` and `/v1` to `api` | `/nginx-health` |
| `api` | `forge-backend` (`forge api`) | REST + OpenAI-compatible API | `/healthz` (liveness), `/readyz` (db, valkey, search) |
| `worker-runs` | `forge-backend` (`forge worker runs`) | Temporal worker: use case and agent runs | heartbeat file (`forge worker-health`) |
| `worker-ingest` | `forge-backend-ingest` (`forge worker ingest`) | Temporal worker: RAG ingestion (Docling, CPU PyTorch, models included) | heartbeat file |
| `migrate` | `forge-backend` (`forge migrate`) | One-shot Alembic upgrade before api/workers start | exit code |
| `db` | `pgvector/pgvector:0.8.7-pg17-trixie` | PostgreSQL 17 + pgvector; databases `forge`, `temporal`, `temporal_visibility` | `pg_isready` over TCP |
| `search` | `opensearchproject/opensearch:3.9.0` | BM25 full-text search for RAG (ADR-016); TLS and users from `make env`; Forge connects as `forge` | cluster health |
| `temporal` | `temporalio/auto-setup:1.29.7` | Durable execution (ADR-004) | namespace describe |
| `valkey` | `valkey/valkey:9.0.6` | Rate limits, quotas, cache, SSE fan-out | `PING` |
| `s3` | `chrislusf/seaweedfs:4.47` | S3-compatible object storage (ADR-015) | `/healthz` |
| `temporal-ui` | `temporalio/ui:2.54.1` | Optional, `--profile tools`, on `127.0.0.1:8233` | — |
| `otel-collector`, `prometheus`, `loki`, `grafana` | `otel/opentelemetry-collector-contrib:0.161.0`, `prom/prometheus:v3.15.0`, `grafana/loki:3.7.8`, `grafana/grafana:13.2.3` | Optional, `make obs/up` (profile `observability`, ADR-009): OTLP on `127.0.0.1:4317/4318`, Grafana on `127.0.0.1:3000` (admin password `GRAFANA_ADMIN_PASSWORD` in `infra.env`); `make obs/smoke` checks the pipeline | readiness endpoints |

Containers run as non-root with read-only root filesystems where the image allows it, `no-new-privileges` and all capabilities dropped. PostgreSQL is published only on `127.0.0.1:5432` (`FORGE_DB_PORT` to change it), and the UI on `FORGE_HTTP_PORT` (default 8080).

## Development

```bash
cd backend && uv sync                       # backend deps into backend/.venv
make lint typecheck                          # ruff, import-linter contracts, mypy --strict, eslint
make test/unit                               # backend + frontend unit tests
make test/integration                        # starts db, migrates, runs tenant-isolation tests
make test/e2e                                # Playwright (in a container) against the running stack
make licenses                                # licence gate (scripts/license-policy.json), also in CI
```

Running the API on the host against the Compose database:

```bash
source deploy/compose/infra.env
cd backend
FORGE_DATABASE_URL=postgresql+asyncpg://$FORGE_DB_USER:$FORGE_DB_PASSWORD@127.0.0.1:5432/$FORGE_DB_NAME \
FORGE_REDIS_URL=redis://:$VALKEY_PASSWORD@127.0.0.1:6379/0 uv run forge api --port 8000
```

Valkey is not published to the host by default. Uncomment a `ports` entry, or run the API inside Compose.

Frontend dev server with live reload, proxying `/api` to `localhost:8000`:

```bash
docker run --rm -it --network host -v "$PWD/frontend:/app" -w /app node:24.22.0-trixie-slim \
  npx ng serve   # http://localhost:4200
```

### Conventions enforced in CI

- **Module boundaries:** domain modules import each other only through their `service` module, and only `forge.gateway` may import LiteLLM. Enforced by import-linter (`backend/pyproject.toml`).
- **Tenant isolation:** every table with a `project_id` column must have forced RLS and a policy, or `tests/integration/test_tenant_isolation.py` fails. Use `forge.core.rls.enable_project_rls()` in the migration that creates the table.
- **Request-scoped tenant:** open DB work with `forge.core.db.project_scope(...)`, which sets `app.project_id` with `SET LOCAL` semantics.
- **Errors:** errors are returned as RFC 9457 `application/problem+json`.
- **Logs:** logs are JSON lines, and keys such as `password`, `token` and `api_key` are redacted.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `make docker/run` stops with a service unhealthy | `make ps`, then `make logs SERVICE=<name>` |
| `migrate` fails with `role "forge_app" does not exist` | The DB volume predates the init script. Recreate it with `make docker/down` (this **deletes local data**) |
| `temporal` keeps restarting | `make logs SERVICE=temporal`; the `temporal` and `temporal_visibility` databases are created by `deploy/compose/initdb/10-forge.sh` on first volume init only |
| UI shows "API offline" | `curl localhost:8080/api/v1/version`; check `api` health |
| Sign-in rejected for the admin, or account locked after failed attempts | `make admin/reset` (see [Lost admin password or locked account](#lost-admin-password-or-locked-account)) |
| `search` unhealthy or restarting | `make logs SERVICE=search`. `AccessDeniedException` on certificates: run `make env` again as the same user (the container runs as uid 1000). Security index errors after changing passwords: the users file is loaded on first start only; recreate the `search-data` volume or use `securityadmin.sh` |
| Upgrading a 0.1.0-dev database (ParadeDB image) | Run `scripts/db-from-paradedb.sh` with the old stack still running, then `make docker/run` |
| Integration tests skipped | They need `FORGE_TEST_DATABASE_URL`; use `make test/integration` |

## Release

Images are published to Docker Hub, one repository per component:

| Repository | Content |
| --- | --- |
| [`lordraw/forge-backend`](https://hub.docker.com/r/lordraw/forge-backend) | API, workers, migrations (`forge api`, `forge worker …`, `forge migrate`) |
| [`lordraw/forge-frontend`](https://hub.docker.com/r/lordraw/forge-frontend) | Web UI (nginx) |

The tag is the version, for example `lordraw/forge-backend:0.1.0-dev`. Always deploy the same version of both images.

```bash
make release                                # version from the VERSION file
make release VERSION=0.2.0                  # explicit version
make release NAMESPACE=registry.local/forge # another registry (air-gapped)
make dockerhub/overview                     # push deploy/dockerhub/*.md as the Docker Hub overviews
```

`make release` builds the three images (`forge-backend`, `forge-backend-ingest`, `forge-frontend`) with `--pull`, then pushes them; run `docker login` first. It performs no git operations, so tagging the source is a separate manual step.

To run the published images instead of building locally:

```bash
FORGE_BACKEND_IMAGE=lordraw/forge-backend:0.1.0-dev FORGE_INGEST_IMAGE=lordraw/forge-backend-ingest:0.1.0-dev \
FORGE_WEB_IMAGE=lordraw/forge-frontend:0.1.0-dev \
  docker compose -f deploy/compose/docker-compose.yml --project-directory . \
  --env-file .env --env-file deploy/compose/infra.env up -d --wait --no-build
```
