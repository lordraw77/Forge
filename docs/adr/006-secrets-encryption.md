# ADR-006 – Envelope encryption for secrets

- Status: Accepted
- Date: 2026-10-16

## Context

Provider keys, LDAP bind passwords, OIDC client secrets, SMTP and connector credentials are stored in PostgreSQL, encrypted with AES-256-GCM under `FORGE_MASTER_KEY`. Rotation is not specified.

## Decision

- Table `secret(id, project_id NULL, kind, kek_id, wrapped_dek, nonce, ciphertext, aad, created_at, rotated_at)`.
- Per-secret random 256-bit DEK encrypts the value (AES-256-GCM, 96-bit nonce, AAD = `secret.id || kind || project_id`). The DEK is wrapped with the KEK.
- KEK sources (the `openbao` source is built, see [M3.8](../architecture/m3.8-vault.md)): `builtin` (derived from `FORGE_MASTER_KEY` with HKDF-SHA256, `kek_id = "builtin:v1"`) or `openbao` (Transit engine, configured from the GUI, Phase 3).
- Rotation: `forge secrets rotate-kek` re-wraps all DEKs under a new KEK; old KEK accepted for decryption until rotation completes.
- APIs never return secret values: write-only fields, responses show `"configured": true` and last 4 characters at most.
- Decrypted values live only in memory for the duration of a call; never logged (log redaction filter on known secret fields and regex).
- If `FORGE_MASTER_KEY` is lost, secrets are unrecoverable and must be re-entered; documented in the operations runbook.
- Startup warning if `FORGE_ADMIN_PASSWORD` is still set after the admin has changed their password.

## Consequences

- Master key rotation without re-entering secrets.
- External vault can be added without schema changes.
