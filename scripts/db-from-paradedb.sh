#!/usr/bin/env bash
# One-time upgrade of a Compose data volume created with the ParadeDB image (Forge 0.1.0-dev)
# to the plain pgvector image (ADR-016: BM25 moves to OpenSearch, pg_search is removed).
#
# Run it while the stack still uses the old image (before `git pull`/`make docker/run` of the new
# version), or set DB_IMAGE=paradedb/paradedb:0.26.2-pg17 to start the old image for it.
# Both images are Debian trixie with PostgreSQL 17, so the data directory itself is compatible;
# only the ParadeDB extensions and preloaded libraries have to go.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
compose=(docker compose -f deploy/compose/docker-compose.yml --project-directory .
         --env-file .env --env-file deploy/compose/infra.env)
container="$("${compose[@]}" ps -q db)"
if [[ -z "$container" ]]; then
  echo "db is not running; start it with the ParadeDB image first" >&2
  exit 1
fi

# Extensions Forge does not use and the pgvector image does not ship. Kept: plpgsql, vector,
# btree_gin (Temporal visibility), pg_stat_statements (contrib).
drop="pg_search pg_cron pg_ivm postgis_tiger_geocoder postgis_topology postgis fuzzystrmatch"
dbs="$(docker exec "$container" psql -U postgres -tAc \
  "SELECT datname FROM pg_database WHERE datallowconn ORDER BY datname")"
for db in $dbs; do
  for ext in $drop; do
    docker exec "$container" psql -U postgres -d "$db" -v ON_ERROR_STOP=1 -qc \
      "DROP EXTENSION IF EXISTS $ext CASCADE" 2>/dev/null || true
  done
  echo "cleaned $db: $(docker exec "$container" psql -U postgres -d "$db" -tAc \
    "SELECT string_agg(extname, ',' ORDER BY extname) FROM pg_extension")"
done
# ParadeDB preloads pg_search and pg_cron; the new image would refuse to start with them.
docker exec "$container" psql -U postgres -qc "ALTER SYSTEM SET shared_preload_libraries = 'pg_stat_statements'"
echo "shared_preload_libraries reset; now run: make docker/run (it recreates db with the new image)"
