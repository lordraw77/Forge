# 12. Installing and operating Forge

[Index](README.md) · Previous: [11. Super Admin console](11-administration.md)

This chapter is for the people who **install, upgrade, protect and watch** Forge. It is a guided tour: each task says what to run, what you should see and what to do if you do not. Everything here is done in a terminal, so there are no screenshots; the web steps after the installation are in the previous chapters.

> **Who is this for?** System administrators and DevOps. You need: a Linux host with Docker (Compose v2), GNU Make and OpenSSL for the single-node installation; or a Kubernetes/OpenShift cluster and Helm for the production installation. Plan at least **8 GB of RAM** and several CPU cores for a lab host that runs everything on one machine (OpenSearch alone takes 1 GB).

## 12.1 What Forge is made of

| Piece | Role | If it stops |
| --- | --- | --- |
| **web** | The web interface (nginx); forwards `/api` and `/v1` to the API. | No UI. |
| **api** | The REST API and the OpenAI-compatible API; also runs trigger schedules. | Nothing works. |
| **worker-runs** | Runs use cases and agents (Temporal worker). | Runs stay queued. |
| **worker-ingest** | Reads, splits and embeds documents; runs source syncs. | Documents stay *In progress*. |
| **PostgreSQL + pgvector** | All configuration, content, runs, audit log, vectors. | The platform stops. **The most precious component.** |
| **OpenSearch** | Keyword (BM25) index for RAG. | Keyword results lost; rebuilt from PostgreSQL. |
| **Temporal** | Durable execution of runs and ingestions. | Runs and ingestions pause, then resume. |
| **Valkey** | Sessions, rate limits, quotas, cache. | Everybody is signed out. |
| **S3 storage** (SeaweedFS or your S3) | Uploaded documents and run files. | Documents cannot be opened or re-processed. |
| *Optional* | observability stack, sandbox runner, vault, backups (below). | Their feature only. |

Forge **never hosts a model**: every model is an external endpoint (a cloud API, or a server you run, such as vLLM or Ollama).

## 12.2 Install on one machine (Docker Compose)

For labs, demos and small teams.

1. **Get the source** and open a terminal in its folder.
2. **Generate the configuration**:

   ```bash
   make env
   ```

   It creates `.env` (the bootstrap settings and the **master key**) and `deploy/compose/infra.env` (generated passwords of the infrastructure). Both are private (mode `600`) and are never committed. Existing files are kept.
3. **Build and start**:

   ```bash
   make docker/build      # builds forge-backend and forge-web (the first time takes several minutes)
   make docker/run        # starts everything and waits until all services are healthy
   make docker/smoke      # optional end-to-end check
   ```

   If `make docker/run` stops with *unhealthy*, run `make ps`, then `make logs SERVICE=<name>`.
4. **Open** `http://localhost:8080` (port `FORGE_HTTP_PORT`).
5. **First sign-in.** With an empty database Forge creates one Super Admin from `FORGE_ADMIN_USERNAME`, `FORGE_ADMIN_EMAIL` and `FORGE_ADMIN_PASSWORD` in `.env`. Sign in, choose a new password (12+ characters), accept the terms (chapter 1) — then **delete `FORGE_ADMIN_PASSWORD` from `.env`**: it is only read to create the first account.
6. **Back up `FORGE_MASTER_KEY` separately.** It encrypts every secret in the database. If it is lost, stored provider keys and tokens cannot be recovered.

### Add models

Open **Super Admin console → Model providers** (chapter 11), add your provider and models, enable them for a project and point the aliases `default-chat`, `fast`, `embedding`, `guard`, `rerank` at them. For a lab, `make providers/import` registers every provider whose key is present in `.env` (`OLLAMA_HOST`, `GROQ_API_KEY`, `MISTRAL_API_KEY`, …).

### Day-to-day commands

| Command | What it does |
| --- | --- |
| `make ps` | Status of the services. |
| `make logs SERVICE=api` | Follow the logs of one service. |
| `make stop` | Stop the containers, keep the data. |
| `make docker/down` | Remove containers **and volumes** — destroys local data. |
| `make migrate` | Apply database migrations. |
| `make admin/reset` | Reset or create a Super Admin and unlock it (asks for a temporary password; needs shell access to the host). `make admin/reset ADMIN_USER=alice` for another user. |

### Optional components (Compose profiles)

| Command | Adds |
| --- | --- |
| `make obs/up` | Observability: OpenTelemetry collector, Prometheus, Loki, Grafana (Grafana on `127.0.0.1:3000`). |
| `make sandbox/up` | The **sandbox runner** for Python tools and `code` nodes (chapter 5). It holds the container-engine socket, so run it only on hosts where that is acceptable. |
| `make pitr/up` | Continuous database backups (section 12.6). |
| `make vault/up` | A lab **OpenBao** vault; then `scripts/vault-setup.sh` and configure it in **Super Admin → Secrets vault** (chapter 11). |
| Langfuse profile | An optional LLM-observability tool fed by OpenTelemetry; off by default. |

## 12.3 Configuration you will need

Forge reads configuration from three places only:

| Where | Contents |
| --- | --- |
| `.env` | Bootstrap: first admin, `FORGE_MASTER_KEY`, `FORGE_PUBLIC_URL`, proxies. |
| `deploy/compose/infra.env` | Infrastructure passwords and keys, generated by `make env`. |
| The database, edited in the web interface | Models, authentication, e-mail, guardrails, limits, vault, retention… (chapter 11). |

Settings that administrators ask about most:

| Need | Setting |
| --- | --- |
| **Corporate proxy** | `FORGE_HTTP_PROXY`, `FORGE_HTTPS_PROXY`, `FORGE_NO_PROXY` in `.env` (Forge's own services are always excluded). If the proxy inspects TLS, put the corporate root certificate in `deploy/compose/extra-ca/` and set `FORGE_EXTRA_CA_FILE`. LDAP does not use the HTTP proxy. |
| **Reverse proxy in front of Forge** | `FORGE_TRUSTED_PROXIES` (so visitor addresses of widgets and rate limits are read from the right headers) and `FORGE_PUBLIC_URL`. |
| **E-mail for approvals** | In the console (**Email**, chapter 11), or the `FORGE_SMTP_*` variables. |
| **MCP over stdio** | Off by default; `FORGE_MCP_STDIO_ALLOWED` lists the allowed commands (chapter 5). |
| **Internal APIs on private addresses** | Blocked by default for tools and sources; `FORGE_TOOLS_ALLOW_PRIVATE_HOSTS` allows them for the whole installation (chapter 5). |
| **Folder sources (SMB/NFS mounted on the server)** | `FORGE_SOURCE_ROOTS`, one entry per mounted path with the projects allowed to read it (chapter 4). |
| **Air-gapped / offline** | The default images carry their models. `MODELS=download` builds smaller images that download them on first start. |

## 12.4 Install on Kubernetes or OpenShift (Helm)

The chart `deploy/helm/forge` is the same stack for a cluster.

```bash
helm install forge deploy/helm/forge -n forge --create-namespace \
  --set ingress.enabled=true --set ingress.host=forge.example.com
helm -n forge test forge
```

With no values the chart installs **everything inside the release** (one replica each) and **generates all secrets**. The notes printed at the end tell you how to read the administrator password and the **master key: back the master key up**. The secrets and volumes are kept on `helm uninstall` on purpose.

**Use your own services** in production: set `postgresql.enabled=false` and `external.postgresql.*` (PostgreSQL 17 with the `vector` extension and the roles of `files/init-databases.sh`), and likewise `external.valkey.*`, `external.s3.*`, `external.opensearch.*` and `external.temporal.*`. High availability of these services is yours: inside the chart each is a single pod.

| Topic | How |
| --- | --- |
| Existing secret | `existingSecret: name` (keys listed in the chart README). |
| OpenShift | `openshift=true`, `route.enabled=true`, and external PostgreSQL, OpenSearch and Temporal (their images need fixed user ids). **Not yet tried on a real OpenShift cluster.** |
| Proxy and private CA | `proxy.*` and `extraCA`. |
| Metrics | Optional `PodMonitor` for the API and the workers (port 9100). |
| Sandbox | Not deployed by the chart: run a runner elsewhere and set `sandbox.url` and `sandbox.token`. |

> **Honest status.** The chart is linted and validated, but an installation on a real cluster has not been verified yet. Try it in a test namespace first.

### Air-gapped installation

```bash
scripts/airgap-bundle.sh ./airgap                         # on a connected machine: images, chart, checksums
scripts/airgap-load.sh registry.local:5000 ./airgap       # inside the network: verify, load, push
helm install forge ./airgap/forge-*.tgz --set global.imageRegistry=registry.local:5000
```

## 12.5 Upgrading

1. **Take a backup** (section 12.6) and read the release notes (`CHANGELOG.md`).
2. **Compose:** get the new source, then `make docker/build` and `make docker/run`. The `migrate` service applies the database migrations before the API starts.
3. **Helm:** `helm upgrade`. The API pods migrate the database in an init container (a lock makes parallel starts safe) and the workers wait for the API.
4. Check that the **API tag** (top right of every page) shows the new version.

**Rolling back** the software does not roll the database back: to undo a bad migration, restore from a point-in-time backup (12.6). Always deploy the **same version** of the backend and frontend images.

## 12.6 Backup and recovery

**What to protect, in order of importance:**

| Item | Why | Protection |
| --- | --- | --- |
| **PostgreSQL** | It holds everything that matters. | Continuous WAL archiving plus daily encrypted base backups (below). |
| **`FORGE_MASTER_KEY`** | Without it, stored secrets are unreadable. | A copy outside the cluster. |
| **`PITR_ENCRYPTION_KEY`** | Without it, backups cannot be read. | A copy outside the cluster. |
| **S3 storage** | The uploaded documents. | Your storage's versioning or replication (not part of Forge). |
| OpenSearch | Keyword index only. | Rebuilt: `forge search reindex`. |
| Valkey | Sessions and caches. | None needed. |

**Two keys decide whether a restore is possible: the master key and the backup key. Store both, apart from the backups.**

### Turn backups on

- **Compose:** `make pitr/up`. PostgreSQL archives every transaction log segment within a minute (**RPO of about one to two minutes**, target 15) and takes a base backup every 24 hours, keeping the last 7, all **compressed and encrypted** before leaving the host.
- **Kubernetes:** `postgresql.pitr.enabled=true`.
- Put the backup bucket on **storage different from your documents** (`FORGE_BACKUP_S3_*` or `postgresql.pitr.s3.*`): a backup on the same disk protects against mistakes, not against losing the machine.
- **Watch it:** the backup container logs `base backup done` or `FAILED`; `SELECT last_archived_time, last_failed_time FROM pg_stat_archiver` must keep moving; `wal-g backup-list` lists the backups.

### Restore (Compose)

Decide whether you want the **latest** state or a **moment before a mistake** (UTC):

```bash
make pitr/restore TIME='2026-10-22 09:30:00+00' CONFIRM=yes     # empty TIME = latest
```

The script stops the stack, **keeps the current data in a safety volume**, restores the latest base backup, replays the log to the chosen moment and restarts everything. Afterwards:

1. Sign in and check what you expected to be back (the audit log shows the last action before the target).
2. Rebuild the keyword index: `docker compose … run --rm api forge search reindex`.
3. Everybody signs in again (Valkey is not restored); runs in progress resume from the state they had.
4. When satisfied, remove the safety volume.

A restore onto a **new machine** needs the same `.env` (master key!), the same backup key and the same backup bucket settings. The Kubernetes procedure is written in `docs/operations/dr-runbook.md` but has **not been exercised**. The runbook also lists other losses (OpenSearch, storage, keys) and the measured drill: lag 55 s, restore of 100 MB in 5 s.

> **Do a restore drill** (`make pitr/drill`) before you need one. An untested backup is a hope, not a backup.

## 12.7 Monitoring

- **Health:** `/healthz` (is it alive) and `/readyz` (are database, Valkey and search reachable) on the API; `make ps` for services.
- **Metrics:** each process exposes Prometheus metrics on an internal port (9100 by default, not routed through the web server). `make obs/up` starts a ready-made Prometheus and Grafana.
- **Traces and logs:** OpenTelemetry traces (every run has a *Trace id* shown in the run view, chapter 6) and JSON logs with the trace id, run id and project id. Secrets and tokens are redacted in logs.
- **Product signals:** **Analytics** and **Model Insights** (chapters 9 and 11) show errors, fallbacks, latency and cost; the **audit log** shows who did what.

What to watch: API readiness; the Temporal workers' heartbeat; backup logs; disk space of PostgreSQL, OpenSearch and S3; the error codes in Analytics; the share of fallbacks.

## 12.8 Capacity and performance

Measured on a shared 4-vCPU lab host (your results will vary): the gateway adds about **30 ms at the 95th percentile** to a model call; one API process sustained about 50 calls per second; retrieval over 800 chunks took 27 ms at the 95th percentile; 200 concurrent agent runs all completed in 51 seconds; ingestion reached about 100 pages per second for 10-page documents but about 10 pages per second for one-page documents (a fixed cost per document). One million chunks have **not** been measured. Scale by running more API replicas and more workers, and by giving PostgreSQL and OpenSearch enough memory. `scripts/loadtest-env.sh` reruns the load tests against your environment.

## 12.9 Security checklist

- Keep **few Super Admins**, with two-factor authentication required (chapter 11, *Settings*).
- Use **HTTPS** in front of Forge (a reverse proxy or the cluster's ingress) and set `FORGE_PUBLIC_URL` and `FORGE_TRUSTED_PROXIES`.
- Prefer **LDAP or OpenID Connect** and switch off local login for non-admins.
- Keep the **master key** and **backup key** outside the cluster, in a secret manager; consider the **vault** for the master key (chapter 11).
- Restrict **outgoing hosts** per project (chapter 5) and leave private-address access off unless needed.
- Review the **audit log** and the project's **API tokens** regularly; revoke what is unused.
- Update the images regularly: `make release` builds and publishes them, and a licence gate (`make licenses`) checks the dependencies.

## 12.10 Troubleshooting

| Symptom | Check |
| --- | --- |
| `make docker/run` stops with a service unhealthy | `make ps`, then `make logs SERVICE=<name>`. |
| `migrate` fails with `role "forge_app" does not exist` | The database volume is older than the init script: recreate it with `make docker/down` (**deletes local data**). |
| `temporal` keeps restarting | `make logs SERVICE=temporal`. Its databases are created only when the volume is first initialised. |
| The UI says *API offline* | `curl localhost:8080/api/v1/version`; check the `api` health. |
| Admin cannot sign in, or is locked | `make admin/reset`. |
| `search` unhealthy | `make logs SERVICE=search`. Certificate errors: run `make env` again as the same user. After changing passwords, the users file is read only at first start: recreate the `search-data` volume. |
| Documents stay *In progress* | `worker-ingest` is down, or the embedding model is unreachable (read the error in the file row, chapter 4). |
| Runs stay *Queued* | `worker-runs` or Temporal is down: `make ps`. |
| Python tools fail | The sandbox runner is not running (`make sandbox/up`) or not configured. |
| Everyone was signed out | Valkey restarted; this is normal. |
| A stored secret cannot be read | The master key changed or the vault is unreachable (chapter 11). |

---

That is the end of the guide. Chapters can be read in any order; the **Index** lists them all. If a screen no longer matches the pictures after an update, the pictures can be regenerated in both languages with `make docs/screenshots` (see the [guide README](../README.md)).
