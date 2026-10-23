# Forge – Threat model v1

_Owner: Security Engineer. Method: STRIDE per trust boundary. Scope: Phase 1–2 architecture ([ADRs](../adr/README.md)). Date: 2026-10-16. Review: at every phase gate and on any ADR change._

## 1. Assets

| Asset | Why it matters |
| --- | --- |
| Project data: documents, chunks, prompts, runs, outputs | Confidential business data, multi-tenant |
| Credentials: provider keys, LDAP/OIDC/SMTP secrets, API keys, sessions | Lead to data exfiltration and cost abuse |
| `FORGE_MASTER_KEY` | Decrypts every secret |
| Audit log | Accountability, compliance |
| LLM budget / quotas | Direct financial cost |
| Tool capabilities (HTTP, MCP, SQL, later code) | Actions on external systems on behalf of users |

## 2. Trust boundaries

```text
[Browser / API client] ──TB1──► [forge-api] ──TB2──► [PostgreSQL, Redis, S3 (SeaweedFS), Temporal, OpenSearch]
                                     │                         ▲
                                     ▼                         │
                              [forge-workers] ─────────────────┘
                                     │
               ┌───────TB3───────────┼──────────TB4───────────┐
               ▼                     ▼                        ▼
      [External LLM/embedding/  [External tools: HTTP,   [Identity: LDAP, OIDC]
       reranker/guard endpoints]  MCP servers, SQL DBs]
                                     ▲
                           TB5: untrusted content
             (uploaded documents, retrieved chunks, tool outputs, model outputs)
```

TB5 is not a network boundary: any text from documents, tools or models is untrusted input to the LLM and to the UI.

## 3. Threats and controls

Status: **P1** = control delivered in Phase 1, **P2** / **P3** later phases.

### TB1 – Clients → API

| STRIDE | Threat | Control | Phase |
| --- | --- | --- | --- |
| S | Credential stuffing / brute force | Argon2id, lockout, per-IP rate limit, TOTP, generic errors | P1 |
| S | Stolen session cookie | HttpOnly + Secure + SameSite cookie, short JWT + rotating refresh with reuse detection, 30 min idle timeout, logout revokes | P1 |
| S | Leaked API key | Only hash stored (SHA-256 of 256-bit random key, prefix shown), scopes, expiry, per-project binding, last-used tracking, rotation | P1 |
| T | CSRF | Double-submit CSRF token on unsafe methods for cookie auth; API keys not accepted from cookies | P1 |
| T | Mass assignment | Separate Pydantic input/output schemas, `extra="forbid"` | P1 |
| R | User denies an admin action | Append-only audit log, actor + IP + diff | P1 |
| I | IDOR across projects | RLS (ADR-002) + `404` for foreign IDs + isolation suite | P1 |
| I | XSS via model output / document content rendered in UI | Markdown rendered with sanitizer (no raw HTML), strict CSP (`script-src 'self'`, no inline scripts: critical-CSS inlining disabled; `worker-src 'self' blob:` only for same-origin workers), Angular auto-escaping; links `rel=noopener`; E2E fails on any CSP violation in the console | P1 |
| D | Request flooding, large uploads | Rate limits per IP/key, 100 MB body cap at proxy and app, request timeouts | P1 |
| D | Cost exhaustion via LLM calls | Quotas tokens/day, requests/minute, `max_tokens` cap, per-run cost budget | P1 |
| E | Privilege escalation via role edits | Only PA edits roles; PA cannot grant `super_admin`; API key scopes ∩ user permissions | P1 |

### TB2 – Services → data stores

| STRIDE | Threat | Control | Phase |
| --- | --- | --- | --- |
| I | Cross-tenant reads from code bugs | RLS with `SET LOCAL`, `forge_app` without BYPASSRLS, `FORCE RLS` | P1 |
| I | SQL injection | SQLAlchemy bound parameters only; raw SQL lint rule; SQL tool uses read-only role + parameterized templates | P1/P2 |
| I | Secret disclosure from DB dump or backup | Envelope encryption (ADR-006), encrypted backups, volume encryption | P1/P3 |
| T | Audit log tampering | No UPDATE/DELETE grants for `forge_app`; periodic hash chain checkpoint | P1 (grants) / P2 (hash chain) |
| I | Object storage access across projects | Server-side access only, per-project key prefix, presigned URLs ≤ 5 min after authorization check | P1 |
| S | Infra credentials default | Compose/Helm generate random credentials; no default passwords in images | P1 |
| I | Cross-tenant read through the search engine (no RLS in OpenSearch) | One query builder in `forge.rag` always adds project, KB and ACL filters plus project routing; no caller-supplied query DSL; CI isolation test (spike E0.5e and `test_rag.py`: 0 leaks) | P1 ✅ M1.4 |
| S/I | Access to the search engine from the network | Security plugin on, TLS (deployment CA), user `forge` limited to `forge-*` indices without cluster admin; port bound to localhost in Compose (ADR-016) | P1 ✅ |

### TB3 – Gateway → external model endpoints

| STRIDE | Threat | Control | Phase |
| --- | --- | --- | --- |
| I | Sensitive data to cloud models | Per-project model enablement; PII masking guardrail before cloud models (AC7) | P2 |
| S | MITM to model endpoint | TLS verification on by default ✅; plain HTTP only with the explicit per-provider flag `allow_http` (on-prem) ✅ M1.2; custom CA bundle from GUI (open, system trust store until then) | P1 |
| E | SSRF via user-configured base URL | Only SA/PA can configure providers; http(s) only; link-local, multicast, unspecified and cloud metadata hosts refused ✅ M1.2; egress allowlist (Security settings) | P1 / P2 |
| I | Provider error messages leak keys | Error text scrubbed of the provider key and key-shaped strings before returning, logging or storing ✅ M1.2 | P1 |

| D | Cost exhaustion through the gateway | Tokens/day and requests/minute per project and per user, per-model rpm, max_tokens ceiling, enforced before the provider call ✅ M1.2 (AC10) | P1 |

### TB4 – Tools and identity providers

| STRIDE | Threat | Control | Phase |
| --- | --- | --- | --- |
| E | Agent misuses a tool (prompt-injected) | `requires_approval` HITL, per-project egress allowlist, tool argument guardrails, least-privilege tool credentials | P1 (allowlist) / P2 |
| S | Directory or IdP entry takes over an existing account; empty-password LDAP bind; LDAP filter injection; OIDC replay and mix-up; stale access after a group change | See [m2.1-external-identity.md §5](../architecture/m2.1-external-identity.md): no merge by email, empty password refused, RFC 4515 escaping, state/nonce/PKCE and token checks, mappings re-evaluated at every login and sync | P2 ✅ M2.1 |
| E | SSRF, credential leaks, SQL writes and command execution through library tools (HTTP, OpenAPI, MCP, SQL) | See [m2.2-tools.md §5](../architecture/m2.2-tools.md): per-project egress allowlist, secret scrubbing of results, read-only SQL, MCP stdio off by default, call log | P2 ✅ M2.2 |
| E | SSRF through HTTP tools | Per-agent host allowlist (empty = nothing); every address a host resolves to must be public (private, loopback, link-local and metadata ranges refused, also IPv4-mapped IPv6); the request connects to the address that was checked (no second lookup), redirects re-checked, 3 redirects, 10 s, 256 KB, text content types only; `FORGE_AGENT_ALLOW_PRIVATE_HOSTS` is a deployment flag for labs, off by default. With a corporate proxy the proxy resolves the name (`forge.core.egress`, unit and integration tests) | P1 ✅ M1.5 |
| E | Agent calls a tool twice after a crash | Idempotency key `{thread_id}:{tool_call_id}` in `tool_call_log`; a repeat returns the stored result (test with a killed worker) | P1 ✅ M1.5 |
| D | Runaway agent loop or cost | Per-run limits (model calls, tokens, cost, time) with platform ceilings; the run ends `failed` with the reason; gateway quotas still apply to every call | P1 ✅ M1.5 |
| I | Reading another user's run, answer or stream | RLS on every run table; `run.read.own` filtered in the query; the SSE endpoint checks the run first; stream keys hold the run id only | P1 ✅ M1.5 |
| E | Malicious MCP server | MCP servers registered by B/PA only; stdio servers run in sandbox; tool descriptions treated as untrusted | P2/P3 |
| E | Sandbox escape (Python tools, `code` nodes) | one throw-away container per run: no network, read-only FS, no capabilities, non-root, memory/CPU/pids/time limits, only the runner holds the engine socket and calls need a token; gVisor via `SANDBOX_RUNTIME=runsc` (untested here); **no egress allowlist** ([M3.4](../architecture/m3.4-sandbox.md)) | P3 ✅ M3.4 (runc), gVisor pending |
| S | LDAP injection / OIDC token forgery | Escaped LDAP filters, LDAPS/StartTLS required; OIDC: Authlib, issuer + audience + nonce + PKCE, JWKS rotation | P2 |
| R | Approval link replay | Signed one-time tokens, short expiry, bound to approver identity | P2 |

### TB5 – Untrusted content to LLMs

| STRIDE | Threat | Control | Phase |
| --- | --- | --- | --- |
| T | Indirect prompt injection from documents/tool output | Content delimiting in prompts, injection guardrail on retrieved/tool content, tool HITL for side effects, no auto-execution of URLs. Phase 1: passages and web content are delimited and declared data in the tool result and the agent's rules; the injection dataset (E1.21, `forge injection run`) gives the baseline the Phase 2 guardrails must beat | P1 (delimiting, dataset) / P2 |
| I | Exfiltration via rendered links/images in answers | No remote image loading in chat; URL guardrail; CSP `img-src` self + data | P1 (CSP) / P2 |
| I | Data from restricted documents | ACL filter at retrieval (AC4), chunks never cross projects | P1 |
| T | Malicious files (parser exploits, zip bombs) | Content sniffing with per-file rejection, 100 MB request limit, ZIP entry and ratio checks, Docling only in the ingestion worker (read-only filesystem, no runtime downloads); uploaded HTML/text never served as HTML ([M1.4](../architecture/m1.4-rag.md#9-threat-model-delta)). Page limits for huge PDFs: E2.13 | P1 ✅ M1.4 (page limits P2) |
| S | Client chooses its address (login rate limit, audit) by sending `X-Forwarded-For` | nginx believes `X-Forwarded-For`/`-Proto` only from `FORGE_TRUSTED_PROXIES`; by default the connection address is used; checked by `scripts/test-proxy-headers.sh` | P1 ✅ |
| T | Egress through a TLS-inspecting corporate proxy weakens certificate checks | Verification stays on; the corporate root CA is added to the trust store (`FORGE_EXTRA_CA_FILE`), never disabled | P1 ✅ |
| I | Secrets or documents leave in an exported bundle | Bundles hold no secrets, files, documents, run history or members; references by name ([ADR-018](../adr/018-bundle-format.md)) | P1 ✅ |
| E | Import changes more than the importer may | Configuration kinds need `project.manage`; dry run by default; every import audited | P1 ✅ |
| E | Jinja2 template injection in prompts | `ImmutableSandboxedEnvironment` without loader; allowlisted filters, globals, methods and statements; capped operators and `range`; step/time/size budget per render; payload suite in CI ([M1.3 design](../architecture/m1.3-prompt-studio.md#3-templates-and-the-sandbox)) | P1 ✅ M1.3 |

## 4. Security requirements for every epic

1. Input validation with Pydantic at the API edge; reject unknown fields.
2. Authorization via `require(permission, project)` on every endpoint; tests for `403`/`404` per role.
3. No secret in logs, traces or error messages (redaction test).
4. New tables: RLS in the same migration (CI-enforced).
5. Dependencies: pinned + hash-locked (`uv.lock`, `package-lock.json`); `pip-audit` / `npm audit` / Trivy image scan in CI, fail on high/critical with no fix-waiver.
6. Containers: non-root, read-only root FS where possible, no capabilities, healthchecks.
7. Security headers: HSTS, CSP, X-Content-Type-Options, Referrer-Policy, frame-ancestors none (widget origin allowlist in P3).
8. TLS 1.3 at the edge (reverse proxy); internal mTLS optional (P3).

## 5. Residual risks

| Risk | Note |
| --- | --- |
| Prompt injection cannot be fully prevented | Mitigated by HITL on side-effecting tools and least-privilege credentials; AC8 targets ≥ 90% on the known set only |
| `forge_admin` BYPASSRLS path | Kept to named jobs, audited, code-owner review required |
| Loss of `FORGE_MASTER_KEY` | Secrets unrecoverable; operator must back it up separately (runbook) |
