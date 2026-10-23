#!/usr/bin/env bash
# Air-gapped installation, step 1 (on a machine with internet access and docker, helm):
#   scripts/airgap-bundle.sh [OUTPUT_DIR] [-- helm template args, e.g. -f my-values.yaml]
# Lists every image the chart needs with these values, pulls them, and writes into OUTPUT_DIR
# (default ./airgap): forge-images.tar, images.txt, the packaged chart and SHA256SUMS.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="airgap"
if [[ $# -gt 0 && "$1" != "--" ]]; then out="$1"; shift; fi
[[ "${1:-}" == "--" ]] && shift
mkdir -p "$out"

# The images of the chart; global.imageRegistry is forced to docker.io so the list holds the
# upstream names that airgap-load.sh maps onto your registry.
helm template forge "$root/deploy/helm/forge" --set global.imageRegistry=docker.io \
  --set workerIngest.modelsVolume.enabled=false "$@" \
  | awk '$1 == "image:" { gsub(/"/, "", $2); print $2 }' | sort -u > "$out/images.txt"
echo "images:"; sed 's/^/  /' "$out/images.txt"

while read -r image; do docker pull --quiet "$image" >/dev/null; done < "$out/images.txt"
# shellcheck disable=SC2046 # one argument per image
docker save -o "$out/forge-images.tar" $(cat "$out/images.txt")
helm package "$root/deploy/helm/forge" -d "$out" >/dev/null
(cd "$out" && sha256sum forge-images.tar images.txt forge-*.tgz > SHA256SUMS)
echo "written to $out/: copy it to the isolated network, then run scripts/airgap-load.sh REGISTRY"
