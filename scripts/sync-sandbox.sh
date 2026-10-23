#!/usr/bin/env bash
# The sandbox sources live in backend/src/forge/sandbox (linted and tested with the backend); the
# two images are built from sandbox/. Copy them over before building (`make sandbox/build` does).
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cp "$root/backend/src/forge/sandbox/server.py" "$root/backend/src/forge/sandbox/runner.py" "$root/sandbox/"
