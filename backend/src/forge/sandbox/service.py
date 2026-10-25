"""Client of the sandbox runner service (E3.4): runs Python in a throw-away container.

`SandboxClient.run` never raises for what the code does (error, timeout, out of memory come back
in the result); it raises `SandboxUnavailable` when there is no service to ask.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

import httpx

from forge.core.settings import Settings

MAX_CODE = 40_000
MAX_ARGS_BYTES = 64_000


class SandboxUnavailable(RuntimeError):
    pass


@dataclass
class SandboxResult:
    status: str  # ok | error | timeout | oom | invalid
    result: Any = None
    error: str | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0


def within_policy(hosts: list[str], policy: list[str]) -> list[str]:
    """The hosts a piece of code asks for that the project's egress policy does not cover: a
    wildcard is covered only by the same wildcard, a name also by a wildcard that matches it."""
    from forge.core.egress import host_allowed

    uncovered: list[str] = []
    for h in hosts:
        ok = h in policy or (not h.startswith("*.") and host_allowed(h, policy))
        if not ok:
            uncovered.append(h)
    return uncovered


def check_code(code: str, entry: str = "run") -> str | None:
    """None when the code parses and defines `entry(args)`; else what is wrong. Nothing runs."""
    if len(code) > MAX_CODE:
        return f"The code is longer than {MAX_CODE} characters"
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"SyntaxError: {exc.msg} (line {exc.lineno})"
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == entry:
            if isinstance(node, ast.AsyncFunctionDef):
                return f"{entry}(args) must be a plain function, not async"
            if len(node.args.args) != 1:
                return f"{entry} takes exactly one argument: the dictionary of arguments"
            return None
    return f"Define a function {entry}(args) that returns the result"


class SandboxClient:
    def __init__(self, url: str, token: str, *, http: httpx.AsyncClient | None = None) -> None:
        self._url = url.rstrip("/")
        self._token = token
        self._http = http

    @classmethod
    def from_settings(cls, settings: Settings) -> SandboxClient | None:
        if not settings.sandbox_url or settings.sandbox_token is None:
            return None
        return cls(settings.sandbox_url, settings.sandbox_token.get_secret_value())

    async def run(
        self,
        code: str,
        args: dict[str, Any],
        *,
        timeout_s: int = 10,
        memory_mb: int = 128,
        entry: str = "run",
        allowed_hosts: list[str] | None = None,
        allow_private: bool = False,
    ) -> SandboxResult:
        body = {
            "code": code,
            "args": args,
            "entry": entry,
            "timeout_s": timeout_s,
            "memory_mb": memory_mb,
            "allowed_hosts": allowed_hosts or [],
            "allow_private": allow_private,
        }
        client = self._http or httpx.AsyncClient()
        try:
            r = await client.post(
                f"{self._url}/v1/run",
                json=body,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=timeout_s
                + 15,  # the service enforces timeout_s; this is the network margin
            )
        except httpx.HTTPError as exc:
            raise SandboxUnavailable(f"sandbox service unreachable: {type(exc).__name__}") from exc
        finally:
            if self._http is None:
                await client.aclose()
        if r.status_code == 401:
            raise SandboxUnavailable("sandbox service refused the token")
        if r.status_code != 200:
            raise SandboxUnavailable(f"sandbox service error {r.status_code}")
        return SandboxResult(**r.json())
