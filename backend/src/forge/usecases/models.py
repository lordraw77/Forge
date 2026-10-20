from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from forge.core.ids import uuid7
from forge.core.models import Base, utcnow


class Catalog(Base):
    __tablename__ = "catalog"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    name: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    status: Mapped[str] = mapped_column(default="active")
    builtin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]


class Category(Base):
    __tablename__ = "category"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    catalog_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("catalog.id"))
    name: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]


class CatalogAccess(Base):
    __tablename__ = "catalog_access"
    project_id: Mapped[uuid.UUID]
    role_name_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    catalog_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)


class CategoryExclusion(Base):
    __tablename__ = "category_exclusion"
    project_id: Mapped[uuid.UUID]
    role_name_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    category_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)


class UseCase(Base):
    __tablename__ = "use_case"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    name: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    icon: Mapped[str] = mapped_column(default="")
    tags: Mapped[list[str]] = mapped_column(default=list)
    catalog_id: Mapped[uuid.UUID | None]
    category_id: Mapped[uuid.UUID | None]
    pattern: Mapped[str]
    status: Mapped[str] = mapped_column(default="draft")
    input_fields: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    output_type: Mapped[str] = mapped_column(default="markdown")
    output_schema: Mapped[dict[str, Any] | None]
    defaults: Mapped[dict[str, Any]] = mapped_column(default=dict)
    definition: Mapped[dict[str, Any]] = mapped_column(default=dict)
    latest_version: Mapped[int] = mapped_column(default=0)
    published_version: Mapped[int | None]
    dirty: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[uuid.UUID | None]


class UseCaseVersion(Base):
    __tablename__ = "use_case_version"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    use_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("use_case.id"))
    version: Mapped[int]
    name: Mapped[str]
    description: Mapped[str]
    pattern: Mapped[str]
    input_fields: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    input_schema: Mapped[dict[str, Any]]
    output_type: Mapped[str]
    output_schema: Mapped[dict[str, Any] | None]
    defaults: Mapped[dict[str, Any]]
    definition: Mapped[dict[str, Any]]
    note: Mapped[str] = mapped_column(default="")
    published_at: Mapped[datetime] = mapped_column(default=utcnow)
    published_by: Mapped[uuid.UUID | None]
