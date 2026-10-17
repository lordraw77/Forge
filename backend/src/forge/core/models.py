"""Declarative base shared by every module's ORM models."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Text
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    type_annotation_map = {  # noqa: RUF012 - SQLAlchemy reads this class attribute
        uuid.UUID: UUID(as_uuid=True),
        datetime: TIMESTAMP(timezone=True),
        list[str]: ARRAY(Text),
        dict[str, Any]: JSONB,
        bytes: BYTEA,
    }


def utcnow() -> datetime:
    return datetime.now(UTC)
