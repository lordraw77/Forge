"""Encrypted secret store (ADR-006). Values never leave this module except to their consumer."""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.admin.models import Secret
from forge.core.crypto import BUILTIN_KEK_ID, Envelope, Sealed
from forge.core.ids import uuid7

VAULT_CREDENTIAL_KIND = "secrets.vault"


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
    # In a thread: with a vault KEK this is a network call that must not stall the event loop.
    sealed = await asyncio.to_thread(
        envelope.seal, value.encode(), _aad(secret_id, kind, project_id)
    )
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
    opened = await asyncio.to_thread(envelope.open, sealed, _aad(row.id, row.kind, row.project_id))
    return opened.decode()


async def delete(session: AsyncSession, secret_id: uuid.UUID) -> None:
    row = await session.get(Secret, secret_id)
    if row is not None:
        await session.delete(row)


REWRAP_PARALLELISM = 8  # vault calls in flight while re-wrapping


async def rotate_all(session: AsyncSession, envelope: Envelope) -> int:
    """Re-wrap every DEK under the current KEK (`forge secrets rotate-kek`). The vault's own
    credential always stays under the built-in KEK: the vault cannot be needed to open it."""
    rows = list((await session.execute(select(Secret))).scalars())
    gate = asyncio.Semaphore(REWRAP_PARALLELISM)

    async def one(row: Secret) -> None:
        async with gate:
            resealed = await asyncio.to_thread(
                envelope.rewrap,
                Sealed(row.kek_id, row.wrapped_dek, row.nonce, row.ciphertext),
                BUILTIN_KEK_ID if row.kind == VAULT_CREDENTIAL_KIND else None,
            )
        row.kek_id, row.wrapped_dek = resealed.kek_id, resealed.wrapped_dek

    await asyncio.gather(*(one(r) for r in rows))
    return len(rows)
