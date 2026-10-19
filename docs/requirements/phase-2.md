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
