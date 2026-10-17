"""Encrypted secret store (ADR-006). Values never leave this module except to their consumer."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.admin.models import Secret
from forge.core.crypto import Envelope, Sealed
from forge.core.ids import uuid7


def _aad(secret_id: uuid.UUID, kind: str, project_id: uuid.UUID | None) -> bytes:
    return f"{secret_id}|{kind}|{project_id or ''}".encode()


async def put(
    session: AsyncSession,
    envelope: Envelope,
    kind: str,
    value: str,
    project_id: uuid.UUID | None = None,
) -> uuid.UUID:
    secret_id = uuid7()
    sealed = envelope.seal(value.encode(), _aad(secret_id, kind, project_id))
    session.add(
        Secret(
            id=secret_id,
            project_id=project_id,
            kind=kind,
            kek_id=sealed.kek_id,
            wrapped_dek=sealed.wrapped_dek,
            nonce=sealed.nonce,
            ciphertext=sealed.ciphertext,
        )
    )
    await session.flush()
    return secret_id


async def get(session: AsyncSession, envelope: Envelope, secret_id: uuid.UUID) -> str:
    row = (await session.execute(select(Secret).where(Secret.id == secret_id))).scalar_one()
    sealed = Sealed(row.kek_id, row.wrapped_dek, row.nonce, row.ciphertext)
    return envelope.open(sealed, _aad(row.id, row.kind, row.project_id)).decode()


async def delete(session: AsyncSession, secret_id: uuid.UUID) -> None:
    row = await session.get(Secret, secret_id)
    if row is not None:
        await session.delete(row)


async def rotate_all(session: AsyncSession, envelope: Envelope) -> int:
    """Re-wrap every DEK under the current KEK (`forge secrets rotate-kek`)."""
    count = 0
    for row in (await session.execute(select(Secret))).scalars():
        resealed = envelope.rewrap(Sealed(row.kek_id, row.wrapped_dek, row.nonce, row.ciphertext))
        row.kek_id, row.wrapped_dek = resealed.kek_id, resealed.wrapped_dek
        count += 1
    return count
