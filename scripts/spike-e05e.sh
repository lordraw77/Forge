#!/usr/bin/env bash
# Run spike E0.5e (BM25 on OpenSearch + pgvector hybrid, ADR-016) against the compose stack.
# Usage: scripts/spike-e05e.sh [rows] [dims]     defaults: 1000000 384
# Stops the application services during the run (memory) and restarts them afterwards.
set -euo pipefail

rows="${1:-1000000}"
dims="${2:-384}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
compose=(docker compose -f "$root/deploy/compose/docker-compose.yml" --project-directory "$root"
         --env-file "$root/.env" --env-file "$root/deploy/compose/infra.env")
"${compose[@]}" up -d --wait db search
"${compose[@]}" stop web api worker-runs worker-ingest temporal s3 valkey
"${compose[@]}" --profile observability stop otel-collector prometheus loki grafana 2>/dev/null || true
trap '"${compose[@]}" up -d --wait >/dev/null' EXIT

# shellcheck disable=SC1091
source deploy/compose/infra.env
export FORGE_SPIKE_DSN="postgresql://${FORGE_DB_USER}:${FORGE_DB_PASSWORD}@127.0.0.1:${FORGE_DB_PORT:-5432}/${FORGE_DB_NAME}"
export FORGE_SPIKE_SEARCH_URL="https://127.0.0.1:${FORGE_SEARCH_PORT:-9200}"
export FORGE_SPIKE_SEARCH_PASSWORD="$FORGE_SEARCH_PASSWORD"
export FORGE_SPIKE_SEARCH_CA="$root/deploy/compose/search/certs/root-ca.pem"
label="r${rows}d${dims}"
cd backend
uv run python -m spikes.e05e.bench --label "$label" --rows "$rows" --other-rows $((rows / 10)) \
  --dims "$dims" --out "$root/docs/spikes/e05e-$label.json"
docker exec forge-db-1 psql -U postgres -d "$FORGE_DB_NAME" -qc "DROP SCHEMA spike_e05b CASCADE"
curl -fsS -o /dev/null --cacert "$FORGE_SPIKE_SEARCH_CA" -u "forge:$FORGE_SEARCH_PASSWORD" \
  -XDELETE "$FORGE_SPIKE_SEARCH_URL/forge-spike-e05e"
