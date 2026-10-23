"""The sandbox runner service (image `forge-sandbox`): one HTTP endpoint that runs a piece of
Python in a throw-away container and returns what it produced. It is deliberately standalone
(standard library, FastAPI, httpx) because it ships in its own small image, and it is the only
component that holds the Docker socket.

Backend: the Docker Engine API. Isolation is the container: no network, read-only root, no
capabilities, no new privileges, memory/CPU/pids limits, a tmpfs for /tmp, a non-root user and a
wall-clock timeout that kills it. With SANDBOX_RUNTIME=runsc the same container runs under gVisor.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import json
import os
import struct
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

MARKER = "\x1eFORGE_RESULT\x1e"
LABEL = "forge.sandbox"
MAX_PAYLOAD = 120_000  # an environment variable holds at most 128 KiB
MAX_LOG = 256 * 1024


class RunIn(BaseModel):
    code: str = Field(min_length=1, max_length=40_000)
    args: dict[str, Any] = Field(default_factory=dict)
    entry: str = Field(default="run", pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,47}$")
    timeout_s: int = Field(default=10, ge=1, le=120)
    memory_mb: int = Field(default=128, ge=32, le=1024)


class RunOut(BaseModel):
    status: str  # ok | error | timeout | oom | invalid
    result: Any = None
    error: str | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0


class Config:
    def __init__(self) -> None:
        env = os.environ
        self.token = env.get("SANDBOX_TOKEN", "")
        self.socket = env.get("SANDBOX_DOCKER_SOCKET", "/var/run/docker.sock")
        self.image = env.get("SANDBOX_RUNTIME_IMAGE", "forge-sandbox-runtime:dev")
        self.runtime = env.get("SANDBOX_RUNTIME", "")  # "" = the engine's default, "runsc" = gVisor
        self.max_concurrency = int(env.get("SANDBOX_MAX_CONCURRENCY", "4"))
        self.max_memory_mb = int(env.get("SANDBOX_MAX_MEMORY_MB", "512"))
        self.cpus = float(env.get("SANDBOX_CPUS", "1"))


def split_logs(raw: bytes) -> tuple[str, str]:
    """Docker's multiplexed log stream: 8-byte headers (stream, 3 zero bytes, size) + payload."""
    out, err, i = bytearray(), bytearray(), 0
    while i + 8 <= len(raw):
        stream, size = raw[i], struct.unpack(">I", raw[i + 4 : i + 8])[0]
        chunk = raw[i + 8 : i + 8 + size]
        (err if stream == 2 else out).extend(chunk)
        i += 8 + size
    return out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def parse_result(stdout: str) -> dict[str, Any] | None:
    """The last marker line is the result; whatever precedes it is output of the code or noise."""
    for line in reversed(stdout.split("\n")):  # not splitlines(): \x1e is a line break there
        if line.startswith(MARKER):
            try:
                value = json.loads(line[len(MARKER) :])
            except ValueError:
                return None
            return value if isinstance(value, dict) else None
    return None


class Docker:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.http = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=cfg.socket),
            base_url="http://docker",
            timeout=httpx.Timeout(30.0, read=None),
        )

    async def close(self) -> None:
        await self.http.aclose()

    def spec(self, body: RunIn, payload: str) -> dict[str, Any]:
        memory = min(body.memory_mb, self.cfg.max_memory_mb) * 1024 * 1024
        host: dict[str, Any] = {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
            "PidsLimit": 64,
            "Memory": memory,
            "MemorySwap": memory,
            "NanoCpus": int(self.cfg.cpus * 1_000_000_000),
            "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=16m"},  # noqa: S108 - inside the container
            "LogConfig": {"Type": "json-file", "Config": {"max-size": "1m", "max-file": "1"}},
            "Ulimits": [{"Name": "nofile", "Soft": 256, "Hard": 256}],
        }
        if self.cfg.runtime:
            host["Runtime"] = self.cfg.runtime
        return {
            "Image": self.cfg.image,
            "Cmd": ["python", "-I", "/opt/forge/runner.py"],
            "Env": [f"FORGE_PAYLOAD={payload}"],
            "User": "65534:65534",
            "NetworkDisabled": True,
            "Labels": {LABEL: "1"},
            "HostConfig": host,
        }

    async def run(self, body: RunIn) -> RunOut:
        payload = base64.b64encode(
            json.dumps({"code": body.code, "args": body.args, "entry": body.entry}).encode()
        ).decode()
        if len(payload) > MAX_PAYLOAD:
            return RunOut(status="invalid", error="code and arguments are too large")
        started = time.monotonic()
        name = f"forge-sbx-{uuid.uuid4().hex[:12]}"
        r = await self.http.post(
            "/containers/create", params={"name": name}, json=self.spec(body, payload)
        )
        if r.status_code >= 300:
            return RunOut(status="error", error=f"sandbox backend: {r.text[:200]}")
        cid = r.json()["Id"]
        timed_out = False
        try:
            (await self.http.post(f"/containers/{cid}/start")).raise_for_status()
            try:
                await asyncio.wait_for(self.http.post(f"/containers/{cid}/wait"), body.timeout_s)
            except TimeoutError:
                timed_out = True
                await self.http.post(f"/containers/{cid}/kill")
            info = (await self.http.get(f"/containers/{cid}/json")).json()
            logs = await self.http.get(
                f"/containers/{cid}/logs", params={"stdout": 1, "stderr": 1, "tail": 2000}
            )
            stdout, stderr = split_logs(logs.content[-MAX_LOG * 4 :])
            oom = bool(info.get("State", {}).get("OOMKilled"))
        finally:
            await self.http.delete(f"/containers/{cid}", params={"force": "true", "v": "true"})
        ms = int((time.monotonic() - started) * 1000)
        if timed_out:
            return RunOut(
                status="timeout", error=f"timed out after {body.timeout_s} s", duration_ms=ms
            )
        if oom:
            return RunOut(
                status="oom", error=f"out of memory ({body.memory_mb} MB)", duration_ms=ms
            )
        parsed = parse_result(stdout)
        if parsed is None:
            return RunOut(
                status="error",
                error="the code ended without a result" + (f": {stderr[-300:]}" if stderr else ""),
                duration_ms=ms,
            )
        if not parsed.get("ok"):
            return RunOut(
                status="error",
                error=str(parsed.get("error", "error")),
                stdout=str(parsed.get("stdout", ""))[:MAX_LOG],
                stderr=str(parsed.get("stderr", ""))[:MAX_LOG],
                duration_ms=ms,
            )
        return RunOut(
            status="ok",
            result=parsed.get("result"),
            stdout=str(parsed.get("stdout", ""))[:MAX_LOG],
            stderr=str(parsed.get("stderr", ""))[:MAX_LOG],
            duration_ms=ms,
        )

    async def sweep(self) -> None:
        """Containers left by a crash of this service."""
        r = await self.http.get(
            "/containers/json", params={"all": "true", "filters": json.dumps({"label": [LABEL]})}
        )
        for c in r.json():
            await self.http.delete(f"/containers/{c['Id']}", params={"force": "true", "v": "true"})


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config()
    if not cfg.token:
        raise RuntimeError("SANDBOX_TOKEN is required")
    docker = Docker(cfg)
    gate = asyncio.Semaphore(cfg.max_concurrency)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        with contextlib.suppress(Exception):  # the engine may start after us; /readyz reports it
            await docker.sweep()
        yield
        await docker.close()

    app = FastAPI(
        title="Forge sandbox", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, Any]:
        try:
            r = await docker.http.get("/images/" + cfg.image + "/json")
        except Exception as exc:
            raise HTTPException(503, f"container engine unreachable: {type(exc).__name__}") from exc
        if r.status_code != 200:
            raise HTTPException(503, f"image {cfg.image} not found on the engine")
        return {"status": "ok", "image": cfg.image, "runtime": cfg.runtime or "default"}

    @app.post("/v1/run", response_model=RunOut)
    async def run(body: RunIn, authorization: str = Header(default="")) -> RunOut:
        expected = f"Bearer {cfg.token}"
        if not hmac.compare_digest(authorization.encode(), expected.encode()):
            raise HTTPException(401, "invalid token")
        async with gate:
            try:
                return await docker.run(body)
            except httpx.HTTPError as exc:
                raise HTTPException(502, f"sandbox backend error: {type(exc).__name__}") from exc

    return app
