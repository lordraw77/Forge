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
[Browser / API client] ──TB1──► [forge-api] ──TB2──► [PostgreSQL, Redis, MinIO, Temporal]
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

### TB3 – Gateway → external model endpoints

| STRIDE | Threat | Control | Phase |
| --- | --- | --- | --- |
| I | Sensitive data to cloud models | Per-project model enablement; PII masking guardrail before cloud models (AC7) | P2 |
| S | MITM to model endpoint | TLS verification on by default; custom CA bundle from GUI; plain HTTP allowed only with explicit per-provider flag (on-prem) | P1 |
| E | SSRF via user-configured base URL | Only SA/PA can configure providers; URL validation; optional block of link-local/metadata ranges (169.254.0.0/16 etc.) | P1 |
| I | Provider error messages leak keys | Error text scrubbed with secret regex before returning/logging | P1 |

### TB4 – Tools and identity providers

| STRIDE | Threat | Control | Phase |
| --- | --- | --- | --- |
| E | Agent misuses a tool (prompt-injected) | `requires_approval` HITL, per-project egress allowlist, tool argument guardrails, least-privilege tool credentials | P1 (allowlist) / P2 |
| E | SSRF through HTTP tools | Egress allowlist enforced in an egress proxy, DNS rebinding protection (resolve then connect to the resolved IP), block private ranges unless allowlisted | P1 |
| E | Malicious MCP server | MCP servers registered by B/PA only; stdio servers run in sandbox; tool descriptions treated as untrusted | P2/P3 |
| E | Sandbox escape (Python tools) | gVisor, no network except allowlist, CPU/mem/time limits, read-only FS | P3 |
| S | LDAP injection / OIDC token forgery | Escaped LDAP filters, LDAPS/StartTLS required; OIDC: Authlib, issuer + audience + nonce + PKCE, JWKS rotation | P2 |
| R | Approval link replay | Signed one-time tokens, short expiry, bound to approver identity | P2 |

### TB5 – Untrusted content to LLMs

| STRIDE | Threat | Control | Phase |
| --- | --- | --- | --- |
| T | Indirect prompt injection from documents/tool output | Content delimiting in prompts, injection guardrail on retrieved/tool content, tool HITL for side effects, no auto-execution of URLs | P2 |
| I | Exfiltration via rendered links/images in answers | No remote image loading in chat; URL guardrail; CSP `img-src` self + data | P1 (CSP) / P2 |
| I | Data from restricted documents | ACL filter at retrieval (AC4), chunks never cross projects | P1 |
| T | Malicious files (parser exploits, zip bombs) | MIME sniffing, size and page limits, Docling in worker with resource limits, decompression ratio checks | P1 |
| E | Jinja2 template injection in prompts | `SandboxedEnvironment`, no custom globals, render timeout | P1 |

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
| pg_search AGPL | Licensing, not security; pending PO |
