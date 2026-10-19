"""Exact tokenizers (PO decision 2026-10-19): a tokenizer per model in the registry, recorded
on knowledge bases for chunking.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-19
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

PATTERN = r"^(hf:[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*|tiktoken:[a-z0-9_]+)$"


def upgrade() -> None:
    op.execute(f"ALTER TABLE model ADD COLUMN tokenizer text CHECK (tokenizer ~ '{PATTERN}')")
    op.execute("ALTER TABLE knowledge_base ADD COLUMN tokenizer text")


def downgrade() -> None:
    op.execute("ALTER TABLE knowledge_base DROP COLUMN IF EXISTS tokenizer")
    op.execute("ALTER TABLE model DROP COLUMN IF EXISTS tokenizer")
