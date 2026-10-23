#!/usr/bin/env bash
# Build and push versioned images, one repository per component:
#   <namespace>/forge-backend:<version>   <namespace>/forge-frontend:<version>
#   <namespace>/forge-db (PostgreSQL + WAL-G), forge-sandbox, forge-sandbox-runtime (E3.4, E3.7)
#   <namespace>/forge-backend-ingest:<version> (ingestion worker: Docling, CPU PyTorch, models)
#   and a `-slim` tag of both backend images that downloads model files at start
# Usage: scripts/release.sh <version> <namespace>   e.g. 0.1.0 docker.io/lordraw
# Performs no git operations: tagging the source tree is left to the release manager.
set -euo pipefail

version="${1:?version required, e.g. 0.1.0}"
namespace="${2:?namespace required, e.g. docker.io/lordraw}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]]; then
  echo "invalid semantic version: $version" >&2
  exit 1
fi

# Backend images come in two variants: <version> with model files baked in (air-gapped) and
# <version>-slim that downloads them at start (MODELS=download).
builds=(
  "forge-backend|$version|$root/backend|--build-arg MODELS=embedded"
  "forge-backend|$version-slim|$root/backend|--build-arg MODELS=download"
  "forge-backend-ingest|$version|$root/backend|--build-arg EXTRAS=ingest --build-arg MODELS=embedded"
  "forge-backend-ingest|$version-slim|$root/backend|--build-arg EXTRAS=ingest --build-arg MODELS=download"
  "forge-frontend|$version|$root/frontend|"
  "forge-db|$version|$root/deploy/db|"
  "forge-sandbox|$version|$root/sandbox|"
  "forge-sandbox-runtime|$version|$root/sandbox|-f $root/sandbox/Dockerfile.runtime"
)

for spec in "${builds[@]}"; do
  IFS='|' read -r repo tag context args <<< "$spec"
  image="$namespace/$repo:$tag"
  echo "build  $image"
  # shellcheck disable=SC2086 # args holds several --build-arg flags
  docker build --pull --build-arg VERSION="$version" $args -t "$image" "$context"
done

for spec in "${builds[@]}"; do
  IFS='|' read -r repo tag _ _ <<< "$spec"
  image="$namespace/$repo:$tag"
  echo "push   $image"
  docker push "$image"
done

echo "released $version to $namespace"
