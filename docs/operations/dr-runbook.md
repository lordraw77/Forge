# Backup, recovery and disaster-recovery runbook

_E3.7 ([results and design](../architecture/m3.7-load-and-dr.md)). Targets from the spec: **RPO 15 minutes, RTO 1 hour** for PostgreSQL. Last drill: 2026-10-22 ([results](#drill-results))._

## 1. What holds what
| Component | Holds | Lost → | Protection |
| --- | --- | --- | --- |
| **PostgreSQL** (`forge` DB) | everything that is configuration or content: users, projects, prompts, agents, use cases, runs, audit log, RAG chunks and vectors, Temporal state (`temporal` DBs) | the platform | **WAL archiving + base backups to S3, encrypted** (this runbook) |
| Object storage (S3) | uploaded documents, parse results, use case run files | documents can no longer be re-chunked or opened (chunks in PostgreSQL stay searchable) | S3 versioning / replication of your storage; **not part of this chart or compose file** |
| OpenSearch | BM25 index | BM25 results only (vector search keeps working) | **rebuilt from PostgreSQL**: `forge search reindex` |
| Valkey | sessions, rate limits, caches | everyone is signed out | none needed |
| `.env` / Secret | **master key**, admin bootstrap | every stored secret (provider keys, tokens) becomes unreadable | keep a copy outside the cluster |
| `PITR_ENCRYPTION_KEY` | key of the database backups | backups cannot be read | keep a copy outside the cluster (`deploy/compose/infra.env`, Helm Secret key `pitr-encryption-key`) |

**Two keys decide whether a restore is possible: the master key and the backup key. Store both, apart from the backups.**

## 2. How the database is protected
- `archive_mode=on`, `archive_command = wal-g wal-push`, `archive_timeout = 60 s`: a WAL segment reaches S3 at the latest a minute after the commit (measured **55 s** in the drill) → **RPO ≈ 1–2 minutes**, far inside 15. Anything committed since the last archived segment is the most you can lose.
- A **base backup** every 24 h (`BACKUP_INTERVAL_HOURS`), the last 7 kept (`BACKUP_RETAIN_FULL`), older ones and the WAL they alone needed are deleted.
- Everything is compressed (lz4) and **encrypted** (libsodium) before it leaves the database host.
- Compose: `make pitr/up` (image `forge-db`, services `backup-init`, `db`, `backup`; [deploy/compose/pitr.yml](../../deploy/compose/pitr.yml)). Kubernetes: `postgresql.pitr.enabled=true` ([chart README](../../deploy/helm/forge/README.md)).
- Put the backup bucket on **different storage than the documents** (`FORGE_BACKUP_S3_*` / `postgresql.pitr.s3.*`). A backup on the same disk protects against mistakes, not against losing the machine.
- Watch it: the `backup` container logs `base backup done` / `FAILED`; `SELECT last_archived_time, last_failed_time FROM pg_stat_archiver` must keep moving; `wal-g backup-list` shows the backups.

## 3. Restore (Compose)
Decide the target: the **latest** state, or a **moment before a mistake** (UTC).
```sh
make pitr/restore TIME='2026-10-22 09:30:00+00' CONFIRM=yes    # empty TIME = latest
```
The script stops the stack, **keeps the current data in a volume** `forge_db-data-before-restore-<stamp>`, restores the latest base backup into `forge_db-data`, starts PostgreSQL (it replays the archived WAL to the target and promotes) and starts the rest. Then:
1. Sign in; check what you expected to be back (the audit log shows the last action before the target).
2. `docker compose … run --rm api forge search reindex` – the BM25 index may hold chunks from after the target or miss ones from before it. Vector search is already right.
3. Valkey was not restored: everyone signs in again; runs that were in progress at the target are in the state PostgreSQL had then, Temporal workflows resume from it.
4. When satisfied, remove the `…-before-restore-…` volume.

Restore into a **new machine** (the old one is gone): install the stack (or chart) with the same `.env` (master key!), the same `PITR_ENCRYPTION_KEY` and the same `FORGE_BACKUP_S3_*`, start **only the database volume empty**, and run `pitr-restore.sh` as in the script above before starting the database.

## 4. Restore (Kubernetes) – written, not exercised
1. `kubectl scale deploy/<rel>-forge-api deploy/<rel>-forge-worker-runs deploy/<rel>-forge-worker-ingest deploy/<rel>-forge-temporal --replicas=0`.
2. Scale the PostgreSQL StatefulSet to 0 and delete its PVC (take a snapshot first if the storage class has them).
3. Run a one-off Pod with the `forge-db` image, the same WAL-G environment (`kubectl get sts <rel>-forge-postgresql -o yaml` has it) and a new PVC of the same name mounted at `/var/lib/postgresql/data`, `PGDATA=/var/lib/postgresql/data/pgdata`, as user 999: `pitr-restore.sh [--time …]`.
4. Scale PostgreSQL up (it recovers), then the rest, then `forge search reindex` from an api pod.

## 5. Other losses
- **One bad migration or deletion** → point-in-time restore as above, to just before it.
- **OpenSearch lost** → start a new one, `forge search reindex`.
- **Object storage lost** → restore it from its own replication; until then documents cannot be opened or re-ingested.
- **Master key lost** → stored provider keys, tokens and webhook secrets are gone for good; users, prompts and the rest are intact. Re-enter the secrets.
- **Backup key lost** → no backup can be read: take a new base backup under a new key immediately (the old archive is useless).
- **Region/site lost** → rebuild from the off-site copy of the backup bucket plus the two keys; the RTO below applies once the database volume exists.

## 6. Drill (do it every quarter, and after every change to the backup setup)
`make pitr/drill` (script [scripts/dr-drill.sh](../../scripts/dr-drill.sh)) on a throw-away database: backup, writes, a moment T, an accident after T, the server and volume destroyed, restore to T, check, timings. It never touches the real database. For a drill of the real thing, restore into a copy of the stack on another host.

### Drill results
| | 2026-10-22, lab host (4 vCPU, 7.5 GB, shared; S3 on the same host) |
| --- | --- |
| Database | 500,000 rows with a vector column, 101 MB |
| Base backup | 3.7 s |
| Archive lag of an unforced commit | **54.6 s** (RPO target 900 s) |
| Restore to T: fetch + start + WAL replay + promote | **5.0 s** (RTO target 3,600 s) |
| Result | rows = those at T (501,000); the deletion after T and a row written after T are gone |

**What that does not prove.** The database was small and the S3 local. Recovery time grows with the size of the base backup (about 100 MB in 3 s here, 30+ MB/s) and the amount of WAL to replay: at 30 MB/s a 50 GB database needs about 30 minutes to fetch, so **1 hour holds up to roughly 80–100 GB at that speed**; measure with your storage by running the drill with `DRILL_ROWS` large enough to matter and S3 where it will really be.
