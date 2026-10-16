#!/usr/bin/env bash
# Run spike E0.5a (Temporal + LangGraph, ADR-004) from the host against the compose stack.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
compose=(docker compose -f deploy/compose/docker-compose.yml --project-directory .
         --env-file .env --env-file deploy/compose/infra.env)
"${compose[@]}" up -d --wait db temporal

# shellcheck disable=SC1091
source deploy/compose/infra.env
export FORGE_SPIKE_DSN="postgresql://${FORGE_DB_USER}:${FORGE_DB_PASSWORD}@127.0.0.1:${FORGE_DB_PORT:-5432}/${FORGE_DB_NAME}"
export FORGE_TEMPORAL_ADDRESS="127.0.0.1:${FORGE_TEMPORAL_PORT:-7233}"

cd backend
exec uv run python -m spikes.e05a.run
