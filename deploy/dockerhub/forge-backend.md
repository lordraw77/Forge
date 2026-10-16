# Forge – backend

Backend image of **Forge**, a self-hosted, LLM-agnostic platform to build and run AI agents, RAG pipelines and governed prompts, with multi-tenancy and observability. Forge never hosts models: every LLM, embedding and reranker model is an external endpoint you configure.

> **Status: pre-release (`0.1.0-dev`).** This image contains the platform skeleton: health endpoints, database migrations, Temporal workers. Product features are being added phase by phase. Do not use in production yet.

## One image, several roles

The same image runs every backend process. You choose the role with the command:

| Command | Role |
| --- | --- |
| `api` (default) | HTTP API: REST under `/api/v1`, OpenAI-compatible endpoints under `/v1`, port `8000` |
| `worker runs` | Temporal worker for agent and use-case runs |
| `worker ingest` | Temporal worker for RAG document ingestion |
| `migrate` | Applies database migrations (Alembic), then exits |
| `worker-health` | Exit code 0 if the local worker is alive (for container healthchecks) |

## Tags

| Tag | Content |
| --- | --- |
| `<version>` | Immutable release, e.g. `0.1.0-dev` |

Use it together with [`lordraw/forge-frontend`](https://hub.docker.com/r/lordraw/forge-frontend) of the **same version**.

## Requirements

- PostgreSQL 17 with the `vector` and `pg_search` extensions (e.g. `paradedb/paradedb`)
- Valkey or Redis
- Temporal server
- S3-compatible object storage (SeaweedFS, Ceph RGW, AWS S3, …)

The reference Docker Compose file wires all of them together, and it creates the database roles Forge expects (`forge_app` and `forge_admin`).

## Configuration

**Bootstrap** values come from environment variables. This is the complete list:

| Variable | Description |
| --- | --- |
| `FORGE_ADMIN_USERNAME`, `FORGE_ADMIN_EMAIL`, `FORGE_ADMIN_PASSWORD` | Initial administrator, created on first start only |
| `FORGE_DATABASE_URL` | `postgresql+asyncpg://user:password@host:5432/forge` |
| `FORGE_MASTER_KEY` | 32-byte base64 key that encrypts secrets stored in the database. **Back it up.** |
| `FORGE_PUBLIC_URL` | External URL of the installation |

**Infrastructure wiring** is usually set by Compose or Helm. These are the variables and their defaults:

| Variable | Default |
| --- | --- |
| `FORGE_REDIS_URL` | `redis://localhost:6379/0` |
| `FORGE_S3_ENDPOINT`, `FORGE_S3_ACCESS_KEY`, `FORGE_S3_SECRET_KEY`, `FORGE_S3_BUCKET` | `http://localhost:8333`, –, –, `forge` |
| `FORGE_TEMPORAL_ADDRESS`, `FORGE_TEMPORAL_NAMESPACE` | `localhost:7233`, `default` |

All other settings, such as models, authentication, SMTP and guardrails, are configured from the web UI and stored in PostgreSQL. Changing them requires no restart.

## Health

- `GET /healthz`: liveness. The process is up; dependencies are not checked.
- `GET /readyz`: readiness. Checks the database and Redis, and returns `503` if either is unavailable.

## Security

- Runs as a non-root user (UID 10001) and supports a read-only root filesystem (mount `/tmp` as tmpfs).
- Contains no secrets: all credentials are injected at runtime.
- Tenant isolation is enforced in PostgreSQL with row-level security.

## License and source

Forge is in early development, and the source repository and license will be announced with the first public release.
