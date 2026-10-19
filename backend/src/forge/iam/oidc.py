"""OpenID Connect client: discovery, Authorization Code with PKCE, ID token verification
(E2.1, US-2.1.3 and US-2.1.4). Outbound calls honour the platform proxy and extra CA."""

from __future__ import annotations

import base64
import hashlib
import secrets as pysecrets
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlencode, urlparse

import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet
from pydantic import BaseModel, Field, model_validator

from forge.iam.directory import ExternalIdentity

ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "ES256", "ES384", "EdDSA"]
TIMEOUT = httpx.Timeout(10.0)


class OidcError(Exception):
    """Discovery, token exchange or ID token verification failed."""


class OidcConfig(BaseModel):
    issuer: str = Field(min_length=8, max_length=300)
    client_id: str = Field(min_length=1, max_length=300)
    scopes: str = Field(default="openid email profile", max_length=300)
    username_claim: str = "preferred_username"
    email_claim: str = "email"
    name_claim: str = "name"
    groups_claim: str = "groups"
    button_label: str = Field(default="", max_length=60)
    allow_insecure: bool = False
    sync_interval_min: int = 0  # not used: OIDC has no directory to sync

    @model_validator(mode="after")
    def _transport(self) -> OidcConfig:
        scheme = urlparse(self.issuer).scheme
        if scheme not in ("http", "https"):
            raise ValueError("issuer must be an http(s) URL")
        if scheme == "http" and not self.allow_insecure:
            raise ValueError("an http:// issuer needs allow_insecure")
        if "openid" not in self.scopes.split():
            raise ValueError("scopes must include openid")
        return self


@dataclass
class Metadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


def new_pkce() -> tuple[str, str]:
    verifier = pysecrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def discover(cfg: OidcConfig) -> Metadata:
    url = cfg.issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            data = (await client.get(url)).raise_for_status().json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcError(f"discovery failed: {exc}") from exc
    if str(data.get("issuer", "")).rstrip("/") != cfg.issuer.rstrip("/"):
        raise OidcError("discovery document issuer does not match the configured issuer")
    try:
        return Metadata(
            data["issuer"],
            data["authorization_endpoint"],
            data["token_endpoint"],
            data["jwks_uri"],
        )
    except KeyError as exc:
        raise OidcError(f"discovery document lacks {exc}") from exc


async def fetch_jwks(meta: Metadata) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            keys: dict[str, Any] = (await client.get(meta.jwks_uri)).raise_for_status().json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcError(f"JWKS fetch failed: {exc}") from exc
    return keys


def authorize_url(
    meta: Metadata,
    cfg: OidcConfig,
    redirect_uri: str,
    state: str,
    nonce: str,
    challenge: str,
) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": cfg.client_id,
            "redirect_uri": redirect_uri,
            "scope": cfg.scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    sep = "&" if "?" in meta.authorization_endpoint else "?"
    return f"{meta.authorization_endpoint}{sep}{query}"


async def exchange_code(
    meta: Metadata,
    cfg: OidcConfig,
    client_secret: str,
    code: str,
    redirect_uri: str,
    verifier: str,
) -> str:
    """Return the raw ID token."""
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": cfg.client_id,
        "client_secret": client_secret,
        "code_verifier": verifier,
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(meta.token_endpoint, data=form)
        resp.raise_for_status()
        token = resp.json().get("id_token")
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcError(f"token exchange failed: {exc}") from exc
    if not isinstance(token, str):
        raise OidcError("token response has no id_token")
    return token


def verify_id_token(
    cfg: OidcConfig, meta: Metadata, jwks: dict[str, Any], id_token: str, nonce: str
) -> dict[str, Any]:
    """Signature (provider keys only), issuer, audience, expiry and nonce."""
    try:
        token = jwt.decode(id_token, KeySet.import_key_set(cast(Any, jwks)), algorithms=ALGORITHMS)
        registry = jwt.JWTClaimsRegistry(
            leeway=60,
            iss={"essential": True, "value": meta.issuer},
            aud={"essential": True, "value": cfg.client_id},
            exp={"essential": True},
            sub={"essential": True},
            nonce={"essential": True, "value": nonce},
        )
        registry.validate(token.claims)
    except (JoseError, ValueError, KeyError) as exc:
        raise OidcError(f"ID token rejected: {exc}") from exc
    claims: dict[str, Any] = token.claims
    return claims


def identity_from_claims(cfg: OidcConfig, claims: dict[str, Any]) -> ExternalIdentity:
    email = str(claims.get(cfg.email_claim) or "").lower()
    groups = claims.get(cfg.groups_claim) or []
    if isinstance(groups, str):
        groups = [groups]
    return ExternalIdentity(
        external_id=str(claims["sub"]),
        username=str(claims.get(cfg.username_claim) or email or claims["sub"]),
        email=email,
        full_name=str(claims.get(cfg.name_claim) or ""),
        groups=[str(g) for g in groups],
    )
