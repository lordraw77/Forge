"""Outbound HTTP for agent and library tools (threat model: SSRF).

Rules, applied before the first request and again on every redirect:

* only `http` and `https`, no credentials in the URL;
* the host must match the caller's allowlist (`example.com` exactly, or `*.example.com` for any
  subdomain); an empty list allows nothing;
* every address the host resolves to must be public, unless the deployment sets
  `FORGE_AGENT_ALLOW_PRIVATE_HOSTS` (labs). Private, loopback, link-local (cloud metadata),
  multicast, reserved and unspecified ranges are refused;
* without a corporate proxy the request connects to the address that was checked (no second DNS
  lookup, so rebinding cannot swap it); with a proxy the proxy resolves the name;
* at most 3 redirects (followed for GET and HEAD only; credentials are dropped when a redirect
  changes host), 10 s by default, 256 KB of body, text-like content types only.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from forge.core.network import proxy_configured

MAX_REDIRECTS = 3
TIMEOUT_S = 10.0
MAX_BODY_BYTES = 256 * 1024
TEXT_TYPES = ("text/", "application/json", "application/xml", "application/xhtml", "+json", "+xml")
TEXT_EXACT = {"application/javascript", "application/x-yaml", "application/yaml"}


class EgressError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Fetched:
    url: str
    status: int
    content_type: str
    body: str
    truncated: bool
    elapsed_ms: int


def host_allowed(host: str, patterns: list[str]) -> bool:
    host = host.lower().rstrip(".")
    for pattern in patterns:
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


async def resolve(host: str, port: int) -> list[str]:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise EgressError("dns_failed", f"Cannot resolve {host}") from exc
    addresses = sorted({str(info[4][0]) for info in infos})
    if not addresses:
        raise EgressError("dns_failed", f"Cannot resolve {host}")
    return addresses


def _text_like(content_type: str) -> bool:
    ct = content_type.split(";")[0].strip().lower()
    return ct in TEXT_EXACT or any(t in ct for t in TEXT_TYPES) or ct == ""


async def _check(url: str, allowed: list[str], allow_private: bool) -> tuple[str, int, list[str]]:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise EgressError("scheme_not_allowed", "Only http and https URLs are allowed")
    if parts.username or parts.password:
        raise EgressError("credentials_not_allowed", "Credentials in the URL are not allowed")
    host = (parts.hostname or "").lower()
    if not host:
        raise EgressError("invalid_url", "The URL has no host")
    if not host_allowed(host, allowed):
        raise EgressError("host_not_allowed", f"{host} is not in the allowed hosts")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    addresses = await resolve(host, port)
    if not allow_private and not all(public_address(a) for a in addresses):
        raise EgressError("address_not_allowed", f"{host} resolves to a non-public address")
    return host, port, addresses


SENSITIVE_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie"})


async def request(
    method: str,
    url: str,
    allowed_hosts: list[str],
    *,
    allow_private: bool = False,
    headers: dict[str, str] | None = None,
    json_body: object | None = None,
    timeout_s: float = TIMEOUT_S,
    max_bytes: int = MAX_BODY_BYTES,
    accept: str = "text/*, application/json;q=0.9, */*;q=0.1",
) -> Fetched:
    started = time.monotonic()
    proxied = proxy_configured()
    current = url
    method = method.upper()
    follow = method in ("GET", "HEAD")
    sent_headers = dict(headers or {})
    first_host = (urlsplit(url).hostname or "").lower()
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_s), follow_redirects=False, trust_env=proxied
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            host, port, addresses = await _check(current, allowed_hosts, allow_private)
            parts = urlsplit(current)
            request_url, extensions = current, {}
            out_headers = {
                k: v
                for k, v in sent_headers.items()
                if host == first_host or k.lower() not in SENSITIVE_HEADERS
            }
            if not proxied:
                pinned = f"[{addresses[0]}]" if ":" in addresses[0] else addresses[0]
                netloc = f"{pinned}:{port}"
                request_url = parts._replace(netloc=netloc).geturl()
                out_headers["Host"] = parts.netloc.rsplit("@", 1)[-1]
                if parts.scheme == "https":
                    extensions["sni_hostname"] = host
            out_headers.setdefault("User-Agent", "Forge-Agent/1")
            out_headers.setdefault("Accept", accept)
            try:
                async with client.stream(
                    method,
                    request_url,
                    headers=out_headers,
                    json=json_body,
                    extensions=extensions,
                    timeout=max(1.0, timeout_s - (time.monotonic() - started)),
                ) as response:
                    if (
                        follow
                        and response.is_redirect
                        and (location := response.headers.get("location"))
                    ):
                        current = urljoin(current, location)
                        continue
                    content_type = response.headers.get("content-type", "")
                    if not _text_like(content_type):
                        raise EgressError(
                            "content_type_not_allowed",
                            f"Content type '{content_type.split(';')[0]}' is not text",
                        )
                    chunks: list[bytes] = []
                    size = 0
                    truncated = False
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        chunks.append(chunk)
                        if size > max_bytes:
                            truncated = True
                            break
                        if time.monotonic() - started > timeout_s:
                            raise EgressError("timeout", "The request took too long")
                    raw = b"".join(chunks)[:max_bytes]
                    return Fetched(
                        url=current,
                        status=response.status_code,
                        content_type=content_type.split(";")[0].strip(),
                        body=raw.decode(response.encoding or "utf-8", errors="replace"),
                        truncated=truncated,
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                    )
            except httpx.TimeoutException as exc:
                raise EgressError("timeout", "The request took too long") from exc
            except httpx.HTTPError as exc:
                raise EgressError(
                    "request_failed", f"Request failed: {type(exc).__name__}"
                ) from exc
    raise EgressError("too_many_redirects", f"More than {MAX_REDIRECTS} redirects")


async def fetch(url: str, allowed_hosts: list[str], *, allow_private: bool = False) -> Fetched:
    return await request("GET", url, allowed_hosts, allow_private=allow_private)
