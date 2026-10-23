"""External vault for the key-encryption key (Super Admin console → Secrets vault, E3.8)."""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from forge.admin.service import VaultLink, VaultSettings, audit, secrets
from forge.admin.vault import config_from, describe
from forge.api.deps import Principal, services, super_admin
from forge.core.crypto import BUILTIN_KEK_ID, KekUnavailable
from forge.core.db import admin_scope
from forge.core.errors import ProblemError
from forge.core.vault import OpenBaoKek

router = APIRouter(prefix="/api/v1/admin/vault", tags=["vault"])
Admin = Annotated[Principal, Depends(super_admin)]


class VaultIn(BaseModel):
    enabled: bool = False
    active: bool = False
    url: str = Field(default="", max_length=500)
    mount: str = Field(default="transit", max_length=100)
    key_name: str = Field(default="forge", max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    namespace: str = Field(default="", max_length=200)
    auth_method: Literal["token", "approle"] = "token"
    role_id: str = Field(default="", max_length=200)
    ca_pem: str = Field(default="", max_length=20000)
    # token (auth_method token) or secret id (approle). None keeps the stored one, "" removes it.
    credential: str | None = Field(default=None, max_length=2000)


class VaultStatus(BaseModel):
    config: dict[str, Any]
    current_kek: str
    secrets_by_kek: dict[str, int]
    attached: bool


class VaultCheck(BaseModel):
    ok: bool
    error: str | None = None
    detail: dict[str, Any] | None = None


class RewrapOut(BaseModel):
    rewrapped: int
    secrets_by_kek: dict[str, int]


def _link(request: Request) -> VaultLink:
    return services(request).vault


async def _status(request: Request) -> VaultStatus:
    svc = services(request)
    link = _link(request)
    return VaultStatus(
        config=describe(svc.store.get(VaultSettings)),
        current_kek=link.envelope.current,
        secrets_by_kek=await link.usage(),
        attached=link.attached,
    )


@router.get("", response_model=VaultStatus)
async def get_vault(_p: Admin, request: Request) -> VaultStatus:
    return await _status(request)


def _credential_json(cfg_in: VaultIn, secret: str) -> dict[str, str]:
    if not secret:
        return {}
    return {"token": secret} if cfg_in.auth_method == "token" else {"secret_id": secret}


@router.put("", response_model=VaultStatus)
async def put_vault(body: VaultIn, p: Admin, request: Request) -> VaultStatus:
    svc = services(request)
    link = _link(request)
    current = svc.store.get(VaultSettings)
    usage = await link.usage()
    on_vault = sum(n for k, n in usage.items() if k.startswith("openbao:"))
    if not body.enabled and on_vault:
        raise ProblemError(
            409,
            "secrets_use_vault",
            f"{on_vault} secret(s) are wrapped by the vault: re-wrap them to the built-in key "
            "first (set 'active' off, then re-wrap), then disable",
        )
    if on_vault and body.key_name != current.key_name:
        raise ProblemError(
            409, "secrets_use_vault", "Re-wrap the secrets to the built-in key before changing key"
        )
    # Credential: keep (None), replace or remove (""). Stored as JSON in the secret store, which
    # is always sealed by the built-in KEK: the vault's own credential cannot depend on the vault.
    secret_id = current.credential_secret_id
    new_secret = body.credential
    stored = await link.credential(current) if secret_id else {}
    effective = _credential_json(body, new_secret) if new_secret is not None else stored
    if body.enabled:
        if not body.url or not effective:
            raise ProblemError(422, "vault_incomplete", "A vault needs a URL and a credential")
        probe = VaultSettings(**{**body.model_dump(exclude={"credential"})})
        kek = OpenBaoKek(config_from(probe, effective))
        try:
            kek.check()
        except KekUnavailable as exc:
            raise ProblemError(
                422, "vault_check_failed", "The vault did not accept the settings", str(exc)
            ) from exc
        finally:
            kek.close()
    async with admin_scope(request.app.state.sessions, p.actor.id) as s:
        if new_secret is not None and secret_id:
            await secrets.delete(s, uuid.UUID(secret_id))
            secret_id = None
        if new_secret:
            secret_id = str(
                await secrets.put(s, svc.envelope, "secrets.vault", json.dumps(effective))
            )
        value = VaultSettings(
            enabled=body.enabled,
            active=body.active and body.enabled,
            url=body.url.strip(),
            mount=body.mount.strip() or "transit",
            key_name=body.key_name,
            namespace=body.namespace.strip(),
            auth_method=body.auth_method,
            role_id=body.role_id.strip(),
            ca_pem=body.ca_pem,
            credential_secret_id=secret_id,
        ).model_dump()
        await svc.store.set(s, p.actor, "secrets.vault", value)
        await audit.record(
            s,
            p.actor,
            "vault.update",
            "setting",
            "secrets.vault",
            {
                "enabled": body.enabled,
                "active": body.active,
                "url": body.url,
                "key": body.key_name,
                "credential": "changed" if new_secret is not None else "kept",
            },
        )
    await svc.store.reload("secrets.vault")
    await link.apply()
    return await _status(request)


@router.post("/test", response_model=VaultCheck)
async def check_vault(_p: Admin, request: Request) -> VaultCheck:
    svc = services(request)
    link = _link(request)
    cfg = svc.store.get(VaultSettings)
    if not (cfg.enabled and cfg.url):
        raise ProblemError(409, "vault_not_configured", "Save and enable a vault first")
    kek = OpenBaoKek(config_from(cfg, await link.credential(cfg)))
    try:
        return VaultCheck(ok=True, detail=kek.check())
    except KekUnavailable as exc:
        return VaultCheck(ok=False, error=str(exc))
    finally:
        kek.close()


@router.post("/rewrap", response_model=RewrapOut)
async def rewrap(p: Admin, request: Request) -> RewrapOut:
    """Re-wrap every secret's data key under the current KEK: the vault when it is active,
    the built-in key otherwise. The values are not re-encrypted."""
    link = _link(request)
    count = await link.rewrap_all()
    async with admin_scope(request.app.state.sessions, p.actor.id) as s:
        await audit.record(
            s,
            p.actor,
            "vault.rewrap",
            "setting",
            "secrets.vault",
            {"secrets": count, "kek": link.envelope.current or BUILTIN_KEK_ID},
        )
    return RewrapOut(rewrapped=count, secrets_by_kek=await link.usage())
