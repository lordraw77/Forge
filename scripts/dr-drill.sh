#!/usr/bin/env bash
# Disaster-recovery drill for PostgreSQL point-in-time recovery (E3.7): on a throw-away database
# (never the real one) it takes a base backup, writes more data, notes a moment T, then simulates
# an accidental deletion after T, destroys the server and its volume, and restores to T from the
# archive in S3. Prints the measured recovery time and the archive lag (the RPO in practice).
#
#   scripts/dr-drill.sh                  uses the Compose stack's S3 (SeaweedFS) on network forge_default
#   DRILL_ROWS=2000000 scripts/dr-drill.sh
#
# Needs: docker, the stack's s3 service running, deploy/compose/infra.env, image forge-db:dev
# (`docker build -t forge-db:dev deploy/db`).
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$root/deploy/compose/infra.env"
rows="${DRILL_ROWS:-500000}"
image="${FORGE_DB_IMAGE:-forge-db:dev}"
network="${DRILL_NETWORK:-forge_default}"
run="$(date +%s)"
bucket="forge-backups-drill"
key="$(openssl rand -hex 32)"  # WAL-G libsodium key: backups are encrypted
name="forge-drill-pg-$run"
vol1="forge-drill-data-$run-a"
vol2="forge-drill-data-$run-b"

walg_env=(
  -e "WALG_S3_PREFIX=s3://$bucket/pg-$run"
  -e "AWS_ENDPOINT=http://s3:8333"
  -e "AWS_S3_FORCE_PATH_STYLE=true"
  -e "AWS_REGION=us-east-1"
  -e "AWS_ACCESS_KEY_ID=$S3_ACCESS_KEY"
  -e "AWS_SECRET_ACCESS_KEY=$S3_SECRET_KEY"
  -e "WALG_LIBSODIUM_KEY=$key"
  -e "WALG_LIBSODIUM_KEY_TRANSFORM=hex"
  -e "PGHOST=/var/run/postgresql"
  -e "PGUSER=postgres"
)
pg_flags=(-c wal_level=replica -c archive_mode=on -c "archive_command=wal-g wal-push %p" -c archive_timeout=60 -c max_wal_size=1GB)

say() { printf '\n== %s\n' "$*"; }
psql_() { docker exec -i "$name" psql -U postgres -d drill -v ON_ERROR_STOP=1 -qtA "$@"; }
now() { date +%s.%N; }
elapsed() { awk -v a="$1" -v b="$(now)" 'BEGIN { printf "%.1f", b - a }'; }
cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker volume rm "$vol1" "$vol2" >/dev/null 2>&1 || true
}
trap cleanup EXIT

start_db() {  # volume
  docker run -d --name "$name" --network "$network" "${walg_env[@]}" \
    -e POSTGRES_PASSWORD=drill -e POSTGRES_DB=drill --shm-size=256m \
    -v "$1:/var/lib/postgresql/data" "$image" postgres "${pg_flags[@]}" >/dev/null
  until docker exec "$name" pg_isready -h 127.0.0.1 -U postgres -d drill -q 2>/dev/null; do sleep 1; done
}

say "bucket $bucket"
(cd "$root/backend" && set -a && source ../.env && set +a \
  && FORGE_S3_ENDPOINT="http://127.0.0.1:${FORGE_S3_PORT:-8333}" FORGE_S3_ACCESS_KEY="$S3_ACCESS_KEY" \
     FORGE_S3_SECRET_KEY="$S3_SECRET_KEY" uv run forge storage ensure-bucket "$bucket")

say "1. database with archiving on, $rows rows"
docker volume create "$vol1" >/dev/null
start_db "$vol1"
psql_ <<SQL
CREATE EXTENSION vector;
CREATE TABLE items (id bigserial PRIMARY KEY, note text NOT NULL, emb vector(8));
INSERT INTO items (note, emb)
  SELECT md5(g::text) || repeat('x', 60), ARRAY(SELECT random() FROM generate_series(1, 8))::vector
  FROM generate_series(1, $rows) g;
SQL
before=$(psql_ -c "SELECT count(*) FROM items")
echo "rows: $before, size: $(psql_ -c "SELECT pg_size_pretty(pg_database_size('drill'))")"

say "2. base backup"
t0=$(now)
docker exec -u postgres "$name" wal-g backup-push /var/lib/postgresql/data 2>&1 | tail -2
echo "base backup took $(elapsed "$t0") s"

say "3. more writes, then the moment T we will recover to"
psql_ -c "INSERT INTO items (note) SELECT 'kept-' || g FROM generate_series(1, 1000) g"
sleep 2
target=$(psql_ -c "SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US') || '+00'")
echo "T = $target"
expected=$(psql_ -c "SELECT count(*) FROM items")
sleep 2

say "4. the accident: a table is emptied after T"
psql_ -c "DELETE FROM items WHERE note LIKE 'kept-%'" -c "INSERT INTO items (note) VALUES ('written-after-T')"
echo "rows now: $(psql_ -c "SELECT count(*) FROM items")  (should be restored to $expected)"

say "5. archive lag: a commit that no WAL switch pushed, and how long until it is archived"
psql_ -c "INSERT INTO items (note) VALUES ('rpo-probe')" >/dev/null
probe=$(now)
until [[ "$(psql_ -c "SELECT (pg_stat_archiver.last_archived_time > now() - interval '$(( $(date +%s) - ${probe%.*} + 1 )) seconds')::int FROM pg_stat_archiver")" == 1 ]]; do
  sleep 2
  [[ "$(elapsed "$probe" | cut -d. -f1)" -gt 180 ]] && { echo "NOT archived within 180 s" >&2; exit 1; }
done
lag=$(elapsed "$probe")
echo "archive lag: $lag s (archive_timeout = 60 s; RPO target 900 s)"

say "6. disaster: the server and its volume are gone"
docker rm -f "$name" >/dev/null
docker volume rm "$vol1" >/dev/null
disaster=$(now)

say "7. restore to T"
docker volume create "$vol2" >/dev/null
docker run --rm --user postgres "${walg_env[@]}" --network "$network" -v "$vol2:/var/lib/postgresql/data" \
  -e PGDATA=/var/lib/postgresql/data --entrypoint pitr-restore.sh "$image" --time "$target"
start_db "$vol2"
until [[ "$(psql_ -c "SELECT pg_is_in_recovery()::int")" == 0 ]]; do sleep 1; done
rto=$(elapsed "$disaster")

say "8. check"
after=$(psql_ -c "SELECT count(*) FROM items")
gone=$(psql_ -c "SELECT count(*) FROM items WHERE note IN ('written-after-T', 'rpo-probe')")
echo "rows after recovery: $after (expected $expected); rows written after T: $gone (expected 0)"
echo "recovery time (server gone -> accepting writes): $rto s (RTO target 3600 s)"
if [[ "$after" != "$expected" || "$gone" != 0 ]]; then
  echo "DRILL FAILED" >&2
  exit 1
fi
docker exec -u postgres "$name" wal-g backup-list 2>&1 | tail -3
say "drill passed: rows=$before rto=${rto}s archive_lag=${lag}s"
