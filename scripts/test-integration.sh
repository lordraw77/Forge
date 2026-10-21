#!/usr/bin/env bash
# Start the database, apply migrations and run backend integration tests from the host.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
"$root/scripts/gen-env.sh" >/dev/null

compose=(docker compose -f deploy/compose/docker-compose.yml --project-directory .
         --env-file .env --env-file deploy/compose/infra.env)
"${compose[@]}" --profile idp-test up -d --wait db valkey search s3 temporal openldap

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
  -c "CREATE EXTENSION IF NOT EXISTS vector" \
  -c "REVOKE CREATE ON SCHEMA public FROM PUBLIC"

url="postgresql+asyncpg://${FORGE_DB_USER}:${FORGE_DB_PASSWORD}@127.0.0.1:${port}/${test_db}"

# AC11: a database that is empty at every run, migrated by `forge migrate` like a first install.
fresh_db="${FORGE_DB_NAME}_fresh"
"${compose[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d postgres -q <<SQL
DROP DATABASE IF EXISTS ${fresh_db} WITH (FORCE);
CREATE DATABASE ${fresh_db} OWNER ${FORGE_DB_USER};
SQL
"${compose[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d "${fresh_db}" -q \
  -c "CREATE EXTENSION IF NOT EXISTS vector" \
  -c "REVOKE CREATE ON SCHEMA public FROM PUBLIC"
fresh_url="postgresql+asyncpg://${FORGE_DB_USER}:${FORGE_DB_PASSWORD}@127.0.0.1:${port}/${fresh_db}"

cd backend
FORGE_DATABASE_URL="$url" uv run forge migrate
FORGE_DATABASE_URL="$fresh_url" uv run forge migrate
export FORGE_TEST_FRESH_DATABASE_URL="$fresh_url"
# RAG tests use their own index alias, bucket and Temporal task queue (see test_rag.py).
FORGE_TEST_DATABASE_URL="$url" \
FORGE_TEST_REDIS_URL="redis://:${VALKEY_PASSWORD}@127.0.0.1:${FORGE_VALKEY_PORT:-6379}/15" \
FORGE_TEST_SEARCH_URL="https://127.0.0.1:${FORGE_SEARCH_PORT:-9200}" \
FORGE_TEST_SEARCH_PASSWORD="$FORGE_SEARCH_PASSWORD" \
FORGE_TEST_SEARCH_CA="$root/deploy/compose/search/certs/root-ca.pem" \
FORGE_TEST_S3_ENDPOINT="http://127.0.0.1:${FORGE_S3_PORT:-8333}" \
FORGE_TEST_S3_ACCESS_KEY="$S3_ACCESS_KEY" \
FORGE_TEST_S3_SECRET_KEY="$S3_SECRET_KEY" \
FORGE_TEST_LDAP_URL="ldap://127.0.0.1:${FORGE_LDAP_TEST_PORT:-3890}" \
FORGE_TEST_TEMPORAL_ADDRESS="127.0.0.1:${FORGE_TEMPORAL_PORT:-7233}" \
  uv run pytest -m integration "$@"
