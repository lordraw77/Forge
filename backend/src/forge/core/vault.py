"""OpenBao (or HashiCorp Vault) Transit as a key-encryption key (ADR-006, E3.8).

Forge never asks the vault to hold its data: each secret still has its own random data key; the
vault only wraps and unwraps those data keys, so the key that protects them never leaves it.
The API is Vault's: `POST /v1/<mount>/encrypt/<key>` and `/decrypt/<key>`. Calls are synchronous
and short (`timeout_s`), and the envelope caches unwrapped data keys for a few minutes.
"""

from __future__ import annotations

import base64
import contextlib
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from forge.core.crypto import KekUnavailable

TOKEN_SKEW_S = 30.0


@dataclass(frozen=True)
class VaultConfig:
    url: str
    mount: str = "transit"
    key_name: str = "forge"
    namespace: str = ""
    auth_method: str = "token"  # token | approle
    token: str = ""  # token auth: the token; approle: unused
    role_id: str = ""
    secret_id: str = ""
    ca_pem: str = ""
    timeout_s: float = 3.0


class OpenBaoKek:
    """RemoteKek on a Transit key. `kek_id` is `openbao:<key name>`; the transit key's own versions
    are inside the ciphertext (`vault:v1:...`), so rotating the key in the vault needs no change
    here and old data keys keep decrypting."""

    def __init__(self, cfg: VaultConfig, *, http: httpx.Client | None = None) -> None:
        self.cfg = cfg
        self.kek_id = f"openbao:{cfg.key_name}"
        self._lock = threading.Lock()
        self._token = cfg.token if cfg.auth_method == "token" else ""
        self._token_until = float("inf") if cfg.auth_method == "token" else 0.0
        self._ca_file: str | None = None
        if http is not None:
            self._http = http
        else:
            verify: Any = True
            if cfg.ca_pem:
                import tempfile

                fd, self._ca_file = tempfile.mkstemp(suffix=".pem")
                with os.fdopen(fd, "w") as fh:
                    fh.write(cfg.ca_pem)
                verify = self._ca_file
            self._http = httpx.Client(
                base_url=cfg.url.rstrip("/"), timeout=cfg.timeout_s, verify=verify
            )

    def close(self) -> None:
        self._http.close()
        if self._ca_file:
            os.unlink(self._ca_file)

    # ---- authentication
    def _headers(self) -> dict[str, str]:
        with self._lock:
            if time.monotonic() >= self._token_until:
                self._login()
            headers = {"X-Vault-Token": self._token}
        if self.cfg.namespace:
            headers["X-Vault-Namespace"] = self.cfg.namespace
        return headers

    def _login(self) -> None:
        if self.cfg.auth_method != "approle":
            return
        try:
            r = self._http.post(
                "/v1/auth/approle/login",
                json={"role_id": self.cfg.role_id, "secret_id": self.cfg.secret_id},
                headers={"X-Vault-Namespace": self.cfg.namespace} if self.cfg.namespace else {},
            )
        except httpx.HTTPError as exc:
            raise KekUnavailable(f"vault unreachable: {type(exc).__name__}") from exc
        if r.status_code != 200:
            raise KekUnavailable(f"vault refused the AppRole login ({r.status_code})")
        auth = r.json()["auth"]
        self._token = auth["client_token"]
        ttl = float(auth.get("lease_duration") or 0)
        self._token_until = time.monotonic() + max(ttl - TOKEN_SKEW_S, 1.0) if ttl else float("inf")

    def _call(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"/v1/{self.cfg.mount.strip('/')}/{path}"
        for attempt in range(2):
            try:
                r = (
                    self._http.post(url, json=body, headers=self._headers())
                    if body is not None
                    else self._http.get(url, headers=self._headers())
                )
            except httpx.HTTPError as exc:
                raise KekUnavailable(f"vault unreachable: {type(exc).__name__}") from exc
            if r.status_code == 403 and self.cfg.auth_method == "approle" and attempt == 0:
                with self._lock:
                    self._token_until = 0.0  # the token was revoked: log in again once
                continue
            if r.status_code >= 400:
                errors = ""
                with contextlib.suppress(ValueError):
                    errors = "; ".join(r.json().get("errors") or [])
                raise KekUnavailable(f"vault answered {r.status_code}: {errors[:200]}")
            data: dict[str, Any] = r.json()["data"]
            return data
        raise KekUnavailable("vault refused the token")

    # ---- RemoteKek
    def wrap(self, dek: bytes) -> bytes:
        data = self._call(
            f"encrypt/{self.cfg.key_name}", {"plaintext": base64.b64encode(dek).decode()}
        )
        return str(data["ciphertext"]).encode()

    def unwrap(self, wrapped: bytes) -> bytes:
        data = self._call(f"decrypt/{self.cfg.key_name}", {"ciphertext": wrapped.decode()})
        return base64.b64decode(data["plaintext"])

    # ---- checks for the console
    def check(self) -> dict[str, Any]:
        """A full round trip with a random value, and the key's facts. Raises KekUnavailable."""
        probe = os.urandom(32)
        if self.unwrap(self.wrap(probe)) != probe:
            raise KekUnavailable("the vault returned a different value")
        info = self._call(f"keys/{self.cfg.key_name}")
        return {
            "key": self.cfg.key_name,
            "type": info.get("type"),
            "latest_version": info.get("latest_version"),
            "derived": bool(info.get("derived")),
        }
