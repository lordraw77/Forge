# Forge Helm chart

```sh
helm install forge deploy/helm/forge -n forge --create-namespace \
  --set ingress.enabled=true --set ingress.host=forge.example.com
helm -n forge test forge
```

With no values the chart installs everything inside the release (one replica each) and generates every
secret. `NOTES.txt` prints how to read the administrator password and the **master key – back the master
key up**: without it the stored provider keys and tokens cannot be read. The Secrets are annotated
`helm.sh/resource-policy: keep`, so `helm uninstall` leaves them (and the PVCs) behind on purpose.

## Choosing services
| Service | In the release (default) | Your own |
| --- | --- | --- |
| PostgreSQL 17 + pgvector | `postgresql.enabled` | `postgresql.enabled=false`, `external.postgresql.*` |
| Valkey / Redis | `valkey.enabled` | `external.valkey.*` |
| S3 | SeaweedFS | `seaweedfs.enabled=false`, `external.s3.*` |
| OpenSearch (BM25) | `opensearch.enabled` | `external.opensearch.*` (+ `caSecret` for a private CA) |
| Temporal | `temporal.enabled` | `external.temporal.*` |

An **external PostgreSQL** must have the roles and databases of [files/init-databases.sh](files/init-databases.sh)
(run it once as superuser with `FORGE_DB_*` and `TEMPORAL_DB_PASSWORD` set; it also needs the `vector`
extension). Give the chart `external.postgresql.password`. The in-chart Temporal on an external database
also needs `external.postgresql.temporalPassword`; with `temporal.enabled=false` Temporal is yours.

An **external OpenSearch** needs a user with the permissions of [files/opensearch-roles.yml](files/opensearch-roles.yml).

## Existing secret
`existingSecret: name` replaces the generated Secret. Keys: `master-key`, `admin-password`, `database-url`
(`postgresql+asyncpg://user:pass@host:5432/forge`, password URL-encoded), `redis-url`, `s3-access-key`,
`s3-secret-key`, `search-password`; with in-chart services also `db-password`, `postgres-password`,
`temporal-db-password`, `valkey-password`, `search-admin-password`.

## OpenShift
`openshift=true` leaves the user ids to the restricted SCC and `route.enabled=true` creates a Route. Use
external PostgreSQL, OpenSearch and Temporal (`ci/openshift.yaml`): their images need fixed user ids, so
in-chart they need the `anyuid` SCC. The Forge images run under an arbitrary uid; this was **not** tried on a
real OpenShift cluster.

## Air-gapped
```sh
scripts/airgap-bundle.sh ./airgap            # online: images.txt, forge-images.tar, forge-*.tgz, SHA256SUMS
scripts/airgap-load.sh registry.local:5000 ./airgap   # inside: verify, load, push
helm install forge ./airgap/forge-*.tgz --set global.imageRegistry=registry.local:5000
```
The default images carry their models. Behind a proxy that is not air-gapped use `proxy.*` and `extraCA`.

## Backups (point-in-time recovery)
`postgresql.pitr.enabled=true` swaps the PostgreSQL image for `forge-db` (pgvector + WAL-G), archives WAL
continuously and runs a sidecar that takes a daily base backup and keeps the last 7, all **encrypted** with a key
the chart generates (`pitr-encryption-key` in the Secret – keep a copy outside the cluster) into an S3 bucket
(default: the release's SeaweedFS; use `postgresql.pitr.s3.*` for other storage – a backup on the same disk
protects against mistakes, not against losing it). The restore procedure is in
[docs/operations/dr-runbook.md](../../../docs/operations/dr-runbook.md). Rendered and validated, **not run on a cluster**.
Add `-- --set postgresql.pitr.enabled=true` to `scripts/airgap-bundle.sh` so the image is in the archive.

## Sandbox
The chart does not deploy a sandbox runner (it needs a container engine socket today). Run one where you can
(`make sandbox/up` on a Compose host, or the `forge-sandbox` image with the engine socket) and set
`sandbox.url` and `sandbox.token`; without it Python tools and `code` nodes say "sandbox not configured".

## Upgrades
`helm upgrade` replaces the pods; the api pods migrate the database in their init container (locked, so any
number may start at once) and the workers wait for the api. Rolling back the chart does not roll the database back.
OpenSearch users and certificates are read at first start only: change a search password by recreating the
OpenSearch volume or through its security API.
