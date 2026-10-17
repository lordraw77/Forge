#!/usr/bin/env bash
# Start the database, apply migrations and run backend integration tests from the host.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
"$root/scripts/gen-env.sh" >/dev/null

compose=(docker compose -f deploy/compose/docker-compose.yml --project-directory .
         --env-file .env --env-file deploy/compose/infra.env)
"${compose[@]}" up -d --wait db valkey

# shellcheck disable=SC1091
source deploy/compose/infra.env
port="${FORGE_DB_PORT:-5432}"
test_db="${FORGE_DB_NAME}_test"

# A dedicated database, so tests never touch the development data of the running stack.
"${compose[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d postgres -q <<SQL
SELECT 'CREATE DATABASE ${test_db} OWNER ${FORGE_DB_USER}'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '${test_db}')\gexec
SQL
"${compose[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d "${test_db}" -q \
  -c "CREATE EXTENSION IF NOT EXISTS vector" -c "CREATE EXTENSION IF NOT EXISTS pg_search" \
  -c "REVOKE CREATE ON SCHEMA public FROM PUBLIC"

url="postgresql+asyncpg://${FORGE_DB_USER}:${FORGE_DB_PASSWORD}@127.0.0.1:${port}/${test_db}"

cd backend
FORGE_DATABASE_URL="$url" uv run forge migrate
FORGE_TEST_DATABASE_URL="$url" \
FORGE_TEST_REDIS_URL="redis://:${VALKEY_PASSWORD}@127.0.0.1:${FORGE_VALKEY_PORT:-6379}/15" \
  uv run pytest -m integration "$@"
