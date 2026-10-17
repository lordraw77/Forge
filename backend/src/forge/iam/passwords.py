"""Password hashing (Argon2id) and policy (US-1.2.2, US-1.2.4)."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # Argon2id, library defaults (t=3, m=64 MiB, p=4)
# Verified against when the user does not exist, so timing does not reveal accounts.
_DUMMY_HASH = _hasher.hash("forge-dummy-password-for-timing")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@lru_cache(maxsize=1)
def _common() -> frozenset[str]:
    text = resources.files("forge.iam").joinpath("data/common-passwords.txt").read_text()
    return frozenset(line.strip().lower() for line in text.splitlines() if line.strip())


def policy_violations(
    password: str,
    *,
    min_length: int,
    block_common: bool = True,
    username: str = "",
    email: str = "",
) -> list[str]:
    errors: list[str] = []
    if len(password) < min_length:
        errors.append(f"Password must be at least {min_length} characters")
    if len(password) > 256:
        errors.append("Password must be at most 256 characters")
    lowered = password.lower()
    if block_common and lowered in _common():
        errors.append("Password is too common")
    for ident in (username, email.split("@")[0] if email else ""):
        if ident and len(ident) >= 3 and ident.lower() in lowered:
            errors.append("Password must not contain the username or email")
            break
    return errors
