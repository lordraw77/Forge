# ADR-012 – Frontend architecture

- Status: Accepted
- Date: 2026-10-16

## Decision

- Angular 21.2 with PrimeNG 21.1 and PrimeIcons 7, the last MIT-licensed PrimeNG line (see [ADR-014](014-frontend-licensing.md)); standalone components, signals, zoneless change detection (Angular default).
- Workspace layout: `frontend/` single Angular workspace, one app (`forge-web`) and feature libraries per area: `shell`, `auth`, `projects`, `catalog`, `builder`, `prompt-studio`, `rag-studio`, `tools-studio`, `agent-studio`, `mcp-studio`, `governance`, `admin`, plus `shared/ui` and `shared/api` (generated client).
- Lazy-loaded routes per feature; route guards on permissions from `/api/v1/me`.
- State: signals + small services per feature; no global store unless a need appears.
- API client generated from OpenAPI (openapi-generator, typescript-angular) in CI; never hand-edited.
- SSE via a typed `EventSource` wrapper with reconnect and `Last-Event-ID`.
- Editors: Monaco (prompts, JSON, code); Foblex Flow 19 (MIT) for the graph canvas, validated by spike E0.5c ([report](../spikes/e05c-graph-editor.md)): unified `fConnector` API, state in immutable signals (Foblex emits outside the zone), `reset()` to re-run the render lifecycle after replacing a graph.
- CSP compatibility: production build disables critical-CSS inlining (its inline `onload` is blocked by `script-src 'self'`); nginx allows `worker-src 'self' blob:` for Foblex connection workers.
- i18n: Angular built-in i18n from day one. Languages: English (source/default) and Italian, confirmed by the PO 2026-10-16. Every user-facing string uses `i18n` / `$localize`; CI will check that the Italian translation file has no missing units.
- Testing: unit with the Angular default runner, E2E with Playwright.
- Served by nginx (unprivileged image) with strict CSP.
