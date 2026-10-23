# ruff: noqa: S603, S607  (docker is started by name, with fixed arguments)
"""A throw-away OpenBao in dev mode with a Transit key and an AppRole, for the vault tests.
Needs docker and the image (pulled on first use)."""

from __future__ import annotations

import socket
import subprocess
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import httpx

IMAGE = "openbao/openbao:2.4.0"
ROOT_TOKEN = "root-test-token"


@dataclass
class Bao:
    url: str
    token: str
    role_id: str
    secret_id: str
    container: str

    def stop(self) -> None:
        subprocess.run(["docker", "stop", self.container], capture_output=True, check=False)

    def api(self, method: str, path: str, **kw: object) -> httpx.Response:
        return httpx.request(
            method,
            f"{self.url}/v1/{path}",
            headers={"X-Vault-Token": self.token},
            timeout=10,
            **kw,  # type: ignore[arg-type]
        )


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextmanager
def openbao() -> Iterator[Bao]:
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode:
        subprocess.run(["docker", "pull", "-q", IMAGE], check=True, capture_output=True)
    port = _free_port()
    name = f"forge-test-bao-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "-p",
            f"127.0.0.1:{port}:8200",
            "-e",
            f"BAO_DEV_ROOT_TOKEN_ID={ROOT_TOKEN}",
            "-e",
            "BAO_DEV_LISTEN_ADDRESS=0.0.0.0:8200",
            IMAGE,
            "server",
            "-dev",
        ],
        check=True,
        capture_output=True,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if httpx.get(f"{url}/v1/sys/health", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.3)
    else:
        raise RuntimeError("OpenBao did not start")
    h = {"X-Vault-Token": ROOT_TOKEN}

    def call(method: str, path: str, **kw: object) -> httpx.Response:
        r = httpx.request(method, f"{url}/v1/{path}", headers=h, timeout=10, **kw)  # type: ignore[arg-type]
        r.raise_for_status()
        return r

    call("POST", "sys/mounts/transit", json={"type": "transit"})
    call("POST", "transit/keys/forge", json={"type": "aes256-gcm96"})
    policy = (
        'path "transit/encrypt/forge" { capabilities = ["update"] }\n'
        'path "transit/decrypt/forge" { capabilities = ["update"] }\n'
        'path "transit/keys/forge" { capabilities = ["read"] }\n'
    )
    call("PUT", "sys/policies/acl/forge", json={"policy": policy})
    call("POST", "sys/auth/approle", json={"type": "approle"})
    call("POST", "auth/approle/role/forge", json={"token_policies": ["forge"], "token_ttl": "2s"})
    role_id = call("GET", "auth/approle/role/forge/role-id").json()["data"]["role_id"]
    secret_id = call("POST", "auth/approle/role/forge/secret-id").json()["data"]["secret_id"]
    bao = Bao(url, ROOT_TOKEN, role_id, secret_id, name)
    try:
        yield bao
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
