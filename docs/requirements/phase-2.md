# Phase 2 – User stories

Conventions as in [phase-1.md](phase-1.md). Stories for M2.2 onwards are added when each milestone starts.

---

## M2.1 External identity

### E2.1 LDAP/AD, OIDC and group mapping

**US-2.1.1 (M) – LDAP/AD provider.** As an SA, I want to configure an LDAP or Active Directory server from the Admin Console.

1. Fields: name, URL (`ldap://` or `ldaps://`), StartTLS flag, CA certificate (optional PEM), bind DN, bind password, user base DN, user filter (default `(|(uid={login})(sAMAccountName={login})(mail={login}))`), attributes for username, email and display name, group base DN and group filter (default: member-of lookup by user DN), timeout.
2. The bind password is stored as an encrypted secret and never returned by the API; the audit log shows the change without its value.
3. A plain `ldap://` URL without StartTLS is refused unless the provider is flagged `allow_insecure` (as `allow_http` for model providers).
4. **Test connection** binds with the service account and returns the server's answer; **Test user** takes a login and shows the DN, attributes and groups found, without creating anything.
5. The outbound proxy settings do not apply to LDAP (not an HTTP protocol); this is stated in the screen help.

**US-2.1.2 (M) – LDAP login.** As an LDAP user, I want to sign in with my directory credentials on the normal login page.

1. Login tries local users first; an unknown local login is tried against each enabled LDAP provider in order. The generic error of US-1.2.2 applies when all fail, and timing does not reveal which step failed.
2. Authentication is a search with the service account, then a bind as the found DN with the given password. Login values are escaped in the filter (LDAP injection test).
3. On first success a user with `auth_source = ldap` is created (`provider_id`, `external_id` = DN); later logins update email, name and groups.
4. A user with no mapped group gets no membership: login succeeds and the home page says no project is assigned.
5. Lockout counts failures per login across providers; local password change and TOTP enrolment are not offered to external users (TOTP is the provider's concern).
6. A disabled directory account or an unreachable server returns the generic error and logs the cause; local users are unaffected.

**US-2.1.3 (M) – OIDC provider.** As an SA, I want to configure any OIDC provider (Entra ID, Keycloak, Google).

1. Fields: name, issuer URL (discovery through `/.well-known/openid-configuration`), client id, client secret (encrypted), scopes (default `openid email profile`), username/email/name/groups claim names, button label.
2. Authorization Code flow with PKCE and `state`/`nonce`; the ID token signature, issuer, audience, expiry and nonce are verified against the provider's JWKS.
3. The redirect URI is shown on the screen for registration at the provider. HTTPS is required for the issuer unless flagged `allow_insecure`.
4. Groups come from the configured claim (list of strings); if the provider omits them (Entra "groups overage"), the user gets no mapped group and the login log says so.
5. **Test discovery** fetches the metadata and the JWKS and reports the endpoints found.
6. Outbound proxy and extra CA settings of the platform apply.

**US-2.1.4 (M) – OIDC login.** As a user, I want a "Sign in with <provider>" button on the login page.

1. The login page lists the enabled providers (name and label only, public endpoint).
2. The callback creates or updates the user as in US-2.1.2 #3 and issues the same session cookies; a user matched by `(provider, subject)`; an email that already belongs to a local or other-provider user is **not** merged (login refused with an explicit message), to prevent account takeover.
3. `must_change_password`, EULA acceptance and session policy apply as for local users.
4. Failed callbacks (bad state, expired, wrong audience) return to the login page with a generic message and are audited.

**US-2.1.5 (M) – Group → Role Name mapping.** As an SA, I want to map external groups to a project and a Role Name.

1. A mapping is (provider, external group, project, Role Name). The external group is a DN or a case-insensitive name for LDAP, the claim string for OIDC. Several mappings may match one user.
2. A user has one Role Name per project (ADR-003): when several mappings give different Role Names for the same project, the one with the highest Role Type rank wins (`project_admin` > `builder` > `operator` > `end_user` > `viewer`); a test covers the tie.
3. Mappings are evaluated at every login. Memberships created by mappings are marked `source = mapped`; those the Project Admin added by hand are `source = manual` and are never changed or removed by a sync. If a manual membership exists, the mapping does not override it.
4. When a mapped group is lost, the mapped membership is removed at the next login or sync, sessions stay valid, and the user's access to the project ends on the next request.
5. Mapping to an inactive Role Name or an archived project is refused when saved and skipped at login (logged).

**US-2.1.6 (S) – LDAP group sync.** As an SA, I want memberships to follow directory changes without waiting for a login.

1. A job runs every N minutes (default 60, 0 = off) per LDAP provider and re-evaluates every user of that provider; users deleted or disabled in the directory become `status = disabled` and their sessions are revoked.
2. A manual "Sync now" button reports counts (users seen, added, removed, disabled).

**US-2.1.7 (M) – Local login switch and break-glass.** As an SA, I want to turn local login off for non-admin users once external identity works.

1. Setting *Local login* (default on). When off, local users who are not Super Admins cannot sign in; Super Admins always can, so a broken directory never locks the platform.
2. The setting cannot be turned off while no provider is enabled.

**US-2.1.8 (M) – Audit and tests.**

1. Provider and mapping create/update/delete, every external login (success and failure), membership changes made by mappings, and sync runs write audit events with the provider name; secrets are redacted.
2. **AC2** is covered by an integration test against an OpenLDAP container: a user in a mapped group sees only the mapped projects; an unmapped user sees none.
3. OIDC is covered by an integration test against a mock provider (discovery, JWKS, token endpoint) that also exercises bad nonce, wrong audience and expired token.

---

## M2.2 Tools

Connectors (E2.3: Jira, ServiceNow, GLPI, GitHub/GitLab, Slack, Teams, Zabbix, Prometheus, SMTP/IMAP) are **low priority** (PO, 2026-10-19) and are not part of this milestone; a connector will be an OpenAPI import or an MCP server first.

### E2.2 Tools Studio

**US-2.2.1 (M) – Tool library.** As a B, I want a library of tools in my project that agents can use.

1. A tool has a name (`^[a-z][a-z0-9_]{0,47}$`, unique in the project), a description the model reads, a type (`http`, `sql`, `mcp`), a JSON input schema, a timeout (1–60 s, default 15), and a `requires_approval` flag.
2. Every save creates an immutable version; one version can be published; Library with Try, Edit, Duplicate, Delete and the usual name/status/time filters.
3. A tool used by an agent version cannot be deleted (409 with the references), as for prompts and pipelines.
4. Tools travel in export/import bundles by name, without credentials.

**US-2.2.2 (M) – Credentials.** As a PA, I want to keep credentials for tools without exposing them.

1. A credential has a name, a kind (`bearer`, `basic`, `header`, `query`) and its secret value(s), encrypted per project (ADR-006). The API never returns the value.
2. A tool references a credential by name; the value is added to the outgoing request only, never shown to the model, the run view, the call log or the audit log.
3. A credential cannot be deleted while a tool uses it.
4. Only Project Admins (`project.manage`) create credentials, data sources, MCP servers and the egress policy; Builders (`asset.write`) use them in tools.

**US-2.2.3 (M) – Egress policy.** As a PA, I want tools to reach only the hosts I allow.

1. The project has an allowlist of hosts (`example.com`, `*.example.com`). An empty list allows nothing. HTTP tools, MCP over HTTP and SQL data sources are all checked against it.
2. Private, loopback, link-local and metadata addresses are refused unless the deployment sets `FORGE_TOOLS_ALLOW_PRIVATE_HOSTS` (labs and internal APIs; named in the screen help). Redirects are re-checked; the request connects to the address that was checked (no proxy), response size and time are capped.
3. A refused call returns an error result to the agent and is written to the call log with `host_not_allowed` / `address_not_allowed`.

**US-2.2.4 (M) – HTTP tool.** As a B, I want to describe one HTTP operation as a tool.

1. Method, URL template with `{path}` parameters, parameters (path, query, header, JSON body fields) with type, description, required and enum, static headers, credential, response limit. The input schema is generated from the parameters.
2. The model's arguments are validated against the schema before the call; unknown arguments are refused.
3. The result given to the model is the status and the text body clipped, inside `<untrusted-content>` delimiters; the call log keeps a larger copy.
4. `GET`, `POST`, `PUT`, `PATCH`, `DELETE` are allowed; a non-GET call is only possible when the tool is flagged `writes` (shown in the Library) so reviewers can see it.

**US-2.2.5 (M) – OpenAPI import.** As a B, I want to create HTTP tools from an OpenAPI document.

1. Upload or paste an OpenAPI 3.x document (JSON or YAML, up to 2 MB); local `$ref` are resolved; remote `$ref` are refused.
2. The screen lists the operations (method, path, summary); the user picks some and Forge creates one draft tool each, with schema, base URL (first `servers` entry, editable) and security scheme mapped to a credential kind.
3. Operations without an `operationId` get a name from method and path; name clashes get a numeric suffix.

**US-2.2.6 (M) – MCP servers.** As a PA, I want to connect MCP servers and use their tools.

1. A server has a name, transport (`http` for Streamable HTTP, `sse`, `stdio`), URL or command, optional credential (sent as header), enabled flag. `stdio` runs a command inside the worker and is available only for executables named in `FORGE_MCP_STDIO_ALLOWED` (empty by default: disabled).
2. **Discover** lists the server's tools with their descriptions and schemas; the user imports the ones wanted as library tools of type `mcp` (schema copied, so versions stay reproducible; a changed remote schema is shown as a difference on the next discover).
3. Calls open a session per call, with the tool's timeout; errors and `is_error` results become error results.

**US-2.2.7 (S) – SQL tool.** As a B, I want a read-only parameterized query as a tool.

1. A data source (PostgreSQL) has host, port, database, user, password (secret), TLS mode; host checked against the egress policy.
2. A SQL tool has one `SELECT`/`WITH` statement with `:name` parameters bound as values (never concatenated), a row limit (default 100, max 1000) and a timeout. The statement runs in a read-only transaction with `statement_timeout`; more than one statement, or any DDL/DML keyword outside strings, is refused when saved.
3. Results are returned to the model as a table (clipped) and kept in the call log.

**US-2.2.8 (M) – Test console and call log.** As a B, I want to try a tool and see its calls.

1. The test page builds a form from the input schema, runs the tool with the caller's rights, and shows the request (credentials masked), the result and the timing.
2. Every call, from the test page or from an agent, is written to the call log: tool and version, source (`test`/`agent`), run, user, status, error code, redacted input, clipped output, latency. The log page filters by tool, status, source and time.
3. Retention follows the audit rule (no automatic purge in Phase 2).

**US-2.2.9 (M) – Agents use library tools.** As a B, I want to add library tools to an agent.

1. The agent editor lists published tools; adding one pins its version when the agent version is saved; the tool name is the model-visible name (unique in the agent).
2. A `requires_approval` tool is accepted in the editor but, until M2.4 (HITL), a call returns an error result "approval is not available yet" and is logged; the editor warns about it.
3. Tool results are data: delimiters and the same truncation as built-ins. Calls are idempotent per `{thread_id}:{tool_call_id}` (ADR-004).

**US-2.2.10 (M) – Security and tests.**

1. Credentials never appear in API responses, logs, audit diffs, run steps or call logs (tests inspect each).
2. SSRF tests: private and metadata addresses, DNS pointing to private, redirect to a private host, host outside the allowlist, credentials in URL.
3. Tenant isolation: tools, credentials, data sources, MCP servers and call logs of another project are invisible (isolation suite covers the new tables).

---

## M2.3 Use cases and graph engine

M2.3 is large and is built in four steps, each ending with tests and a commit: **a** catalogs and use case model (E2.4, E2.5); **b** graph engine and supervisor (E2.6, E2.7); **c** Build Your Own (E2.8); **d** end-user catalog and runs (E2.9). Human approval nodes are validated and stored here but execute only in M2.4; until then a run reaching one stops with an explicit error.

### E2.4 Catalogs, categories and access control

**US-2.4.1 (M) – Catalogs and categories.** As a PA, I want to organise use cases in catalogs and categories.

1. A catalog has a name (unique in the project), description and status; a category belongs to one catalog (unique name inside it). Built-in catalogs Forge.Software, Forge.ITOps, Forge.BizOps are created with the project and can be disabled but not deleted.
2. A catalog or category with use cases cannot be deleted (409 with the references).
3. Only Project Admins manage catalogs (`project.manage`).

**US-2.4.2 (M) – Access control by Role Name.** As a PA, I want to decide which roles see which catalog.

1. Per Role Name, a toggle per catalog; enabling a catalog enables all its categories, which can then be unchecked one by one.
2. Users see and run only use cases in categories enabled for their Role Name; Builders and Project Admins see all use cases of the project in the studio.
3. A user with no enabled catalog sees an empty catalog page with an explanation. The API returns 404 (not 403) for a use case the caller cannot see.
4. Changes are audited and take effect on the next request.

### E2.5 Use case model, versions and lifecycle

**US-2.5.1 (M) – Use case library.** As a B, I want to create and manage use cases.

1. A use case has name (unique in the project), description, icon, tags, catalog and category (optional until published), pattern (`supervisor` or `graph`), input form, output type, defaults (model alias overrides, max cost, max duration) and a definition.
2. Input form fields: text, long text, number, select, file, data collection; each with a name, label, help text and required flag. The JSON Schema for the run API is generated from it.
3. Output type: markdown, JSON (with schema), file (DOCX, XLSX, PDF) or chat transcript.
4. Library with the usual name/status/time filters, Duplicate and Delete.

**US-2.5.2 (M) – Versions and lifecycle.** As a B, I want controlled publishing.

1. States `draft` → `testing` → `published` → `deprecated`. Every publish creates an immutable version (1, 2, …) with definition, schemas, limits, publisher and time.
2. Editing a published use case creates a new draft; the published version keeps serving new and running runs until the new one is published.
3. Publishing requires validation to pass, and pinned agents, tools and prompts to be published; the screen lists what is missing.
4. Rollback: re-publish any previous version from the version history, with a diff view between two versions.
5. A use case with runs cannot be deleted, only deprecated; deprecated use cases disappear from catalogs but runs keep their version.
6. A use case used as a reference elsewhere (agent version, bundle) is reported in a 409 on delete, as for prompts and tools.

**US-2.5.3 (M) – Bundles.** As a B, I want to export and import a use case.

1. Export is a bundle per ADR-018: the use case plus referenced agents, prompts, tools and RAG configuration, by name, without secrets or data.
2. Import applies the conflict policy of ADR-018 (new version / rename / skip) and reports unresolved references.
3. Each bundle carries the Forge schema version and is migrated on import.

### E2.6 Graph definition and engine

**US-2.6.1 (M) – Graph JSON v1 validation.** As a B, I want invalid graphs rejected when saved.

1. The JSON Schema is published at `/api/v1/schemas/use-case-graph/v1` (ADR-010); node types `start`, `agent`, `tool`, `llm`, `router`, `for_each`, `while`, `human_approval`, `end`.
2. Semantic checks: unique node ids, no unreachable nodes, no unbounded cycles, loops with `max_iterations`, every `{variable}` defined on all paths before use, referenced versions exist and are published, router sections reference defined fields.
3. Errors carry the node id and a code, so the canvas can highlight them; a draft may be saved invalid, but not tested or published.
4. `ui.positions` is ignored by validation, diffs and execution.

**US-2.6.2 (M) – Execution on Temporal.** As an operator, I want graph runs to be durable.

1. Run states `queued` → `running` → `waiting_approval` → `running` → `succeeded` | `failed` | `cancelled`; each node execution is a persisted step with input, output, status, tokens, cost, latency and trace id.
2. A worker killed mid-run: the run resumes from the last completed step without repeating completed steps or tool calls (idempotency keys as in ADR-004).
3. Nodes whose dependencies finish together run in parallel; `after_mode` `all`/`any` and dead-path elimination follow ADR-010.
4. Limits (`max_steps`, `max_cost_usd`, `timeout_s`) stop the run with a reason; project quotas and gateway limits still apply.
5. Cancel stops pending steps and marks the run `cancelled`.

**US-2.6.3 (M) – Node types.** As a B, I want the building blocks of the spec.

1. `agent` runs a pinned published agent with a task and input and stores its output in a variable; `tool` runs a library tool with arguments built from variables (tools with `requires_approval` follow M2.4); `llm` runs a pinned prompt on a model alias.
2. `router` evaluates ordered sections (`=`, `!=`, `>`, `<`, `contains`, `and`/`or`), several may match, default path when none does.
3. `for_each` iterates a list variable or static list, exposing `{item}` and `{index}`, with Loop and Done outputs; `while` repeats while its condition holds; both stop at `max_iterations` with a clear failure.
4. `human_approval` and `hitl: true` are validated and pause the run for a person (M2.4, E2.10).
5. Variables are resolved from a run-scoped map; an undefined or non-serialisable value at run time fails the step with the variable name.

### E2.7 Supervisor pattern

**US-2.7.1 (M) – Agent team.** As a B, I want a simple sequential team of agents.

1. A supervisor definition is an ordered list of agents (pinned) with a task each; the first is the Team Leader and receives the input form values.
2. It compiles to the same graph (a chain of `agent` nodes), so runs, steps, tracing and limits are the same as for graphs.
3. Each agent receives the previous outputs as context and the shared feed shows every agent's output in order.
4. Reordering, adding and removing agents creates a new draft version.

### E2.8 Build Your Own

**US-2.8.1 (M) – Requirements page.** As a B, I want to describe the use case in natural language.

1. Description up to 4000 characters, optional attached file (up to 100 MB, stored per project), pattern selection (supervisor or graph).
2. Save as Draft at any time with name and description; later saves overwrite the draft.

**US-2.8.2 (M) – Planner Agent.** As a B, I want a generated first workflow.

1. The Planner knows the published agents, tools and prompts of the project, uses ADR-010 as its structured output schema, and returns a valid definition or a list of validation errors it then repairs (bounded retries).
2. It flags missing capabilities and suggests the agents to create, with a link to the agent editor; it never creates assets by itself.
3. Chat refinement edits the current definition; each accepted change is a draft save. The Planner's cost is accounted in the usage metrics.

**US-2.8.3 (M) – Canvas and node drawer.** As a B, I want to edit the graph by hand.

1. Foblex Flow canvas with New Node, zoom, pan, auto-layout and validation errors on nodes; connector ids are the `after` references.
2. Node settings open in a drawer with the fields of US-2.6.3 and variable pickers; the supervisor pattern shows the team as tiles with drag-and-drop reordering.
3. Chat and canvas edit the same definition without overwriting each other.

**US-2.8.4 (M) – Test workflow.** As a B, I want to run a draft before publishing.

1. The test page runs the draft with the input form, one pane per agent, a shared timeline and the current node highlighted on the graph.
2. Test runs are marked as such, count in usage, and do not appear in end-user run history.
3. Publish asks name, description, catalog and category (US-2.5.2).

### E2.9 Catalog, sessions and runs for end users

**US-2.9.1 (M) – Catalog view.** As an end user, I want to find and open use cases.

1. Card grid with search, category and tag filters, recently used and favorites; only use cases allowed by US-2.4.2.
2. The detail page shows description, the generated input form and the user's run history; End Users never see agents, prompts or graphs.

**US-2.9.2 (M) – Sessions and runs.** As an end user, I want to run a use case and follow it.

1. Running opens a session; a session holds one or more runs, each pinned to the published version in force when it started.
2. Progress streams over SSE: per-agent panes, shared timeline, final output. A refreshed page picks up the running run.
3. End Users see their own runs; Operators, Builders and Admins see all runs and traces of the project.
4. Run history shows inputs, outputs, files, cost, duration, version and author, with re-run using the same inputs.

**US-2.9.3 (M) – Outputs.** As an end user, I want to download results.

1. Markdown and JSON are shown and downloadable; DOCX, XLSX and PDF are generated from the final output and stored in object storage with the run.
2. Downloads are checked against the same visibility rule as the run.

**US-2.9.4 (M) – Run API.** As an integrator, I want to start a use case from code.

1. `POST /api/v1/use-cases/{id}/runs` accepts the input form as JSON and returns a run id; modes sync (with timeout), SSE stream, or async with a webhook on completion.
2. API keys see only use cases in categories enabled for their Role Name.

**US-2.9.5 (M) – Security and tests.**

1. Tenant isolation: catalogs, use cases, versions, sessions, runs, steps and outputs of another project are invisible (isolation suite extended).
2. A published version is immutable; a run started before a new publish finishes on its original version.
3. Acceptance: a use case is built, published, run by an End User from the catalog and its outputs downloaded; a worker killed mid-run resumes from the last completed step; rollback works.

---

## M2.4 Human in the loop

M2.4 is built in one step. Scope decisions: approvals pause **graph and supervisor runs** (the `human_approval` node, `hitl: true` on a node, and `tool` nodes whose tool has `requires_approval`); a `requires_approval` tool called inside an agent's own loop stays refused with an explicit result, because pausing an agent mid-loop needs the agent runtime to checkpoint at the call (planned with the agent chat HITL, backlog).

### E2.10 Approvals

**US-2.10.1 (M) – Pause and resume.** As an operator, I want a run to wait for a person.

1. A run reaching a `human_approval` node, a node with `hitl: true` (after it produced its output) or a `tool` node whose tool has `requires_approval` (before the call) creates an **approval** and the run becomes `waiting_approval` while any approval of it is pending; it returns to `running` when the last one is decided. The wait is durable (days) and survives worker restarts.
2. The approval holds what to review: the text to approve, or the planned tool call (tool name and arguments), the node's instructions, the use case, run and version, and who asked.
3. Decisions: **approve** (as is or **edited**: the edited text becomes the node's output, or the edited arguments are used for the call), **reject with feedback**. Rejecting a `hitl` node re-runs it with the feedback added to its task, up to 3 rejections, then the run fails (`approval_rejected`); rejecting a `human_approval` node or a tool call fails the run with the feedback as the reason.
4. An approval not decided within its time limit (node `timeout_hours`, default 72, at most 720) expires and the run fails with `approval_timeout`. Cancelling the run cancels its pending approvals.
5. The paused step is a step of the run (visible in the timeline) and finishes when decided; the decision, editor and comment are kept and audited.

**US-2.10.2 (M) – Who decides.** As a PA, I want only the right people to approve.

1. Deciding needs the permission `approval.decide` (Project Admin, Builder, Operator by default). A node may restrict approvers to Role Names (`assignees`); Project Admins can always decide. A decider must not need to be the person who started the run.
2. Everyone else gets 404 for the approval. The starter of a run sees the status "waiting for approval" on their run, without the content under review.
3. Decisions through the API are checked like those in the UI (same endpoint, CSRF for sessions, API keys with the scope).

**US-2.10.3 (M) – Approvals inbox and review.** As an approver, I want to review from the UI.

1. An **Approvals** page lists the pending approvals the user may decide (and recent decided ones), with use case, node, who started the run, age and expiry.
2. The review dialog shows the instructions and the content in an editor (Monaco), and the actions **Approve**, **Approve with changes** and **Reject** (feedback required).
3. The builder's test page links to the approval when a test run is waiting.

**US-2.10.4 (M) – Notifications and links.** As an approver, I want to be told and to decide from the message.

1. When an approval is created, every member allowed to decide with an email address gets an **email** (SMTP configured by the deployment; none configured = no email, the inbox still works). The email names the use case and node and carries a link to the review page and **signed one-time links**.
2. The link opens a public review page (no sign-in) that shows the content and asks for the decision; **opening a link never decides** (mail scanners follow links). The token is random, 256 bits, stored hashed, bound to the approval and the user, expires with the approval, and is consumed by the first decision (any decision, by any means, invalidates all tokens of the approval). The decider is recorded as the user the link was sent to, who must still be allowed to decide.
3. If the run has a `webhook_url`, it also receives `use_case_run.approval_requested` with the approval id and the review URL (no content, no token).
4. Unknown, used or expired tokens answer 404 with no detail.

**US-2.10.5 (M) – Security and tests.**

1. Tenant isolation: approvals and tokens of another project are invisible; RLS is forced.
2. An approver cannot decide twice (409), cannot decide after expiry or cancellation, and edits are size-bounded like step outputs.
3. Acceptance (AC5, AC12): a run with an approval node stops, notifies and resumes after approval from the UI or API; the same from an email link; rejection with feedback re-runs a `hitl` node.

## M2.5 Guardrails

M2.5 is built in slices. **Slice a (done):** policy model, in-process checks, gateway enforcement on input and output, intervention log, policy API with dry run. **Not yet built:** escalate-to-approval, the AC8 run against a guard endpoint.

### E2.11 Guardrail policy

**US-2.11.1 (M) – Policy.** As a PA, I want one policy per project.

1. A policy is an ordered list of rules `{type, stage[], action, params, fail_mode}`; `type` is `pii`, `injection`, `secrets`, `urls`, `banned_topics`, `json_schema`, `toxicity` or `grounding`; `stage` is `input`, `output` or `tool_args`; `action` is `log`, `mask`, `block`, `rewrite` or `escalate`.
2. Only Project Admins read and write it; the policy can be switched off; changes apply within 5 s.
3. A model-backed check (`toxicity`, `grounding`) with no endpoint is reported as unavailable on the policy; a rule that changes or stops text (`mask`, `rewrite`, `block`, `escalate`) then fails closed, a `log` rule fails open, unless `fail_mode` says otherwise.
4. A dry-run endpoint evaluates text against the saved or a draft policy without logging or calling a model.

**US-2.11.2 (M) – Enforcement.** As a compliance officer, I want every model call checked.

1. Every gateway chat call (API keys, agents, use cases, Planner) checks the user and tool messages before the model and the answer after it.
2. `mask` replaces PII spans with `[ENTITY]` before the cloud model sees them (AC7); `rewrite` replaces with the rule's text; `block` answers 400 `guardrail_blocked` (input) or 502 (output); the strictest action across rules wins.
3. A `mask`/`rewrite` rule whose check has no span to cut (injection, schema) blocks instead, unless fail-open.
4. A streamed call whose policy has output rules is run whole and replayed, so an answer is never partly delivered.
5. Guard-model checks (chat shape): `toxicity`, and `injection` after the heuristic, with `params.model` naming a `guard` model of the project; calls are accounted for in `llm_call`, a guard that cannot answer follows the fail mode.
6. `params.judge: true` lets an ordinary chat model act as the guard for `toxicity` and `injection`.
7. The `tool_args` stage checks each string of a tool call's arguments before the tool runs (agents and use case tool nodes); a blocked call does not run.
8. `grounding` (output stage): a chat model judges whether the answer is supported by the call's tool results; calls without tool results are not checked.
9. A `guard` model with `params.shape: classify` is a plain HTTP classifier (`{text} → {labels}` or the TEI shape); it serves `toxicity` and `injection` rules like a chat guard model.
10. Checks without a guard model: PII (email, card with Luhn, IBAN with mod 97, Italian fiscal code with check character, phone, IP; and, with the Presidio models, `PERSON`, `LOCATION`, `ORGANIZATION` in Italian and English), secrets (cloud keys, tokens, private keys, passwords in assignments, custom patterns), URLs (allow/deny lists, shorteners, raw IPs), banned terms, JSON schema, the injection heuristics of Phase 1.

**US-2.11.5 (S) – Agent rules.** An agent version can carry its own guardrail rules, applied after the project's for its model and tool calls; they never replace or relax the project's.

**US-2.11.4 (S) – Bundles.** The policy exports and imports with the other configuration; import replaces the policy after validating it and its guard models.

**US-2.11.3 (M) – Intervention log.**

1. Each finding is logged with stage, source, rule, check, action, labels and match count. **The text is never stored.**
2. The log is project-scoped (forced RLS) and readable by Project Admins.
