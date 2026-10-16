#!/usr/bin/env python3
"""Push the Docker Hub overviews from deploy/dockerhub/*.md to <namespace>/<repo>.

Uses the credentials of the existing `docker login` (~/.docker/config.json) and never prints them.
Usage: scripts/dockerhub-overview.py <hub-namespace>      e.g. lordraw
"""

from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://hub.docker.com/v2"
ROOT = Path(__file__).resolve().parent.parent
DESCRIPTIONS = {  # Docker Hub short description, max 100 characters
    "forge-backend": "Forge backend: API, Temporal workers and migrations of a self-hosted, "
    "LLM-agnostic AI platform.",
    "forge-frontend": "Forge web UI: Angular + PrimeNG app served by unprivileged nginx, "
    "proxying to forge-backend.",
}


def request(method: str, path: str, body: dict[str, object], token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(  # noqa: S310 - fixed https endpoint
        API + path, method=method, headers=headers, data=json.dumps(body).encode()
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        raw = resp.read()
    return json.loads(raw) if raw else {}


def hub_token() -> str:
    config = json.loads((Path.home() / ".docker" / "config.json").read_text())
    auth = config["auths"]["https://index.docker.io/v1/"]["auth"]
    user, secret = base64.b64decode(auth).decode().split(":", 1)
    return str(request("POST", "/auth/token", {"identifier": user, "secret": secret})["access_token"])


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    namespace = sys.argv[1]
    token = hub_token()
    for repo, short in DESCRIPTIONS.items():
        assert len(short) <= 100, f"{repo}: short description is {len(short)} chars"
        full = (ROOT / "deploy" / "dockerhub" / f"{repo}.md").read_text()
        try:
            request(
                "PATCH",
                f"/repositories/{namespace}/{repo}/",
                {"description": short, "full_description": full},
                token,
            )
            print(f"updated {namespace}/{repo}")
        except urllib.error.HTTPError as exc:
            print(f"failed  {namespace}/{repo}: HTTP {exc.code}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
