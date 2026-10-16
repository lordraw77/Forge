#!/usr/bin/env bash
# Run spike E0.5d (LiteLLM providers, ADR-005) inside a network namespace with loopback only:
# proves the gateway library needs no outbound network (air-gapped installs). Needs root.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root/backend"
uv sync --group spikes --frozen -q  # install while the network is still available

export LITELLM_LOCAL_MODEL_COST_MAP=True
export SPIKE_E05D_OUT="${SPIKE_E05D_OUT:-$root/docs/spikes/e05d-litellm.json}"
exec unshare --net sh -c 'ip link set lo up && exec .venv/bin/python -m spikes.e05d.run'
