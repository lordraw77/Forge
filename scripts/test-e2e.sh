#!/usr/bin/env bash
# Playwright E2E tests in a container against the running stack (http://localhost:8080).
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose=(docker compose -f deploy/compose/docker-compose.yml --project-directory "$root"
         --env-file "$root/.env" --env-file "$root/deploy/compose/infra.env")

# Dedicated automation Super Admin with a fresh random password for every run.
admin_user="${FORGE_E2E_ADMIN_USER:-e2e-admin}"
admin_password="E2e-$(openssl rand -hex 12)"
printf '%s\n' "$admin_password" | "${compose[@]}" exec -T api forge users ensure-superuser \
  --username "$admin_user" --email "${admin_user}@forge.local" --full-name "E2E Admin" \
  --password-stdin --ready >/dev/null
"${compose[@]}" exec -T valkey sh -c 'valkey-cli -a "$VALKEY_PASSWORD" --no-auth-warning --scan --pattern "ratelimit:login:*" | xargs -r valkey-cli -a "$VALKEY_PASSWORD" --no-auth-warning del' >/dev/null

version="$(python3 -c "import json;print(json.load(open('$root/frontend/package.json'))['devDependencies']['@playwright/test'])")"
docker run --rm --network host -u "$(id -u):$(id -g)" -e HOME=/tmp \
  -e FORGE_E2E_ADMIN_USER="$admin_user" -e FORGE_E2E_ADMIN_PASSWORD="$admin_password" \
  -e FORGE_E2E_DEMO_PASSWORD="${FORGE_E2E_DEMO_PASSWORD:-}" \
  -e FORGE_E2E_BASE_URL="${FORGE_E2E_BASE_URL:-http://localhost:${FORGE_HTTP_PORT:-8080}}" \
  -v "$root/frontend:/app" -w /app \
  "mcr.microsoft.com/playwright:v${version}-noble" npx playwright test "$@"
