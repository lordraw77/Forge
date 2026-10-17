#!/usr/bin/env bash
# Regenerate frontend/src/app/api-client from the backend OpenAPI spec (ADR-012). Never edit by hand.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root/backend"
uv run python - <<'PY'
import json
from forge.api.app import create_app
from forge.core.settings import Settings

spec = create_app(Settings(master_key="a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2U=")).openapi()
with open("../frontend/openapi.json", "w") as fh:
    json.dump(spec, fh, indent=2, sort_keys=True)
    fh.write("\n")
PY
rm -rf "$root/frontend/src/app/api-client"
docker run --rm -u "$(id -u):$(id -g)" -v "$root/frontend:/local" openapitools/openapi-generator-cli:v7.25.0 \
  generate -i /local/openapi.json -g typescript-angular -o /local/src/app/api-client --global-property skipFormModel=true \
  --additional-properties=ngVersion=21.2.0,providedIn=root,useSingleRequestParameter=true,supportsES6=true \
  >/dev/null
rm -f "$root/frontend/src/app/api-client/git_push.sh" "$root/frontend/src/app/api-client/.gitignore" \
  "$root/frontend/src/app/api-client/.openapi-generator-ignore"
rm -rf "$root/frontend/src/app/api-client/.openapi-generator"
echo "api client regenerated"
