from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from forge.core.ids import uuid7
from forge.core.models import Base, utcnow


class ChatWidget(Base):
    __tablename__ = "chat_widget"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID]
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent.id"))
    name: Mapped[str]
    public_key: Mapped[str]
    title: Mapped[str] = mapped_column(default="")
    welcome: Mapped[str] = mapped_column(default="")
    allowed_origins: Mapped[list[str]] = mapped_column(default=list)
    enabled: Mapped[bool] = mapped_column(default=True)
    messages_per_minute: Mapped[int] = mapped_column(default=10)
    daily_limit: Mapped[int] = mapped_column(default=1000)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by: Mapped[uuid.UUID]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class WidgetSession(Base):
    __tablename__ = "widget_session"
    token_hash: Mapped[bytes] = mapped_column(primary_key=True)
    project_id: Mapped[uuid.UUID]
    widget_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_widget.id"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversation.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
