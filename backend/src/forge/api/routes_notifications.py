"""Outgoing mail settings and a test message (Super Admin console → Email)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field

from forge.admin.service import SmtpSettings, audit, secrets
from forge.api.deps import Principal, services, super_admin
from forge.core.db import admin_scope
from forge.core.errors import ProblemError

router = APIRouter(prefix="/api/v1/admin/notifications", tags=["notifications"])
Admin = Annotated[Principal, Depends(super_admin)]


class SmtpOut(BaseModel):
    host: str
    port: int
    username: str
    from_address: str
    starttls: bool
    has_password: bool
    source: str  # console | environment | none


class SmtpIn(BaseModel):
    host: str = Field(default="", max_length=255)
    port: int = Field(default=587, ge=1, le=65535)
    username: str = Field(default="", max_length=255)
    from_address: str = Field(default="forge@localhost", max_length=255)
    starttls: bool = True
    # None keeps the stored password, "" removes it.
    password: str | None = Field(default=None, max_length=1000)


class SmtpTestIn(BaseModel):
    to: EmailStr


class SmtpTestOut(BaseModel):
    ok: bool
    error: str | None = None


def _out(request: Request) -> SmtpOut:
    svc = services(request)
    mailer = svc.mailer
    cfg = svc.store.get(SmtpSettings)
    if cfg.host:
        return SmtpOut(
            host=cfg.host,
            port=cfg.port,
            username=cfg.username,
            from_address=cfg.from_address,
            starttls=cfg.starttls,
            has_password=cfg.password_secret_id is not None,
            source="console",
        )
    s = svc.settings
    return SmtpOut(
        host=s.smtp_host,
        port=s.smtp_port,
        username=s.smtp_username,
        from_address=s.smtp_from,
        starttls=s.smtp_starttls,
        has_password=s.smtp_password is not None,
        source=mailer.source(),
    )


@router.get("/smtp", response_model=SmtpOut)
async def get_smtp(_p: Admin, request: Request) -> SmtpOut:
    return _out(request)


@router.put("/smtp", response_model=SmtpOut)
async def put_smtp(body: SmtpIn, p: Admin, request: Request) -> SmtpOut:
    svc = services(request)
    current = svc.store.get(SmtpSettings)
    secret_id = current.password_secret_id
    async with admin_scope(request.app.state.sessions, p.actor.id) as s:
        if body.password is not None:
            if secret_id:
                await secrets.delete(s, uuid.UUID(secret_id))
                secret_id = None
            if body.password:
                secret_id = str(
                    await secrets.put(s, svc.envelope, "notifications.smtp", body.password)
                )
        if not body.host:
            if secret_id:
                await secrets.delete(s, uuid.UUID(secret_id))
            secret_id = None
        value = SmtpSettings(
            host=body.host.strip(),
            port=body.port,
            username=body.username,
            from_address=body.from_address,
            starttls=body.starttls,
            password_secret_id=secret_id,
        ).model_dump()
        await svc.store.set(s, p.actor, "notifications.smtp", value)
        await audit.record(
            s,
            p.actor,
            "smtp.update",
            "setting",
            "notifications.smtp",
            {"host": body.host, "password": "changed" if body.password is not None else "kept"},
        )
    await svc.store.reload("notifications.smtp")
    return _out(request)


@router.post("/smtp/test", response_model=SmtpTestOut)
async def send_test_mail(body: SmtpTestIn, _p: Admin, request: Request) -> SmtpTestOut:
    mailer = services(request).mailer
    if not mailer.enabled:
        raise ProblemError(409, "smtp_not_configured", "Save a mail server first")
    try:
        await mailer.send_checked(
            str(body.to), "Forge test message", "This is a test message from Forge."
        )
    except Exception as exc:
        return SmtpTestOut(ok=False, error=f"{type(exc).__name__}: {str(exc)[:200]}")
    return SmtpTestOut(ok=True)
