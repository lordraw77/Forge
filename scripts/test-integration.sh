#!/usr/bin/env bash
# Start the database, apply migrations and run backend integration tests from the host.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
"$root/scripts/gen-env.sh" >/dev/null

compose=(docker compose -f deploy/compose/docker-compose.yml --project-directory .
         --env-file .env --env-file deploy/compose/infra.env)
"${compose[@]}" up -d --wait db

# shellcheck disable=SC1091
source deploy/compose/infra.env
port="${FORGE_DB_PORT:-5432}"
url="postgresql+asyncpg://${FORGE_DB_USER}:${FORGE_DB_PASSWORD}@127.0.0.1:${port}/${FORGE_DB_NAME}"

cd backend
FORGE_DATABASE_URL="$url" uv run forge migrate
FORGE_TEST_DATABASE_URL="$url" uv run pytest -m integration "$@"
