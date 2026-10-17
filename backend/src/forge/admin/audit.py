"""Append-only audit log (F7.6, US-1.1.3)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.admin.models import AuditEvent
from forge.core.context import Actor

_SECRET_HINTS = ("password", "secret", "token", "api_key", "hash", "totp", "recovery")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: "***" if any(h in k.lower() for h in _SECRET_HINTS) else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


async def record(
    session: AsyncSession,
    actor: Actor,
    action: str,
    target_type: str,
    target_id: str | uuid.UUID | None = None,
    diff: dict[str, Any] | None = None,
    project_id: uuid.UUID | None = None,
) -> None:
    session.add(
        AuditEvent(
            project_id=project_id,
            actor_id=actor.id,
            actor_label=actor.label,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            diff=redact(diff or {}),
            ip=actor.context.ip,
            user_agent=actor.context.user_agent,
        )
    )


async def query(
    session: AsyncSession,
    *,
    project_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    action: str | None = None,
    target_type: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 100,
) -> list[AuditEvent]:
    stmt = select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(min(limit, 500))
    if project_id is not None:
        stmt = stmt.where(AuditEvent.project_id == project_id)
    if actor_id is not None:
        stmt = stmt.where(AuditEvent.actor_id == actor_id)
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    if target_type:
        stmt = stmt.where(AuditEvent.target_type == target_type)
    if since:
        stmt = stmt.where(AuditEvent.created_at >= since)
    if until:
        stmt = stmt.where(AuditEvent.created_at < until)
    return list((await session.execute(stmt)).scalars())
