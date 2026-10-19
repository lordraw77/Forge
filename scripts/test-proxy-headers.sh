#!/usr/bin/env bash
# Checks that the web image believes X-Forwarded-For / -Proto only from FORGE_TRUSTED_PROXIES.
# Usage: scripts/test-proxy-headers.sh [image]   (default forge-web:dev; needs docker, curl, python3)
set -euo pipefail
image="${1:-forge-web:dev}"
tmp="$(mktemp -d)"
trap 'docker rm -f forge-proxy-test >/dev/null 2>&1 || true; kill "$echo_pid" 2>/dev/null || true; rm -rf "$tmp"' EXIT

cat > "$tmp/echo.py" <<'PY'
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"xff": self.headers.get("X-Forwarded-For"),
                           "proto": self.headers.get("X-Forwarded-Proto")}).encode()
        self.send_response(200); self.send_header("content-length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
HTTPServer(("0.0.0.0", 18000), H).serve_forever()
PY
python3 "$tmp/echo.py" & echo_pid=$!

check() { # name trusted expected-xff expected-proto curl-args...
  local name="$1" trusted="$2" xff="$3" proto="$4"; shift 4
  docker rm -f forge-proxy-test >/dev/null 2>&1 || true
  # Host networking so the "client" is 127.0.0.1; the upstream is the local echo server.
  docker run -d --name forge-proxy-test --network host -e FORGE_TRUSTED_PROXIES="$trusted" \
    --entrypoint sh "$image" -c '
      /docker-entrypoint.d/25-forge-nginx.sh
      sed -i "s#api:8000#127.0.0.1:18000#; s#listen 8080;#listen 18080;#" /tmp/forge-nginx/default.conf
      exec nginx -g "daemon off;"' >/dev/null
  for _ in $(seq 20); do curl -fs http://127.0.0.1:18080/nginx-health >/dev/null 2>&1 && break; sleep 0.5; done
  local got; got="$(curl -s "$@" http://127.0.0.1:18080/api/v1/x)"
  local want; want="{\"xff\": \"$xff\", \"proto\": \"$proto\"}"
  if [[ "$got" != "$want" ]]; then echo "FAIL $name: got $got, want $want" >&2; exit 1; fi
  echo "ok   $name"
}

check "forged header ignored when no proxy is trusted" "" 127.0.0.1 http \
  -H "X-Forwarded-For: 6.6.6.6" -H "X-Forwarded-Proto: https"
check "trusted proxy: client address and protocol are believed" "127.0.0.1" 203.0.113.9 https \
  -H "X-Forwarded-For: 203.0.113.9" -H "X-Forwarded-Proto: https"
check "trusted proxy: a hop added by the client is not believed" "127.0.0.1" 203.0.113.9 http \
  -H "X-Forwarded-For: 6.6.6.6, 203.0.113.9"
