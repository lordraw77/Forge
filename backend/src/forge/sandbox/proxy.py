"""The sandbox's egress proxy (E3.4): the only way out of a sandboxed execution.

A run that is allowed to reach some hosts is attached to an *internal* container network, which
has no route to anywhere but this service, and gets `HTTP_PROXY`/`HTTPS_PROXY` pointing here with
a one-run credential. The proxy then lets through only what that run was granted:

- the host must match the run's patterns (`api.example.com` or `*.example.com`);
- only ports 80 and 443;
- the name is resolved here and the connection goes to the address that was checked, so a name
  that resolves to a private, loopback or link-local address is refused (unless the deployment
  allows private hosts) and a DNS answer cannot change between check and connect;
- a cap on connections and bytes per run, and an idle timeout.

Standalone (standard library only): it ships in the runner's image next to `server.py`.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import ipaddress
import secrets
import socket
import time
from dataclasses import dataclass, field

ALLOWED_PORTS = frozenset({80, 443})
MAX_HEAD = 16 * 1024
MAX_CONNECTIONS = 16
MAX_BYTES = 20 * 1024 * 1024
IDLE_S = 30.0
CONNECT_S = 10.0


def host_allowed(host: str, patterns: list[str]) -> bool:
    host = host.lower().rstrip(".")
    for pattern in patterns:
        pattern = pattern.lower()
        if pattern.startswith("*."):
            if host.endswith(pattern[1:]) and host != pattern[2:]:
                return True
        elif host == pattern:
            return True
    return False


def public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


@dataclass
class Grant:
    hosts: list[str]
    allow_private: bool
    expires: float
    connections: int = 0
    bytes_left: int = MAX_BYTES
    log: list[str] = field(default_factory=list)


class Refused(Exception):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status, self.reason = status, reason


class EgressProxy:
    def __init__(self, ports: frozenset[int] = ALLOWED_PORTS) -> None:
        self._ports = ports
        self._grants: dict[str, Grant] = {}
        self._server: asyncio.Server | None = None

    # ------------------------------------------------------------------ grants
    def grant(self, hosts: list[str], allow_private: bool, ttl_s: float) -> str:
        """A credential for one execution; it dies with `revoke` or after `ttl_s`."""
        token = secrets.token_urlsafe(24)
        self._grants[token] = Grant(list(hosts), allow_private, time.monotonic() + ttl_s)
        return token

    def revoke(self, token: str) -> Grant | None:
        return self._grants.pop(token, None)

    def _lookup(self, header: str | None) -> Grant:
        if not header or not header.lower().startswith("basic "):
            raise Refused(407, "proxy authentication required")
        try:
            user, _, token = base64.b64decode(header[6:].strip()).decode().partition(":")
        except ValueError as exc:
            raise Refused(407, "bad credentials") from exc
        grant = self._grants.get(token) if user == "run" else None
        if grant is None or grant.expires < time.monotonic():
            raise Refused(407, "unknown or expired run")
        return grant

    # ------------------------------------------------------------------ server
    async def start(self, host: str, port: int) -> None:
        self._server = await asyncio.start_server(self._client, host, port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        grant: Grant | None = None
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), CONNECT_S)
            if len(head) > MAX_HEAD:
                raise Refused(431, "request head too large")
            first, *lines = head.decode("latin-1").split("\r\n")
            headers = {}
            for line in lines:
                if ":" in line:
                    name, _, value = line.partition(":")
                    headers[name.strip().lower()] = value.strip()
            grant = self._lookup(headers.get("proxy-authorization"))
            if grant.connections >= MAX_CONNECTIONS:
                raise Refused(429, "too many connections for this run")
            grant.connections += 1
            method, _, rest = first.partition(" ")
            target, _, _version = rest.rpartition(" ")
            if method.upper() == "CONNECT":
                host, _, port_s = target.rpartition(":")
                upstream = await self._open(grant, host.strip("[]"), int(port_s or 443))
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                await self._pipe(reader, writer, upstream, grant, b"")
            else:
                if not target.lower().startswith("http://"):
                    raise Refused(400, "only absolute http:// or CONNECT requests")
                rest_url = target[7:]
                authority, slash, path = rest_url.partition("/")
                host, _, port_s = authority.partition(":")
                upstream = await self._open(grant, host, int(port_s or 80))
                kept = [
                    ln
                    for ln in lines
                    if ln and not ln.lower().startswith(("proxy-", "connection:", "keep-alive:"))
                ]
                request = f"{method} {slash}{path} HTTP/1.1\r\n" + "\r\n".join(kept)
                request += "\r\nConnection: close\r\n\r\n"
                await self._pipe(reader, writer, upstream, grant, request.encode("latin-1"))
        except Refused as exc:
            grant_log = f"refused: {exc.reason}"
            if grant is not None:
                grant.log.append(grant_log)
            with contextlib.suppress(Exception):
                writer.write(
                    f"HTTP/1.1 {exc.status} {exc.reason}\r\nConnection: close\r\n"
                    f"Content-Length: 0\r\n\r\n".encode("latin-1")
                )
                await writer.drain()
        except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
            pass
        except OSError:
            pass
        finally:
            if grant is not None:
                grant.connections = max(0, grant.connections - 1)
            with contextlib.suppress(Exception):
                writer.close()

    async def _open(
        self, grant: Grant, host: str, port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if port not in self._ports:
            raise Refused(403, f"port {port} is not allowed")
        if not host_allowed(host, grant.hosts):
            grant.log.append(f"host {host} not in the allowed list")
            raise Refused(403, f"host {host} is not allowed")
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise Refused(502, f"cannot resolve {host}") from exc
        addresses = sorted({str(info[4][0]) for info in infos})
        if not addresses:
            raise Refused(502, f"cannot resolve {host}")
        if not grant.allow_private and not all(public_address(a) for a in addresses):
            grant.log.append(f"host {host} resolves to a non-public address")
            raise Refused(403, f"{host} resolves to a non-public address")
        try:
            return await asyncio.wait_for(asyncio.open_connection(addresses[0], port), CONNECT_S)
        except (TimeoutError, OSError) as exc:
            raise Refused(502, f"cannot connect to {host}") from exc

    async def _pipe(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        upstream: tuple[asyncio.StreamReader, asyncio.StreamWriter],
        grant: Grant,
        first: bytes,
    ) -> None:
        up_reader, up_writer = upstream

        async def copy(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            while True:
                data = await asyncio.wait_for(src.read(65536), IDLE_S)
                if not data:
                    return
                grant.bytes_left -= len(data)
                if grant.bytes_left < 0:
                    grant.log.append("byte limit reached")
                    return
                dst.write(data)
                await dst.drain()

        try:
            if first:
                up_writer.write(first)
                await up_writer.drain()
            tasks = [
                asyncio.ensure_future(copy(reader, up_writer)),
                asyncio.ensure_future(copy(up_reader, writer)),
            ]
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(BaseException):
                    await t
        finally:
            with contextlib.suppress(Exception):
                up_writer.close()
