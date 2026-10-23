# ruff: noqa: S603, S607  (docker is started by name, with fixed arguments)
"""The real sandbox runner service on a local port, for tests that run Python tools and code nodes
(needs the container engine and the `forge-sandbox-runtime:dev` image)."""

from __future__ import annotations

import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import uvicorn

from forge.sandbox.server import Config, create_app

IMAGE = "forge-sandbox-runtime:dev"
TOKEN = "test-sandbox-token"
SOCKET = "/var/run/docker.sock"
ROOT = Path(__file__).resolve().parents[3]


def ensure_image() -> None:
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode == 0:
        return
    directory = ROOT / "sandbox"
    subprocess.run(
        [
            "docker",
            "build",
            "-q",
            "-f",
            str(directory / "Dockerfile.runtime"),
            "-t",
            IMAGE,
            str(directory),
        ],
        check=True,
    )


@contextmanager
def sandbox_service() -> Iterator[str]:
    ensure_image()
    cfg = Config()
    cfg.token, cfg.socket, cfg.image = TOKEN, SOCKET, IMAGE
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(cfg), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "sandbox service did not start"
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
