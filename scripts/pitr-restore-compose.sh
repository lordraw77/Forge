#!/usr/bin/env bash
# Restore the Compose stack's PostgreSQL from the WAL-G archive (deploy/compose/pitr.yml).
#   scripts/pitr-restore-compose.sh ['2026-10-22 09:30:00+00'] yes      (empty time = latest)
# The stack is stopped, the current data directory is KEPT in a volume named
# forge_db-data-before-restore-<time stamp> (delete it yourself once the result is checked), the
# volume forge_db-data is emptied and filled from the backup, and the database is started: it
# replays the archived WAL to the target and promotes. Everything else is then started again.
set -euo pipefail

target="${1:-}"
confirm="${2:-}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
if [[ "$confirm" != "yes" ]]; then
  echo "This stops Forge and replaces the database with the backup${target:+ as of $target}." >&2
  echo "The current data is kept in a volume. Run again with a second argument: yes" >&2
  exit 1
fi
compose=(docker compose -f deploy/compose/docker-compose.yml -f deploy/compose/pitr.yml
         --project-directory . --env-file .env --env-file deploy/compose/infra.env)
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
old="forge_db-data-before-restore-$stamp"

echo "== stopping the stack"
"${compose[@]}" stop web api worker-runs worker-ingest temporal backup db
echo "== keeping the current data in volume $old"
docker volume create "$old" >/dev/null
docker run --rm -v forge_db-data:/from:ro -v "$old:/to" --entrypoint sh forge-db:dev -c 'cp -a /from/. /to/'
echo "== emptying forge_db-data and restoring"
docker run --rm -v forge_db-data:/data --entrypoint sh forge-db:dev -c 'find /data -mindepth 1 -delete'
args=()
[[ -n "$target" ]] && args=(--time "$target")
"${compose[@]}" run --rm --no-deps --user postgres -e PGDATA=/var/lib/postgresql/data \
  --entrypoint pitr-restore.sh db "${args[@]}"
echo "== starting the database (WAL replay)"
"${compose[@]}" up -d db
until "${compose[@]}" exec -T db psql -U postgres -d postgres -qtA -c "SELECT NOT pg_is_in_recovery()" 2>/dev/null | grep -q t; do
  sleep 2
done
echo "== starting the rest"
"${compose[@]}" up -d
echo "restored. The previous data is in volume $old."
