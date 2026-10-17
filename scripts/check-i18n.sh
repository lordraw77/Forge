#!/usr/bin/env bash
# Fail if any UI message lacks an Italian translation or its placeholders differ (US-1.4.3).
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root/frontend"
if [[ "${FORGE_NATIVE_NODE:-0}" == 1 ]]; then  # CI: Node is already installed
  NG_CLI_ANALYTICS=false npx ng extract-i18n --format json --output-path src/locale >/dev/null
else
  docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -e NG_CLI_ANALYTICS=false -e CI=1 -v "$PWD:/app" -w /app \
    node:24.22.0-trixie-slim npx ng extract-i18n --format json --output-path src/locale >/dev/null
fi
python3 - <<'PY'
import json, re, sys
src = json.load(open("src/locale/messages.json"))["translations"]
it = json.load(open("public/i18n/it.json"))["translations"]
ph = lambda s: sorted(re.findall(r"\{\$[A-Za-z_]+\}", s))
missing = [k for k in src if k not in it]
bad = [k for k in src if k in it and ph(src[k]) != ph(it[k])]
for k in missing: print(f"missing it translation: {k} {src[k]!r}")
for k in bad: print(f"placeholder mismatch: {k} {src[k]!r} -> {it[k]!r}")
print(f"{len(src)} messages, {len(missing)} missing, {len(bad)} placeholder mismatches")
sys.exit(1 if missing or bad else 0)
PY
