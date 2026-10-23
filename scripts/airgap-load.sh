#!/usr/bin/env bash
# Air-gapped installation, step 2 (inside the isolated network, with docker or podman):
#   scripts/airgap-load.sh REGISTRY [BUNDLE_DIR]      e.g. registry.local:5000 ./airgap
# Verifies the checksums, loads the images and pushes each as REGISTRY/<repository>:<tag>. Then:
#   helm install forge forge-*.tgz --set global.imageRegistry=REGISTRY
set -euo pipefail

registry="${1:?usage: airgap-load.sh REGISTRY [BUNDLE_DIR]}"
dir="${2:-airgap}"
registry="${registry%/}"
engine="$(command -v docker || command -v podman || { echo "docker or podman is required" >&2; exit 1; })"

(cd "$dir" && sha256sum --check --quiet SHA256SUMS)
"$engine" load -i "$dir/forge-images.tar"
while read -r image; do
  # docker.io/lordraw/forge-web:1.0 -> REGISTRY/lordraw/forge-web:1.0
  target="$registry/${image#docker.io/}"
  "$engine" tag "$image" "$target"
  "$engine" push "$target"
  echo "pushed $target"
done < "$dir/images.txt"
