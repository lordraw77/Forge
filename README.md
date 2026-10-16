# Forge

Self-hosted, LLM-agnostic platform to build and run agents, RAG pipelines and governed prompts, with multi-tenancy and observability. All models are external endpoints; Forge never hosts a model.

**Status:** Phase 0 (inception). The repository holds the specification, the plan, the architecture decisions and a running skeleton: health endpoints, worker smoke workflow and the UI shell. No product features yet.

| Document | Content |
| --- | --- |
| [docs/specs.md](docs/specs.md) | Product specification |
| [docs/planning/01-spec-analysis.md](docs/planning/01-spec-analysis.md) | Gaps, contradictions, risks, PO decisions |
| [docs/planning/02-implementation-plan.md](docs/planning/02-implementation-plan.md) | Phases, epics, acceptance-criteria mapping |
| [docs/adr/](docs/adr/README.md) | Architecture decision records |
| [docs/requirements/phase-1.md](docs/requirements/phase-1.md) | Phase 1 user stories and acceptance criteria |
| [docs/security/threat-model.md](docs/security/threat-model.md) | STRIDE threat model and security requirements |

## Repository layout

```text
backend/    Python 3.12, FastAPI, SQLAlchemy async, Alembic, Temporal workers (one image, several roles)
frontend/   Angular 21 + PrimeNG 21 (MIT), served by nginx
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

Open <http://localhost:8080>. The initial admin password is `FORGE_ADMIN_PASSWORD` in `.env`. Login screens arrive with E1.2.

`make help` lists every target.

## Configuration

Forge reads configuration from three places only (ADR-007):

| Kind | Where | Contents |
| --- | --- | --- |
| Bootstrap | `.env` ([.env.example](.env.example)) | Initial admin, `FORGE_DATABASE_URL`, `FORGE_MASTER_KEY`, `FORGE_PUBLIC_URL` |
| Infrastructure wiring | `deploy/compose/infra.env`, injected by Compose | Database role passwords, Valkey password, S3 keys; Compose derives `FORGE_REDIS_URL`, `FORGE_S3_*`, `FORGE_TEMPORAL_ADDRESS` |
| Application settings | PostgreSQL, edited from the GUI | Models, authentication, SMTP, connectors, guardrails, … (from E1.3) |

**Back up `FORGE_MASTER_KEY` separately.** It encrypts every secret stored in the database, and if it is lost those secrets cannot be recovered.

Both generated files are git-ignored and created with mode `600`.

## Services (Compose)

| Service | Image | Purpose | Health |
| --- | --- | --- | --- |
| `web` | `forge-web` | Angular UI; proxies `/api` and `/v1` to `api` | `/nginx-health` |
| `api` | `forge-backend` (`forge api`) | REST + OpenAI-compatible API | `/healthz` (liveness), `/readyz` (db + valkey) |
| `worker-runs` | `forge-backend` (`forge worker runs`) | Temporal worker: use case and agent runs | heartbeat file (`forge worker-health`) |
| `worker-ingest` | `forge-backend` (`forge worker ingest`) | Temporal worker: RAG ingestion | heartbeat file |
| `migrate` | `forge-backend` (`forge migrate`) | One-shot Alembic upgrade before api/workers start | exit code |
| `db` | `paradedb/paradedb:0.26.2-pg17` | PostgreSQL 17 + pgvector + pg_search; databases `forge`, `temporal`, `temporal_visibility` | `pg_isready` over TCP |
| `temporal` | `temporalio/auto-setup:1.29.7` | Durable execution (ADR-004) | namespace describe |
| `valkey` | `valkey/valkey:9.0.6` | Rate limits, quotas, cache, SSE fan-out | `PING` |
| `s3` | `chrislusf/seaweedfs:4.47` | S3-compatible object storage (ADR-015) | `/healthz` |
| `temporal-ui` | `temporalio/ui:2.54.1` | Optional, `--profile tools`, on `127.0.0.1:8233` | — |

Containers run as non-root with read-only root filesystems where the image allows it, `no-new-privileges` and all capabilities dropped. PostgreSQL is published only on `127.0.0.1:5432` (`FORGE_DB_PORT` to change it), and the UI on `FORGE_HTTP_PORT` (default 8080).

## Development

```bash
cd backend && uv sync                       # backend deps into backend/.venv
make lint typecheck                          # ruff, import-linter contracts, mypy --strict, eslint
make test/unit                               # backend + frontend unit tests
make test/integration                        # starts db, migrates, runs tenant-isolation tests
make test/e2e                                # Playwright (in a container) against the running stack
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

`make release` builds both images with `--pull`, then pushes them; run `docker login` first. It performs no git operations, so tagging the source is a separate manual step.

To run the published images instead of building locally:

```bash
FORGE_BACKEND_IMAGE=lordraw/forge-backend:0.1.0-dev FORGE_WEB_IMAGE=lordraw/forge-frontend:0.1.0-dev \
  docker compose -f deploy/compose/docker-compose.yml --project-directory . \
  --env-file .env --env-file deploy/compose/infra.env up -d --wait --no-build
```
