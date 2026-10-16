#!/usr/bin/env bash
# Build and push versioned images, one repository per component:
#   <namespace>/forge-backend:<version>   <namespace>/forge-frontend:<version>
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

declare -A contexts=([forge-backend]="$root/backend" [forge-frontend]="$root/frontend")

for repo in forge-backend forge-frontend; do
  image="$namespace/$repo:$version"
  echo "build  $image"
  docker build --pull --build-arg VERSION="$version" -t "$image" "${contexts[$repo]}"
done

for repo in forge-backend forge-frontend; do
  image="$namespace/$repo:$version"
  echo "push   $image"
  docker push "$image"
done

echo "released $version to $namespace"
