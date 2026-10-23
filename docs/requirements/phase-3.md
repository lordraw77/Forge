# Phase 3 – User stories

Roles as in [phase-1.md](phase-1.md): PA = Project Admin, B = Builder, SA = Super Admin. Phase 3 has no milestones yet beyond those the PO has chosen; each section is added when its work starts.

## M3.1 SDK and command line

### E3.3 Python SDK and `forgectl`

**US-3.3.1 (M) – Assets in Git.** As a B, I want my prompts, RAG pipelines, agents, use cases and collection definitions as files in a Git repository, so that changes are reviewed and a project can be rebuilt.
1. `forgectl export DIR` writes one JSON file per item, with its whole version history, plus a manifest; exporting twice gives identical files.
2. `forgectl diff DIR` lists what differs from the project (new, changed, only in the project) and can fail a CI job (`--exit-code`).
3. `forgectl apply DIR` sends only the versions the project lacks (a new version appended in Git becomes one new version; a revert becomes a new version); applying twice changes nothing.
4. Nothing is deleted by `apply`; tags (`prod`, `staging`) never move on an existing item.
5. `--dry-run` reports what would happen and writes nothing.

**US-3.3.2 (M) – Keys, not passwords.** As an integrator, I want to authenticate with a project API key limited to what automation needs.
1. `export`, `diff` and `apply` work with a key that has `asset.write`; configuration (`--config`) needs `project.manage`.
2. A key without the permission gets 403 and a clear message; a wrong key gets 401.
3. `forgectl whoami` shows the key's project and the permissions it really holds.

**US-3.3.3 (S) – Run and chat from a script.** As an integrator, I want to run a published use case and call the gateway from Python or the shell.
1. `forgectl run NAME -i key=value` starts the published version, waits, and prints the outputs; `--no-wait` prints the run.
2. `ForgeClient.run_use_case`, `.chat`, `.models`, `.export_bundle`, `.import_bundle` exist with the same behaviour.

**US-3.3.5 (M) – Everything in the web interface too.** As a B or PA, I want to do in the browser everything `forgectl` does, so that nobody needs a terminal.
1. Projects Studio → *Sync* downloads the project as a zip of the same files `forgectl export` writes.
2. A picked folder, a zip or a single bundle file is compared with the project (new, changed with the versions to send, unchanged, differs, only in the project) in a table.
3. *Preview* shows what an apply would do and writes nothing; *Apply changes* sends only the new items and the missing versions, with the conflict policy chosen; applying again leaves nothing to send.
4. The page can create an API key with the scopes the command line needs and shows the `forgectl` commands for this project and server.
5. The command line and the page give the same answers (one implementation on the server).

**US-3.3.4 (M) – Packaging.**
1. `pip install ./sdk` gives the `forge_sdk` package and the `forgectl` command; the only dependency is `httpx`.
2. Python 3.10 or newer; tests run with the backend suite; the licence gate passes.

## M3.2 Kubernetes

### E3.1 Helm chart and air-gapped bundle

**US-3.1.1 (M) – One chart, the whole platform.** As an SA, I want to install Forge on k3s, RKE2 or OpenShift with one `helm install`, so that I do not translate the Compose file by hand.
1. `deploy/helm/forge` deploys api, worker-runs, worker-ingest, web and the migration (a Job that runs before every install and upgrade), plus PostgreSQL with pgvector, Valkey, OpenSearch, SeaweedFS and Temporal.
2. Every dependency can be switched off and pointed at an external service (`external.*`): PostgreSQL, Valkey, S3, OpenSearch, Temporal.
3. Secrets are generated at first install and kept on upgrade; an existing Secret can be named instead (`existingSecret`).
4. `helm lint` and `helm template` pass for every combination in `ci/*.yaml` (default, external services, OpenShift, ingress).

**US-3.1.2 (M) – Fits the cluster.** As an SA, I want the chart to follow cluster conventions.
1. Ingress (any class, TLS) or an OpenShift Route; the public URL comes from one value.
2. Pods run non-root with a read-only root filesystem and no capabilities; on OpenShift (`openshift: true`) no fixed user id is set, so the restricted SCC applies.
3. `storageClass`, image registry prefix, pull secrets, node selectors, tolerations and resources are values; the corporate proxy and extra CA of the Compose setup are values too.
4. Liveness and readiness probes on every component; the web waits for nothing at start because it resolves the api when a request arrives.

**US-3.1.3 (M) – Air-gapped installation.** As an SA, I want to install without internet access.
1. `scripts/airgap-bundle.sh` lists every image the chart needs (from `helm template`), saves them as one archive together with the packaged chart, and writes a checksum file.
2. `scripts/airgap-load.sh REGISTRY` loads the archive and pushes the images to a local registry; `--set global.imageRegistry=REGISTRY` makes the chart use them.
3. The default images carry their models (embedded variant), so nothing is downloaded at run time.

**US-3.1.4 (S) – Operable.**
1. `NOTES.txt` prints the URL, how to read the initial admin password and how to run the smoke check.
2. `helm test` calls `/readyz` through the web service.
3. An optional ServiceMonitor exposes the metrics of every Forge process.

### E3.2 Prompt and RAG evaluations

**US-3.2.1 (M) – Datasets.** As a B, I want datasets of cases, so that I can measure a prompt or a RAG pipeline on the same questions every time.
1. A dataset has a name, a kind (`prompt`: each case is the variables of the prompt; `rag`: each case is a question) and up to 1000 cases, each with an optional expected answer and, for RAG, expected source documents.
2. Cases are entered as a JSON array, JSON Lines or CSV (prompt: every column except `expected` is a variable; RAG: `query`, `expected`, `expected_sources` joined by `;`).
3. Invalid cases are refused with the line or row; names are unique per project.

**US-3.2.2 (M) – Runs and evaluators.** As a B, I want to run a prompt version or RAG pipeline version over a dataset and have every answer scored.
1. A run records the dataset, the target and its version (default: the latest), the evaluators, and a result per case (output, scores, pass/fail, error, tokens, cost, latency); each case also leaves its normal Prompt Studio or RAG run trace.
2. A case passes when it has no error and every evaluator that could score it passed; an evaluator with nothing to compare with (no expected answer) is skipped, not failed. A failing call is that case's error, not the run's.
3. Prompt evaluators: `exact`, `contains`, `regex`, `json_schema`, `judge` (LLM-as-judge with criteria). RAG evaluators: `recall_at_k`, `mrr` (expected documents among the retrieved ones), `faithfulness`, `relevance`, `correctness`, `judge`. Judges name a gateway model and are traced like any call.
4. Invalid evaluators, an empty dataset or an unknown target are refused before anything runs.
5. The summary has pass rate, mean score per evaluator, errors, cost, tokens, p50/p95 latency; a run can be cancelled; progress is visible while it runs.

**US-3.2.3 (M) – Compare.** As a B, I want to compare two runs, so that I see what a change broke.
1. The comparison gives the pass-rate and per-evaluator deltas and lists the cases that regressed, improved or stayed the same.

**US-3.2.4 (M) – RAG metrics.**
1. `recall_at_k` and `mrr` read the documents retrieved in rank order (stored in the run's retrieval trace), matched to the expected sources by file name.
2. `faithfulness` judges the answer against the passages the answer was built from, `relevance` against the question, `correctness` against the expected answer.

**US-3.2.5 (S) – A gate in CI.**
1. `forgectl eval DATASET TARGET --evaluators FILE` runs and waits; `--fail-under RATE` exits 1 below the pass rate; `--baseline RUN --no-regressions` exits 1 when a case that passed before fails now.
2. Everything is also in the web interface: Governance → Evaluations (datasets, run, results, compare).

## M3.3 Code execution

### E3.4 Sandbox, Python tools and the `code` node

**US-3.4.1 (M) – Python tools.** As a B, I want to write a tool as a Python function, so that I can add logic no HTTP API provides.
1. Tools Studio has a *Python* type: code defining `run(args)` that returns a JSON value, typed arguments (validated before the call like any tool), a memory limit and the usual timeout; it can be tested in the console, versioned, published and used by agents and use cases.
2. The code runs in the sandbox, never in Forge's own processes; what it printed, its result and its error are in the test console and the tool call log.
3. The code is checked on save (syntax, a function `run` with one argument) and refused with the reason; a Python tool never "writes" outside itself (no network, no files).

**US-3.4.2 (M) – A sandbox with real limits.** As an SA, I want code to be unable to harm the platform.
1. Every execution is a throw-away container: no network, read-only root filesystem, a small non-executable `/tmp`, user `nobody`, no capabilities, no privilege escalation, memory, CPU and process limits, a wall-clock timeout that kills it, output cut at 64 KB.
2. The runner service is the only component with access to the container engine; calls need a token; at most N run at once.
3. With `SANDBOX_RUNTIME=runsc` the same containers run under gVisor.
4. Standard library only; nothing is installed at run time.

**US-3.4.3 (M) – The `code` node.** As a B, I want a node that runs a small piece of Python between steps, so that I can reshape data without an agent.
1. A `code` node has inline `code`, `args` (templates over variables like a tool node) and an output variable; its return value is the output (JSON), usable by later nodes and conditions.
2. Errors, timeouts and out-of-memory fail the step with `code_error`, `code_timeout`, `code_oom`; invalid code is reported by the graph validation before the run.
3. The node appears in the canvas palette.

**US-3.4.4 (S) – Optional.** Without a sandbox service, Python tools and `code` nodes report "sandbox not configured" and nothing else changes.

## M3.4 Reliability

### E3.7 Load tests, point-in-time recovery and disaster recovery

**US-3.7.1 (M) – Point-in-time recovery.** As an SA, I want the database to be recoverable to any moment in the retention window.
1. WAL is archived continuously (at least every 60 s) and a base backup is taken on a schedule (default daily, last 7 kept), both **encrypted** before leaving the database host, to an S3 bucket that can be a different storage than the documents.
2. Compose: `make pitr/up`; Kubernetes: `postgresql.pitr.enabled`. Backups and the key are checked by logs and `pg_stat_archiver`.
3. `make pitr/restore TIME=… CONFIRM=yes` restores the Compose database to a moment (or the latest state), keeping the previous data aside.

**US-3.7.2 (M) – Disaster recovery you have rehearsed.** As an SA, I want a runbook and a drill, so that the targets are measured, not promised.
1. A runbook says what holds what, what is lost with each component, the restore steps for Compose and Kubernetes, the keys that must be stored apart, and how long it takes.
2. `make pitr/drill` runs the whole story on a throw-away database (backup, accident, destruction, restore to a moment) and prints the recovery time and the archive lag; it fails if the data is not as at that moment.
3. Targets: RPO 15 min (measured: the archive lag), RTO 1 h (measured: server gone → accepting writes, with the size and speed it was measured at).

**US-3.7.3 (M) – Load tests against the targets.** As an SA, I want to check the performance targets on my own hardware.
1. `scripts/loadtest.py` (any deployment) and `scripts/loadtest-env.sh` (a throw-away stack on the lab host) measure over HTTP: gateway overhead over a direct provider call (target < 50 ms p95), hybrid retrieval (< 300 ms p95; the corpus size is reported), 200 concurrent agent runs on one worker, ingestion pages per second per worker (≥ 50).
2. Each scenario prints its numbers and PASS/FAIL against its target; `--strict` fails the command.

**US-3.7.4 (M) – The search index follows the database.** As an SA, after restoring PostgreSQL I want to rebuild the BM25 index from it.
1. `forge search reindex [--project ID]` deletes each project's index entries and writes them again from the stored chunks; vector search is unaffected throughout.

### E3.8 External vault (OpenBao Transit)

**US-3.8.1 (M) – The key-encryption key in a vault.** As an SA, I want the key that protects stored secrets to live in OpenBao or Vault, so that a copy of the database and `.env` is not enough to read them.
1. Each secret keeps its own random data key; the vault's Transit engine wraps and unwraps those data keys (`openbao:<key>`), so the key never leaves it; rotating the Transit key in the vault needs no change in Forge (old data keys keep opening).
2. Sign-in by token or AppRole (the AppRole token is renewed by itself).
3. Existing secrets can be moved to the vault and back without being re-encrypted.
4. If the vault cannot be reached, reading a secret whose key is in it fails with `vault_unavailable` (503) and nothing is stored in clear; keys unwrapped in the last few minutes are cached, so a short outage is not an outage of Forge.

**US-3.8.2 (M) – Set up from the console.** As an SA, I want to configure it in the web interface.
1. Super Admin → *Secrets vault*: address, mount, key name, namespace, sign-in, CA certificate, *use the vault for new and re-wrapped secrets*; the settings are checked with a real wrap/unwrap before they are saved, and the credential is never shown again.
2. *Test the connection*, a count of secrets by key, and *Re-wrap now* move every secret to the key in use.
3. The vault cannot be switched off (or its key changed) while secrets depend on it; every change is in the audit log. All Forge processes (api, workers) follow a change without a restart.
4. The vault's own credential is kept under the built-in key, which therefore stays necessary (and is also the way back).

**US-3.8.3 (S) – A lab vault.** `make vault/up` and `scripts/vault-setup.sh` start an OpenBao, initialise and unseal it, enable Transit and create the key, the policy and a token; the policy Forge needs is in `deploy/vault/forge-policy.hcl`.

## M3.5 Triggers

### E3.9 Async invocation and triggers

Asynchronous runs with a completion webhook already exist (US-2.7.x, `delivery: async` and `webhook_url`); this epic adds the ways a run starts without a person.

**US-3.9.1 (M) – Schedules.** As a Builder, I want a use case to run by itself on a schedule, so that a daily report or a weekly check needs nobody.
1. A trigger is a five-field cron expression with a time zone, or a fixed interval in minutes, with fixed inputs; it runs the published version.
2. The run belongs to the user who created the trigger and shows up in the normal run history; the optional completion webhook works as for any run.
3. After downtime a schedule fires once, not once per missed slot; two API replicas never fire the same slot.
4. A schedule that fails to start five times in a row is paused, with the reason kept (*last error*); resuming it resets the counter.

**US-3.9.2 (M) – Inbound URL.** As a Builder, I want another system (CI, monitoring, a queue consumer) to start a use case with an HTTP call.
1. A webhook trigger has a secret URL `/api/v1/triggers/<token>` (no session, no CSRF); the body may add or override inputs.
2. The token is shown once, at creation or rotation; rotating invalidates the old one at once; pausing the trigger refuses calls.

**US-3.9.3 (S) – Managed in the web interface.** *Build Your Own → Use case library → Triggers*: create, edit, pause, *Run now*, new token, delete, with next and last run and the last error. Needs `usecase.build`; end users cannot see triggers.

