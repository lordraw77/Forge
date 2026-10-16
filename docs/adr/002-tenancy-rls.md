# ADR-002 – Tenant isolation with PostgreSQL RLS

- Status: Accepted
- Date: 2026-10-16

## Context

Projects (workspaces) are logical tenants (F8.5). The spec requires row-level security on `project_id`. The backend uses async SQLAlchemy with a connection pool, so session-level settings would leak between requests.

## Decision

**Entity scopes**

| Scope | `project_id` | Examples | Protection |
| --- | --- | --- | --- |
| Platform | none | organization, user, group, role_type, audit_event (platform), settings | Application RBAC (Super Admin) |
| Dual | nullable (`NULL` = platform-wide) | provider, model, guardrail_policy, connector, catalog | RLS: `project_id IS NULL OR project_id = current project` for reads; writes on `NULL` rows only via admin role |
| Project | `NOT NULL` | everything else | RLS: `project_id = current project` |

**Session variable**

- Each request/activity opens a transaction and runs `SET LOCAL app.project_id = '<uuid>'` (and `app.user_id`) before any query. Never `SET` at session level.
- Policies use `current_setting('app.project_id', true)::uuid`: if unset, the value is `NULL` and no project row matches (deny by default).

**Database roles**

| Role | Use | RLS |
| --- | --- | --- |
| owner (`forge` in Compose, the user in `FORGE_DATABASE_URL`) | Owns schema, runs Alembic | Not subject to RLS on its own tables unless forced; never used for request handling |
| `forge_app` | API and workers | Subject to RLS (`NOBYPASSRLS`, `FORCE ROW LEVEL SECURITY` on tables). `NOLOGIN`: processes connect with the single `FORGE_DATABASE_URL` owner credentials and run `SET ROLE forge_app` on every new connection (`forge.core.db.create_engine`). |
| `forge_admin` | Cross-project jobs: purge, platform dashboards, Planner catalog index | `BYPASSRLS`, used only by named code paths, every use audited |

**Rules**

- Every new project table gets `ENABLE` + `FORCE ROW LEVEL SECURITY` and a policy in the same migration that creates it.
- A CI test enumerates all tables with a `project_id` column and fails if any lacks RLS or a policy, then runs cross-tenant read/write probes on each.
- Cross-workspace sharing (catalog publish) works by reading published versions through a `published_asset` view owned by `forge_admin`, exposing read-only columns only.

## Consequences

- Because the app connects as the owner and switches role, SQL injection could `RESET ROLE`. Accepted to keep the spec's single database URL; mitigated by bound parameters only (threat model TB2). A separate login role for the app can be added later via deployment wiring without code changes beyond `create_engine`.
- Isolation holds even if application code forgets a filter.
- Queries still add `project_id` filters explicitly so indexes are used.
- `forge_admin` is a privileged path and must stay small and reviewed.

## Alternatives

- Schema per tenant: complex migrations with many projects. Database per tenant: too heavy for the target scale.
