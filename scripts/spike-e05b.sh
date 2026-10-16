#!/usr/bin/env bash
# Run spike E0.5b (hybrid retrieval, ADR-008) against the compose database.
# Usage: scripts/spike-e05b.sh [rows] [dims]     defaults: 1000000 384
# Stops the application services during the run (memory) and restarts them afterwards.
set -euo pipefail

rows="${1:-1000000}"
dims="${2:-384}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
compose=(docker compose -f deploy/compose/docker-compose.yml --project-directory .
         --env-file .env --env-file deploy/compose/infra.env)
"${compose[@]}" up -d --wait db
"${compose[@]}" stop web api worker-runs worker-ingest temporal s3 valkey
trap '"${compose[@]}" up -d --wait >/dev/null' EXIT

# shellcheck disable=SC1091
source deploy/compose/infra.env
export FORGE_SPIKE_DSN="postgresql://${FORGE_DB_USER}:${FORGE_DB_PASSWORD}@127.0.0.1:${FORGE_DB_PORT:-5432}/${FORGE_DB_NAME}"
label="r${rows}d${dims}"
cd backend
uv run python -m spikes.e05b.bench --label "$label" --rows "$rows" --other-rows $((rows / 10)) \
  --dims "$dims" --out "$root/docs/spikes/e05b-$label.json"
docker exec forge-db-1 psql -U postgres -d "$FORGE_DB_NAME" -qc "DROP SCHEMA spike_e05b CASCADE"
