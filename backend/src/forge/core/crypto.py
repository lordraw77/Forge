"""Envelope encryption and key derivation from FORGE_MASTER_KEY (ADR-006)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

BUILTIN_KEK_ID = "builtin:v1"


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

    def rewrap(self, sealed: Sealed) -> Sealed:
        """Re-wrap the DEK under the current KEK (KEK rotation, no re-encryption of data)."""
        dek = self._unwrap(sealed.kek_id, sealed.wrapped_dek)
        return Sealed(
            self._current, self._wrap(self._current, dek), sealed.nonce, sealed.ciphertext
        )

    def _wrap(self, kek_id: str, dek: bytes) -> bytes:
        nonce = os.urandom(12)
        return nonce + AESGCM(self._keks[kek_id]).encrypt(nonce, dek, kek_id.encode())

    def _unwrap(self, kek_id: str, wrapped: bytes) -> bytes:
        if kek_id not in self._keks:
            raise KeyError(f"KEK {kek_id!r} not available")
        return AESGCM(self._keks[kek_id]).decrypt(wrapped[:12], wrapped[12:], kek_id.encode())


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def sha256(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def constant_time_equals(a: bytes, b: bytes) -> bool:
    return hmac.compare_digest(a, b)
