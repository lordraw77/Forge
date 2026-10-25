# ruff: noqa: S603, S607  (docker is started by name, with fixed arguments)
"""E3.4 the sandbox runner service against the real container engine.

Needs the Docker socket and builds `forge-sandbox-runtime:dev` if it is missing. Every case is a
real throw-away container: isolation is checked by what the code can and cannot do.
"""

from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from forge.sandbox.server import Config, create_app

SOCKET = os.environ.get("SANDBOX_DOCKER_SOCKET", "/var/run/docker.sock")
ROOT = Path(__file__).resolve().parents[3]
IMAGE = "forge-sandbox-runtime:dev"
TOKEN = "test-sandbox-token"

pytestmark = pytest.mark.skipif(not Path(SOCKET).exists(), reason="needs the container engine")


@pytest.fixture(scope="module")
def sandbox() -> Iterator[TestClient]:
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode:
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
    cfg = Config()
    cfg.token, cfg.socket, cfg.image = TOKEN, SOCKET, IMAGE
    with TestClient(create_app(cfg)) as client:
        yield client


def run(client: TestClient, code: str, **extra: Any) -> dict[str, Any]:
    r = client.post(
        "/v1/run",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"code": code, "timeout_s": 10} | extra,
    )
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def test_returns_the_result_and_what_was_printed(sandbox: TestClient) -> None:
    out = run(
        sandbox,
        "def run(args):\n    print('hello')\n    return {'sum': sum(args['numbers'])}",
        args={"numbers": [1, 2, 3]},
    )
    assert out["status"] == "ok"
    assert out["result"] == {"sum": 6}
    assert out["stdout"] == "hello\n"
    assert out["duration_ms"] > 0


def test_errors_are_reported_not_raised(sandbox: TestClient) -> None:
    out = run(sandbox, "def run(args):\n    return 1 / 0")
    assert out["status"] == "error"
    assert "ZeroDivisionError" in out["error"]
    assert "SyntaxError" in run(sandbox, "def run(args) return 1")["error"]
    assert "run(args)" in run(sandbox, "x = 1")["error"]
    assert "JSON" in run(sandbox, "def run(args):\n    return {1, 2}")["error"]


def test_a_forged_result_line_cannot_replace_the_real_one(sandbox: TestClient) -> None:
    code = (
        "import os\n"
        "def run(args):\n"
        '    os.write(1, b\'\\n\\x1eFORGE_RESULT\\x1e{"ok": true, "result": "forged"}\\n\')\n'
        "    return 'real'\n"
    )
    assert run(sandbox, code)["result"] == "real"


def test_timeout_kills_the_container(sandbox: TestClient) -> None:
    out = run(sandbox, "def run(args):\n    while True:\n        pass", timeout_s=2)
    assert out["status"] == "timeout"
    left = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "label=forge.sandbox"], capture_output=True, text=True
    ).stdout.split()
    assert left == []


def test_memory_limit(sandbox: TestClient) -> None:
    out = run(sandbox, "def run(args):\n    return len(bytearray(400 * 1024 * 1024))", memory_mb=64)
    assert out["status"] in ("oom", "error")
    assert out["status"] != "ok"


def test_no_network(sandbox: TestClient) -> None:
    code = (
        "import socket\n"
        "def run(args):\n"
        "    s = socket.socket()\n"
        "    s.settimeout(3)\n"
        "    s.connect(('1.1.1.1', 80))\n"
    )
    out = run(sandbox, code)
    assert out["status"] == "error"
    assert "OSError" in out["error"] or "Error" in out["error"]


def test_filesystem_is_read_only_except_tmp_and_runs_as_nobody(sandbox: TestClient) -> None:
    code = (
        "import os\n"
        "def run(args):\n"
        "    res = {'uid': os.getuid()}\n"
        "    try:\n"
        "        open('/etc/forge-test', 'w').write('x')\n"
        "        res['etc'] = 'writable'\n"
        "    except OSError:\n"
        "        res['etc'] = 'read-only'\n"
        "    open('/tmp/ok', 'w').write('x')\n"
        "    res['tmp'] = 'writable'\n"
        "    res['env'] = sorted(k for k in os.environ if k.startswith('FORGE'))\n"
        "    return res\n"
    )
    out = run(sandbox, code)
    assert out["result"] == {"uid": 65534, "etc": "read-only", "tmp": "writable", "env": []}


def test_runaway_output_is_cut(sandbox: TestClient) -> None:
    out = run(sandbox, "def run(args):\n    print('x' * 5_000_000)\n    return 1")
    assert out["status"] == "ok"
    assert len(out["stdout"]) <= 64 * 1024


def test_token_and_limits(sandbox: TestClient) -> None:
    bad = sandbox.post("/v1/run", headers={"Authorization": "Bearer nope"}, json={"code": "x"})
    assert bad.status_code == 401
    assert sandbox.post("/v1/run", json={"code": "x"}).status_code == 401
    big = run(sandbox, "def run(args):\n    return 1", args={"blob": "x" * 150_000})
    assert big["status"] == "invalid"
    assert sandbox.get("/readyz").json()["status"] == "ok"


# ---- network through the egress proxy, and gVisor
class _Page(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"hello from the allowed host"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: Any) -> None:
        return


@pytest.fixture(scope="module")
def networked() -> Iterator[tuple[TestClient, int]]:
    """A runner whose executions may join an internal network and reach a local web page through
    the proxy. The runner itself runs here, on the host: the proxy answers on the network's
    gateway address."""
    name = f"forge-sbx-test-{os.getpid()}"
    subprocess.run(
        ["docker", "network", "create", "--internal", name], check=True, capture_output=True
    )
    try:
        gateway = subprocess.run(
            [
                "docker",
                "network",
                "inspect",
                name,
                "--format",
                "{{(index .IPAM.Config 0).Gateway}}",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Page)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cfg = Config()
        cfg.token, cfg.socket, cfg.image = TOKEN, SOCKET, IMAGE
        cfg.network, cfg.proxy_host, cfg.proxy_port = name, gateway, 18091
        cfg.proxy_ports = frozenset({port})
        with TestClient(create_app(cfg)) as client:
            yield client, port
        server.shutdown()
    finally:
        subprocess.run(["docker", "network", "rm", name], capture_output=True)


FETCH = (
    "import urllib.request\n"
    "def run(args):\n"
    "    with urllib.request.urlopen(args['url'], timeout=5) as r:\n"
    "        return {'status': r.status, 'body': r.read().decode()}\n"
)


def test_an_allowed_host_is_reachable_through_the_proxy(
    networked: tuple[TestClient, int],
) -> None:
    client, port = networked
    out = run(
        client,
        FETCH,
        args={"url": f"http://127.0.0.1:{port}/"},
        allowed_hosts=["127.0.0.1"],
        allow_private=True,
    )
    assert out["status"] == "ok", out
    assert out["result"] == {"status": 200, "body": "hello from the allowed host"}


def test_https_tunnels_use_connect_and_are_checked_the_same_way(
    networked: tuple[TestClient, int],
) -> None:
    client, port = networked
    code = (
        "import http.client, os, urllib.parse\n"
        "def run(args):\n"
        "    p = urllib.parse.urlsplit(os.environ['HTTP_PROXY'])\n"
        "    c = http.client.HTTPConnection(p.hostname, p.port, timeout=5)\n"
        "    import base64\n"
        "    auth = base64.b64encode(f'{p.username}:{p.password}'.encode()).decode()\n"
        "    c.set_tunnel(args['host'], args['port'], {'Proxy-Authorization': 'Basic ' + auth})\n"
        "    c.request('GET', '/')\n"
        "    r = c.getresponse()\n"
        "    return {'status': r.status, 'body': r.read().decode()}\n"
    )
    ok = run(
        client,
        code,
        args={"host": "127.0.0.1", "port": port},
        allowed_hosts=["127.0.0.1"],
        allow_private=True,
    )
    assert ok["status"] == "ok", ok
    assert ok["result"]["status"] == 200
    refused = run(
        client,
        code,
        args={"host": "example.org", "port": port},
        allowed_hosts=["127.0.0.1"],
        allow_private=True,
    )
    assert refused["status"] == "error"  # the proxy answered 403 to the CONNECT


def test_hosts_outside_the_list_and_private_addresses_are_refused(
    networked: tuple[TestClient, int],
) -> None:
    client, port = networked
    other = run(
        client,
        FETCH,
        args={"url": f"http://localhost:{port}/"},  # resolves to loopback, not in the list
        allowed_hosts=["127.0.0.1"],
        allow_private=True,
    )
    assert other["status"] == "error"
    assert "403" in other["error"]
    private = run(
        client, FETCH, args={"url": f"http://127.0.0.1:{port}/"}, allowed_hosts=["127.0.0.1"]
    )
    assert private["status"] == "error"
    assert "403" in private["error"]  # loopback is not a public address


def test_code_cannot_go_around_the_proxy(networked: tuple[TestClient, int]) -> None:
    client, _port = networked
    direct = (
        "import socket\n"
        "def run(args):\n"
        "    s = socket.socket()\n"
        "    s.settimeout(3)\n"
        "    s.connect(('1.1.1.1', 80))\n"
    )
    out = run(client, direct, allowed_hosts=["127.0.0.1"], allow_private=True)
    assert out["status"] == "error"
    # and without hosts there is no network device at all
    plain = run(
        client, "import os\ndef run(args):\n    return sorted(os.listdir('/sys/class/net'))"
    )
    assert plain["result"] == ["lo"]


def test_hosts_need_a_configured_network(sandbox: TestClient) -> None:
    out = run(sandbox, "def run(args):\n    return 1", allowed_hosts=["example.org"])
    assert out["status"] == "invalid"


@pytest.mark.skipif(
    "runsc"
    not in subprocess.run(
        ["docker", "info", "--format", "{{.Runtimes}}"], capture_output=True, text=True
    ).stdout,
    reason="gVisor (runsc) is not registered with the engine",
)
def test_executions_run_under_gvisor() -> None:
    cfg = Config()
    cfg.token, cfg.socket, cfg.image, cfg.runtime = TOKEN, SOCKET, IMAGE, "runsc"
    with TestClient(create_app(cfg)) as client:
        out = run(client, "import platform\ndef run(args):\n    return platform.release()")
    assert out["status"] == "ok", out
    assert "gvisor" in out["result"]
