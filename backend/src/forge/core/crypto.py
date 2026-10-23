"""Envelope encryption and key derivation from FORGE_MASTER_KEY (ADR-006)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Protocol

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

BUILTIN_KEK_ID = "builtin:v1"
UNWRAP_CACHE_SIZE = 2048
UNWRAP_CACHE_TTL_S = 300.0


class KekUnavailable(RuntimeError):
    """A key-encryption key held outside the process (a vault) cannot be reached right now."""


class RemoteKek(Protocol):
    """A KEK that never leaves its vault: it wraps and unwraps data keys on request."""

    kek_id: str

    def wrap(self, dek: bytes) -> bytes: ...

    def unwrap(self, wrapped: bytes) -> bytes: ...


def derive_key(master_key_b64: str, purpose: str, length: int = 32) -> bytes:
    """Independent sub-key per purpose (KEK, JWT signing, ...) via HKDF-SHA256."""
    master = base64.b64decode(master_key_b64)
    return HKDF(
        algorithm=hashes.SHA256(), length=length, salt=b"forge", info=purpose.encode()
    ).derive(master)


@dataclass(frozen=True)
class Sealed:
    kek_id: str
    wrapped_dek: bytes  # nonce(12) || AES-GCM(KEK, DEK)
    nonce: bytes
    ciphertext: bytes


class Envelope:
    """Per-secret random DEK encrypts the value; the KEK wraps the DEK."""

    def __init__(self, keks: dict[str, bytes], current: str = BUILTIN_KEK_ID) -> None:
        if current not in keks:
            raise ValueError(f"unknown current KEK {current!r}")
        self._keks = keks
        self._current = current
        self._remotes: dict[str, RemoteKek] = {}
        # Unwrapped data keys of remote KEKs, for a few minutes: a vault call per secret read
        # would make the vault a bottleneck and an outage of it an instant one.
        self._cache: OrderedDict[tuple[str, bytes], tuple[float, bytes]] = OrderedDict()
        self._cache_lock = threading.Lock()  # vault calls run in worker threads

    @property
    def current(self) -> str:
        return self._current

    def attach(self, remote: RemoteKek, *, make_current: bool = False) -> None:
        """Make a remote KEK available for reading; with `make_current`, for new secrets too."""
        self._remotes[remote.kek_id] = remote
        with self._cache_lock:
            self._cache.clear()
        if make_current:
            self._current = remote.kek_id

    def detach(self, kek_id: str) -> None:
        self._remotes.pop(kek_id, None)
        with self._cache_lock:
            self._cache.clear()
        if self._current == kek_id:
            self._current = BUILTIN_KEK_ID

    def use_builtin(self) -> None:
        self._current = BUILTIN_KEK_ID

    @classmethod
    def from_master_key(cls, master_key_b64: str) -> Envelope:
        return cls({BUILTIN_KEK_ID: derive_key(master_key_b64, "forge/kek/v1")})

    def seal(self, plaintext: bytes, aad: bytes) -> Sealed:
        dek = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        ciphertext = AESGCM(dek).encrypt(nonce, plaintext, aad)
        return Sealed(self._current, self._wrap(self._current, dek), nonce, ciphertext)

    def open(self, sealed: Sealed, aad: bytes) -> bytes:
        dek = self._unwrap(sealed.kek_id, sealed.wrapped_dek)
        return AESGCM(dek).decrypt(sealed.nonce, sealed.ciphertext, aad)

    def rewrap(self, sealed: Sealed, to: str | None = None) -> Sealed:
        """Re-wrap the DEK under the current KEK, or under `to` (KEK rotation, no re-encryption
        of data)."""
        target = to or self._current
        dek = self._unwrap(sealed.kek_id, sealed.wrapped_dek)
        return Sealed(target, self._wrap(target, dek), sealed.nonce, sealed.ciphertext)

    def _wrap(self, kek_id: str, dek: bytes) -> bytes:
        if kek_id in self._remotes:
            return self._remotes[kek_id].wrap(dek)
        nonce = os.urandom(12)
        return nonce + AESGCM(self._keks[kek_id]).encrypt(nonce, dek, kek_id.encode())

    def _unwrap(self, kek_id: str, wrapped: bytes) -> bytes:
        if kek_id in self._remotes:
            key = (kek_id, wrapped)
            with self._cache_lock:
                hit = self._cache.get(key)
                if hit and time.monotonic() - hit[0] < UNWRAP_CACHE_TTL_S:
                    self._cache.move_to_end(key)
                    return hit[1]
            dek = self._remotes[kek_id].unwrap(wrapped)
            with self._cache_lock:
                self._cache[key] = (time.monotonic(), dek)
                while len(self._cache) > UNWRAP_CACHE_SIZE:
                    self._cache.popitem(last=False)
            return dek
        if kek_id.startswith("openbao:"):
            raise KekUnavailable(f"KEK {kek_id!r} is not configured in this process")
        if kek_id not in self._keks:
            raise KeyError(f"KEK {kek_id!r} not available")
        return AESGCM(self._keks[kek_id]).decrypt(wrapped[:12], wrapped[12:], kek_id.encode())


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def sha256(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def constant_time_equals(a: bytes, b: bytes) -> bool:
    return hmac.compare_digest(a, b)
