#!/usr/bin/env bash
# Regenerates the screenshots of the user guide (docs/guide/images/{en,it}) from the running stack.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose=(docker compose -f deploy/compose/docker-compose.yml --project-directory "$root"
         --env-file "$root/.env" --env-file "$root/deploy/compose/infra.env")

admin_user="${FORGE_DOCS_ADMIN_USER:-docs-admin}"
admin_password="Docs-$(openssl rand -hex 12)"
printf '%s\n' "$admin_password" | "${compose[@]}" exec -T api forge users ensure-superuser \
  --username "$admin_user" --email "${admin_user}@forge.local" --full-name "Docs Admin" \
  --password-stdin --ready >/dev/null
"${compose[@]}" exec -T valkey sh -c 'valkey-cli -a "$VALKEY_PASSWORD" --no-auth-warning --scan --pattern "ratelimit:login:*" | xargs -r valkey-cli -a "$VALKEY_PASSWORD" --no-auth-warning del' >/dev/null

base_url="${FORGE_E2E_BASE_URL:-http://localhost:${FORGE_HTTP_PORT:-8080}}"
FORGE_URL="$base_url" FORGE_DOCS_USER="$admin_user" FORGE_DOCS_PASSWORD="$admin_password" \
  python3 -I "$root/scripts/docs-seed.py"

[ -z "${SEED_ONLY:-}" ] || exit 0

version="$(python3 -c "import json;print(json.load(open('$root/frontend/package.json'))['devDependencies']['@playwright/test'])")"
docker run --rm --network host -u "$(id -u):$(id -g)" -e HOME=/tmp \
  -e FORGE_E2E_ADMIN_USER="$admin_user" -e FORGE_E2E_ADMIN_PASSWORD="$admin_password" \
  -e FORGE_E2E_BASE_URL="$base_url" \
  -e RECON="${RECON:-}" -e SHOTS="${SHOTS:-}" -e DOCS_IMAGES_DIR=/work/docs/guide/images \
  -v "$root:/work" -w /work/frontend \
  "mcr.microsoft.com/playwright:v${version}-noble" npx playwright test -c docs-shots/playwright.config.ts "$@"
