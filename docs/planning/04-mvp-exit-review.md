# MVP exit review (M2.7)

Date: 2026-10-21. Checked against the 13 acceptance criteria of the [plan](02-implementation-plan.md#4-mvp-acceptance-criteria--epics), the security review of the Phase 2 work and the performance baseline.

## Verdict

Twelve criteria are met by automated tests; **AC8 is met only with an LLM judge** (accepted by the PO on 2026-10-21; not with the heuristic or a guard model alone) and on a small hand-written set. AC11 is verified through the API on an empty database, not as a browser test on a clean Compose stack. Details and open items below.

## Acceptance criteria

| AC | Result | Evidence |
| --- | --- | --- |
| AC1 model from UI, Test Connection, alias switch | Met | `test_gateway.py`, `test_fresh_install.py` (provider, model, test, alias), Phase 1 demo |
| AC2 LDAP user sees only mapped projects | Met | `test_identity.py::test_ldap_user_sees_only_mapped_projects` (OpenLDAP container) |
| AC3 upload queryable < 60 s with citations | Met | `test_rag.py`; 38.3 s for a 20-page PDF ([m1.4](../architecture/m1.4-rag.md)) |
| AC4 no chunks without ACL | Met | `test_rag.py` ACL and negative tests, `test_tenant_isolation.py` |
| AC5 approval stops, notifies, resumes via UI/API | Met | `test_approvals.py` (pause, edit, reject, expiry, email link, webhook); Playwright `approvals.spec.ts` exists, not run in this review |
| AC6 worker killed mid-run resumes | Met | `test_usecase_runs.py::test_killed_worker_resumes_without_repeating_finished_steps`; agents: `test_agents.py` |
| AC7 PII masked before the model, logged | Met | `test_guardrails.py::test_pii_masked_before_the_model_and_logged` |
| AC8 ≥ 90% injection set blocked | **Met with a judge, 2026-10-21** | `forge injection run --detector 'judge:openai/gpt-oss-20b@…groq…' --set holdout`: **40/40 attacks blocked (EN 20/20, IT 20/20), 0/40 false positives**. Without a judge: heuristic 2.5%, Prompt Guard 2 30% ([m2.5 §AC8](../architecture/m2.5-guardrails.md)). See open item 1 |
| AC9 steps/tokens/cost/latency per run, totals = sum | Met | `test_analytics.py` (dashboard totals equal `llm_call` rows; agent run totals reconcile with the calls of their steps), `test_agents.py` |
| AC10 429 after quota | Met | `test_gateway.py`; also applied on a running app in `test_fresh_install.py` |
| AC11 fresh install, only `.env` admin, rest via GUI, no restart | Met through the API, with gaps | `test_fresh_install.py`: empty database migrated by `forge migrate`, app started with only database, cache, master key and admin password; then password change, EULA, provider, model test, alias, project and key, a gateway limit that applies at once, an LDAP provider and a directory login, all on the same running app. Gaps: open items 2 and 3 |
| AC12 use case published, run from catalog, HITL, outputs, exact version | Met | `test_usecase_end_user.py`, `test_usecase_runs.py`, `test_approvals.py`; Playwright `usecases.spec.ts`, `catalog.spec.ts` |
| AC13 editing a published use case does not affect runs; rollback | Met | `test_usecases.py::test_versions_are_immutable_and_rollback_works`, `test_usecase_runs.py::test_published_run_pins_the_version…` |

## Test results

| Suite | Result |
| --- | --- |
| Backend unit | 240 passed, 1 skipped (spaCy models not downloaded) |
| Backend integration, full run (clean host, 2026-10-21) | **168 passed, 2 skipped** in 22 min 32 s (the skips are the performance baseline, which needs `FORGE_PERF=1`, and one guardrail test that needs the spaCy models) |
| Frontend unit | 51 passed |
| ruff, mypy (149 files), import contracts (3 kept), ng build, i18n check (1074 messages, 0 missing) | clean |
| Playwright E2E (stack rebuilt: api, web, worker-runs, migration 0018) | **21 passed** in 3.1 min, including a new spec for the M2.6 pages and the Email screen (Analytics and activity log, a source created and deleted, Model Insights, Email, Home). The ingest worker image was not rebuilt (memory). The prompts spec was made robust against a long model list (the option was out of the virtual-scroll window once the dev database held ~150 models) |

The final run follows an earlier one with 3 failures: two approvals tests (an unrelated project was running tests on the same host and the machine was swapping; both pass alone) and the gateway overhead test, which was a real regression and is fixed (below).

## Performance baseline

Measured with `FORGE_PERF=1 scripts/test-integration.sh tests/integration/test_perf_baseline.py -s` on the 4-vCPU, 7.5 GB lab host (PostgreSQL in Docker, mock provider, no network hop to the API).

| Measure | Target | p50 | p95 |
| --- | --- | --- | --- |
| `/v1/chat/completions`, full pipeline (auth, quota, guardrails, cache, `llm_call` write) | | 25.5 ms | 37.8 ms |
| Mock provider called directly | | 1.6 ms | 2.5 ms |
| **Gateway overhead** | **< 50 ms p95** | 23.8 ms | **36.2 ms** |
| Project dashboard, 7 days, 200,000 calls | none set | 293 ms | 315 ms |
| Project dashboard, 30 days, 200,000 calls | none set | 1,083 ms | 1,157 ms |
| Model Insights, 30 days, 200,000 calls | none set | 964 ms | 972 ms |
| Activity log search | none set | 27 ms | 43 ms |

- The gateway overhead test (`test_gateway_overhead_p95`) failed at 53–61 ms p95 before this review. Cause: every call opened four database transactions (key lookup, `last_used_at` update, project access, `llm_call` write). The `last_used_at` update now runs at most once a minute per key and process: p95 overhead 26–33 ms in three runs. The remaining three transactions are the next candidates (a short-lived cache of key and access would trade revocation delay for latency, so it was not done without a decision).
- The Phase 1 figures (4 ms overhead, retrieval 159 ms p95 at 1M chunks) came from spikes; retrieval at 1M chunks in the shipped schema and load tests against the NFR targets are in E3.7 ([results](../architecture/m3.7-load-and-dr.md#2-load-test-results-2026-10-22); retrieval at 1M chunks was not re-measured).
- The host was shared and swapping during part of the work; numbers are for comparing builds, not for sizing.

## Security review

Dependency audit (2026-10-21): `pip-audit` found `certifi` (fixed by upgrade) and 8 Pillow CVEs (TIFF, GIF, PPM, EPS decoders) that need Pillow 13; **docling-slim pins `pillow<13`**, so they stay until Docling moves (PO, 2026-10-26: accepted as is; upgrade Pillow when Docling allows it). Exposure: uploads accept only PNG and JPG (content sniffing), so those decoders are not reachable from a file upload. `npm audit` (production): 0. License check: 143 backend and 60 frontend packages pass.

Code review of the uncommitted M2.6 work (dashboards, scheduled sources): one finding.

| Finding | Severity | Status |
| --- | --- | --- |
| `filesystem` sources could read any directory under `FORGE_SOURCE_ROOTS` for any project, so a Builder could index files meant for another project | Medium | **Fixed**: each root is bound to projects (`path=<project id>,…` or `=*`); an entry without a project list is ignored; checked at save and at sync; unit test |

Noted, not fixed (low): `git clone` resolves the host itself and follows its first redirect, so an allowlisted host could redirect to an internal address (the answer is read only as a Git repository); the Git token travels as an HTTP header on the command line, visible to other processes in the worker container.

## Open items

1. **AC8 — PO decision 2026-10-21: the LLM judge is accepted as the way to meet it** (a project that needs AC8 puts a `judge` injection rule in its policy). Met only when an `injection` rule uses a judge (a chat model per guarded text: cost and latency) — 80 hand-written cases, one judge model, one run. The generated `main` set (880 cases) was not run with the judge, and the 40 attacks were written by the team: a hostile test set would be a stronger proof. Still worth doing later: a hostile test set written by someone else, and the `main` set run with the judge.
2. ~~SMTP has no GUI~~ **Closed 2026-10-21**: Super Admin console → Email (host, port, user, sender, STARTTLS, password in the secret store, test message); `FORGE_SMTP_*` still works when nothing is set there. `tests/integration/test_notifications.py`.
3. **Deployment flags stay in the environment** by design: `FORGE_TOOLS_ALLOW_PRIVATE_HOSTS`, `FORGE_AGENT_ALLOW_PRIVATE_HOSTS`, `FORGE_MCP_STDIO_ALLOWED`, `FORGE_SOURCE_ROOTS`, and the proxy settings.
4. **AC11 as a browser test on a clean Compose stack** is not done: the host has 3 GB of free memory and the ingest image is 3.7 GB to rebuild. The script-level equivalent above covers the configuration path; run `make` on a clean host for the final proof.
5. **Playwright E2E** passes (21/21) on the rebuilt stack. `worker-ingest` still runs the previous image, so a source sync was not exercised in the browser stack (the integration suite covers it).
6. **Pillow CVEs** wait on Docling (see above).
7. Choices awaiting PO review in the milestone designs: [m2.1](../architecture/m2.1-external-identity.md), [m2.2](../architecture/m2.2-tools.md), [m2.3a–d](../architecture/m2.3d-end-user.md), [m2.4](../architecture/m2.4-hitl.md), [m2.5](../architecture/m2.5-guardrails.md), [m2.6a](../architecture/m2.6a-analytics.md), [m2.6b](../architecture/m2.6b-sources.md).
8. Phase 2 work from M2.3d on is uncommitted in the working tree (M2.6 and M2.7); CI has not seen it.

## Next

Phase 3 (Enterprise): Helm chart and air-gapped bundle, evaluations, Python SDK and CLI, sandbox, MCP server and chat widget, long-term memory, load tests and DR, external vault. Before that: the PO decisions in open items 1 and 2, a clean-host install run, and the E2E suite on a rebuilt stack.
