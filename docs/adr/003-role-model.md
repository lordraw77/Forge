# ADR-003 – Two-level role model

- Status: Accepted (PO, 2026-10-16)
- Date: 2026-10-16

## Context

The spec defines three role sets (F8.3; *Workspace roles*; Part 2 *Role hierarchy*). One model is needed.

## Decision

**Platform level**

- `super_admin`: manages users, projects, Role Types, Admin Console, Model Insights. One organization exists in MVP (`organization` table kept for future multi-org); Super Admin acts as Org Admin.

**Project level**

- **Role Types** (permission sets, editable by Super Admin; defaults below): `project_admin`, `builder`, `operator`, `end_user`, `viewer`.
- **Role Names**: labels created by the Project Admin, each bound to exactly one Role Type, with catalog/category access (Access Control Management) and status active/inactive.
- A user has one Role Name per project. New members default to the Role Name bound to `end_user` ("User" in Part 2).

**Default permission matrix** (from the spec's workspace table)

| Permission | project_admin | builder | operator | end_user | viewer |
| --- | :-: | :-: | :-: | :-: | :-: |
| `project.manage` (members, settings, tokens, roles) | ✓ | | | | |
| `asset.write` (prompts, RAG, tools, agents) | ✓ | ✓ | | | |
| `usecase.build` (Build Your Own) | ✓ | ✓ | | | |
| `usecase.publish` | ✓ | ✓ | | | |
| `approval.decide` | ✓ | ✓ | ✓ | | |
| `usecase.run` | ✓ | ✓ | ✓ | ✓ | |
| `run.read` | ✓ | ✓ | ✓ | own | ✓ |

Permissions are strings `resource.action`; Role Types are sets of permissions, so new modules add permissions without schema changes.

**Group mapping**: LDAP/OIDC group → (project, Role Name), evaluated at login and on group sync.

**API keys** carry scopes that are a subset of permissions; the effective permission is the intersection with the creating user's role.

## Consequences

- One authorization check: `require(permission, project)`.
- "Org Admin" from F8 is not a separate role in MVP.
