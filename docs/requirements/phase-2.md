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
