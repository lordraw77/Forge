# ADR-007 – Runtime configuration in the database

- Status: Accepted (PO approved infra wiring outside `.env`, 2026-10-16)
- Date: 2026-10-16

## Context

All settings are edited in the GUI and stored in PostgreSQL; `.env` holds only the admin and boot values. Bundled infrastructure (Redis, MinIO, Temporal) must be reachable before any DB setting can be read by every process.

## Decision

**Three kinds of configuration**

| Kind | Where | Examples | Changed by |
| --- | --- | --- | --- |
| Bootstrap | `.env` | `FORGE_ADMIN_*`, `FORGE_DATABASE_URL`, `FORGE_MASTER_KEY`, `FORGE_PUBLIC_URL` | Operator, restart |
| Infrastructure wiring | Env vars set by Compose / Helm with defaults | `FORGE_REDIS_URL=redis://redis:6379/0`, `FORGE_S3_ENDPOINT=http://minio:9000` (+ credentials from a deployment secret), `FORGE_TEMPORAL_ADDRESS=temporal:7233` | Deployment, restart |
| Application settings | PostgreSQL `setting` table, GUI | Models, auth, SMTP, connectors, guardrails, observability endpoints, quotas | Admin, live |

Infrastructure wiring is not application config: users never edit it, defaults work with the shipped Compose/Helm files.

**Application settings**

- `setting(key, scope, project_id NULL, value jsonb, version, updated_by, updated_at)` + `setting_history` (append-only). Secrets inside settings are references to `secret` rows (ADR-006).
- Every write: validated with a Pydantic model per key, versioned, audited, then `NOTIFY forge_config, '<key>:<version>'` in the same transaction.
- Each process keeps an in-memory cache, loaded at start, and listens with a dedicated connection; on notify it reloads the key. On reconnect it reloads everything (missed notifications).
- `FORGE_PUBLIC_URL` is the default; the GUI value overrides it once set.
- Wherever an external system is involved, the settings API exposes `POST .../test`.

## Consequences

- AC11 holds: a fresh install has only `.env` + deployment defaults; everything else is GUI-driven, without restarts.
- The spec's "`.env` (complete list)" wording must be updated to mention infrastructure wiring (Docs task).
