# Phase 3 – User stories

Roles as in [phase-1.md](phase-1.md): PA = Project Admin, B = Builder, SA = Super Admin. Phase 3 has no milestones yet beyond those the PO has chosen; each section is added when its work starts.

## M3.1 SDK and command line

### E3.3 Python SDK and `forgectl`

**US-3.3.1 (M) – Assets in Git.** As a B, I want my prompts, RAG pipelines, agents, use cases and collection definitions as files in a Git repository, so that changes are reviewed and a project can be rebuilt.
1. `forgectl export DIR` writes one JSON file per item plus a manifest; exporting twice gives identical files.
2. `forgectl diff DIR` lists what differs from the project (new, changed, only in the project) and can fail a CI job (`--exit-code`).
3. `forgectl apply DIR` imports only new and changed items, as new versions; applying twice changes nothing.
4. Nothing is deleted by `apply`; tags (`prod`, `staging`) never move on an existing item.
5. `--dry-run` reports what would happen and writes nothing.

**US-3.3.2 (M) – Keys, not passwords.** As an integrator, I want to authenticate with a project API key limited to what automation needs.
1. `export`, `diff` and `apply` work with a key that has `asset.write`; configuration (`--config`) needs `project.manage`.
2. A key without the permission gets 403 and a clear message; a wrong key gets 401.
3. `forgectl whoami` shows the key's project and the permissions it really holds.

**US-3.3.3 (S) – Run and chat from a script.** As an integrator, I want to run a published use case and call the gateway from Python or the shell.
1. `forgectl run NAME -i key=value` starts the published version, waits, and prints the outputs; `--no-wait` prints the run.
2. `ForgeClient.run_use_case`, `.chat`, `.models`, `.export_bundle`, `.import_bundle` exist with the same behaviour.

**US-3.3.4 (M) – Packaging.**
1. `pip install ./sdk` gives the `forge_sdk` package and the `forgectl` command; the only dependency is `httpx`.
2. Python 3.10 or newer; tests run with the backend suite; the licence gate passes.
