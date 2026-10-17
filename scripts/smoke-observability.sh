#!/usr/bin/env bash
# Send one OTLP metric and one OTLP log to the collector; find them in Prometheus and Loki.
set -euo pipefail

now="$(date +%s%N)"
run="smoke-$(date +%s)"
resource='{"attributes":[{"key":"service.name","value":{"stringValue":"forge-smoke"}}]}'
curl -fsS -o /dev/null -H 'Content-Type: application/json' http://127.0.0.1:4318/v1/metrics -d \
  '{"resourceMetrics":[{"resource":'"$resource"',"scopeMetrics":[{"metrics":[{"name":"forge_smoke_total","sum":{"aggregationTemporality":2,"isMonotonic":true,"dataPoints":[{"asInt":"1","timeUnixNano":"'"$now"'"}]}}]}]}]}'
curl -fsS -o /dev/null -H 'Content-Type: application/json' http://127.0.0.1:4318/v1/logs -d \
  '{"resourceLogs":[{"resource":'"$resource"',"scopeLogs":[{"logRecords":[{"timeUnixNano":"'"$now"'","severityText":"INFO","body":{"stringValue":"'"$run"'"}}]}]}]}'

for _ in $(seq 1 30); do
  sleep 2
  metric="$(curl -fsS 'http://127.0.0.1:9090/api/v1/query?query=forge_smoke_total' | grep -c forge-smoke || true)"
  log="$(curl -fsS -G http://127.0.0.1:3100/loki/api/v1/query_range \
    --data-urlencode 'query={service_name="forge-smoke"}' | grep -c "$run" || true)"
  if [[ "$metric" -gt 0 && "$log" -gt 0 ]]; then
    echo "observability smoke: metric in Prometheus, log in Loki"
    exit 0
  fi
done
echo "observability smoke failed (metric=$metric log=$log)" >&2
exit 1
