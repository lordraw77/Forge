from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from forge.core.ids import uuid7
from forge.core.models import Base, utcnow


class McpExposure(Base):
    """One agent, prompt or data collection a project offers to MCP clients."""

    __tablename__ = "mcp_exposure"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    name: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent.id"))
    prompt_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("prompt.id"))
    collection_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("data_collection.id"))
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID | None]
