#!/usr/bin/env bash
# Playwright E2E tests in a container against the running stack (http://localhost:8080).
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="$(python3 -c "import json;print(json.load(open('$root/frontend/package.json'))['devDependencies']['@playwright/test'])")"
docker run --rm --network host -u "$(id -u):$(id -g)" -e HOME=/tmp \
  -e FORGE_E2E_BASE_URL="${FORGE_E2E_BASE_URL:-http://localhost:${FORGE_HTTP_PORT:-8080}}" \
  -v "$root/frontend:/app" -w /app \
  "mcr.microsoft.com/playwright:v${version}-noble" npx playwright test "$@"
