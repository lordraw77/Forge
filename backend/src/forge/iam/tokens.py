"""Access JWTs, TOTP login challenges, refresh tokens and TOTP code checks."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

import jwt
import pyotp

from forge.core.crypto import random_token

ACCESS_TTL_S = 600
CHALLENGE_TTL_S = 300
TOTP_STEP_S = 30
ISSUER = "forge"


class TokenError(Exception):
    pass


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    session_id: uuid.UUID


class TokenCodec:
    def __init__(self, key: bytes) -> None:
        self._key = key

    def _encode(self, claims: dict[str, Any], ttl: int) -> str:
        now = int(time.time())
        return jwt.encode(
            {**claims, "iss": ISSUER, "iat": now, "exp": now + ttl}, self._key, "HS256"
        )

    def _decode(self, token: str, typ: str) -> dict[str, Any]:
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                self._key,
                algorithms=["HS256"],
                issuer=ISSUER,
                options={"require": ["exp", "iat"]},
            )
        except jwt.PyJWTError as exc:
            raise TokenError(str(exc)) from exc
        if claims.get("typ") != typ:
            raise TokenError("wrong token type")
        return claims

    def access(self, user_id: uuid.UUID, session_id: uuid.UUID) -> str:
        return self._encode(
            {"typ": "access", "sub": str(user_id), "sid": str(session_id)}, ACCESS_TTL_S
        )

    def read_access(self, token: str) -> AccessClaims:
        c = self._decode(token, "access")
        return AccessClaims(uuid.UUID(c["sub"]), uuid.UUID(c["sid"]))

    def challenge(self, user_id: uuid.UUID) -> str:
        return self._encode({"typ": "totp_challenge", "sub": str(user_id)}, CHALLENGE_TTL_S)

    def read_challenge(self, token: str) -> uuid.UUID:
        return uuid.UUID(self._decode(token, "totp_challenge")["sub"])


def new_refresh_token() -> str:
    return random_token(32)


def new_csrf_token() -> str:
    return random_token(16)


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, account: str, issuer: str = "Forge") -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=issuer)


def match_totp(secret: str, code: str, last_step: int, now: float | None = None) -> int | None:
    """Return the matched time step if `code` is valid (±1 step) and newer than `last_step`."""
    code = code.strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6):
        return None
    now = time.time() if now is None else now
    totp = pyotp.TOTP(secret)
    current = int(now // TOTP_STEP_S)
    for step in (current - 1, current, current + 1):
        if step > last_step and pyotp.utils.strings_equal(totp.at(step * TOTP_STEP_S), code):
            return step
    return None
