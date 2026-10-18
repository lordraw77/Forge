#!/usr/bin/env bash
# Smoke test of a running stack: web → api, api readiness, Temporal workers on both queues.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
compose=(docker compose -f deploy/compose/docker-compose.yml --project-directory .
         --env-file .env --env-file deploy/compose/infra.env)
base="http://localhost:${FORGE_HTTP_PORT:-8080}"
fail=0

check() {
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then echo "ok    $name"; else echo "FAIL  $name"; fail=1; fi
}

check "web serves the UI"            curl -fsS "$base/"
check "web proxies /api to the API"  curl -fsS "$base/api/v1/version"
check "api is ready (db, valkey, search)" "${compose[@]}" exec -T api python -c \
  "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=5)"

for queue in runs ingest; do
  check "worker '$queue' runs a workflow" "${compose[@]}" exec -T "worker-$queue" python -c "
import asyncio, uuid
from temporalio.client import Client
from forge.core.settings import get_settings
from forge.workers.runner import PingWorkflow
async def main():
    s = get_settings()
    c = await Client.connect(s.temporal_address, namespace=s.temporal_namespace)
    r = await c.execute_workflow(PingWorkflow.run, 'smoke', id=f'smoke-{uuid.uuid4()}',
                                 task_queue='$queue', execution_timeout=__import__('datetime').timedelta(seconds=30))
    assert r == 'pong: smoke', r
asyncio.run(main())
"
done

exit "$fail"
