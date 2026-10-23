#!/usr/bin/env bash
# A throw-away stack on this host for the load tests (E3.7): its own database, bucket, search
# index and Temporal task queues on the Compose infrastructure, the mock model provider, one API
# process, one runs worker and one ingest worker as host processes. Then scripts/loadtest.py.
#
#   scripts/loadtest-env.sh [loadtest.py arguments]      e.g. --scenarios gateway --calls 500
#
# Needs the Compose infrastructure (make docker/run or the db, valkey, search, s3 and temporal
# services). Everything it creates is removed at the end. Numbers depend on the host (the lab host
# is 4 vCPU, shared): they are for comparing builds and checking the targets, not for sizing.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
# shellcheck disable=SC1091
source deploy/compose/infra.env
set -a
# shellcheck disable=SC1091
source .env
set +a

run="load$(date +%s)"
db="forge_$run"
logs="${LOADTEST_LOGS:-$(mktemp -d)}"
api_port="${LOADTEST_API_PORT:-18000}"
mock_port="${LOADTEST_MOCK_PORT:-18100}"
compose=(docker compose -f deploy/compose/docker-compose.yml --project-directory . --env-file .env --env-file deploy/compose/infra.env)
pids=()

cleanup() {
  for pid in "${pids[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  "${compose[@]}" exec -T db psql -U postgres -d postgres -q -c "DROP DATABASE IF EXISTS $db WITH (FORCE)" >/dev/null 2>&1 || true
  echo "logs: $logs"
}
trap cleanup EXIT

"${compose[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d postgres -q \
  -c "CREATE DATABASE $db OWNER $FORGE_DB_USER"
"${compose[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d "$db" -q \
  -c "CREATE EXTENSION IF NOT EXISTS vector" -c "REVOKE CREATE ON SCHEMA public FROM PUBLIC"

export FORGE_DATABASE_URL="postgresql+asyncpg://$FORGE_DB_USER:$FORGE_DB_PASSWORD@127.0.0.1:${FORGE_DB_PORT:-5432}/$db"
export FORGE_REDIS_URL="redis://:$VALKEY_PASSWORD@127.0.0.1:${FORGE_VALKEY_PORT:-6379}/14"
export FORGE_S3_ENDPOINT="http://127.0.0.1:${FORGE_S3_PORT:-8333}" FORGE_S3_ACCESS_KEY="$S3_ACCESS_KEY" FORGE_S3_SECRET_KEY="$S3_SECRET_KEY"
export FORGE_S3_BUCKET="forge-$run"
export FORGE_SEARCH_URL="https://127.0.0.1:${FORGE_SEARCH_PORT:-9200}" FORGE_SEARCH_PASSWORD
export FORGE_SEARCH_CA_FILE="$root/deploy/compose/search/certs/root-ca.pem" FORGE_SEARCH_INDEX="forge-$run-chunks"
export FORGE_TEMPORAL_ADDRESS="127.0.0.1:${FORGE_TEMPORAL_PORT:-7233}"
export FORGE_TEMPORAL_RUNS_QUEUE="runs-$run" FORGE_TEMPORAL_INGEST_QUEUE="ingest-$run"
export FORGE_ADMIN_USERNAME=loadadmin FORGE_ADMIN_EMAIL=loadadmin@example.com
export FORGE_ADMIN_PASSWORD="Load-Admin-Pass-$run"
export FORGE_PUBLIC_URL="http://127.0.0.1:$api_port"
export FORGE_TOOLS_ALLOW_PRIVATE_HOSTS=true
export FORGE_METRICS_PORT=0 FORGE_LOG_LEVEL=WARNING

cd backend
uv run forge migrate >"$logs/migrate.log" 2>&1
printf '%s' "$FORGE_ADMIN_PASSWORD" | uv run forge users ensure-superuser --username loadadmin \
  --email loadadmin@example.com --password-stdin --ready >"$logs/user.log" 2>&1

uv run uvicorn tests.support.mock_provider:app --port "$mock_port" --log-level warning >"$logs/mock.log" 2>&1 &
pids+=($!)
FORGE_METRICS_PORT=0 uv run forge api --port "$api_port" >"$logs/api.log" 2>&1 &
pids+=($!)
uv run forge worker runs >"$logs/worker-runs.log" 2>&1 &
pids+=($!)
uv run forge worker ingest >"$logs/worker-ingest.log" 2>&1 &
pids+=($!)

for _ in $(seq 1 90); do
  curl -fs "http://127.0.0.1:$api_port/readyz" >/dev/null 2>&1 && curl -fs "http://127.0.0.1:$mock_port/openapi.json" >/dev/null 2>&1 && break
  sleep 1
done
curl -fs "http://127.0.0.1:$api_port/readyz" >/dev/null || { echo "api not ready, see $logs/api.log" >&2; exit 1; }
sleep 5  # workers register

uv run python "$root/scripts/loadtest.py" --url "http://127.0.0.1:$api_port" \
  --login loadadmin --password "$FORGE_ADMIN_PASSWORD" \
  --provider-url "http://127.0.0.1:$mock_port/v1" "$@"
