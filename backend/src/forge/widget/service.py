"""The embeddable chat widget (E3.5): configuration by Project Admins, and the public side that
a visitor's browser talks to.

A visitor has no account. Each widget is bound to one published agent and runs as the Project
Admin who created it; a visitor's conversation is tied to a random session token kept in their
browser. What protects the agent is the list of allowed origins (checked for browsers), a rate
limit per visitor address and a daily cap per widget.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta

import redis.asyncio as redis
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import audit
from forge.agents.models import Agent, AgentTag
from forge.agents.schemas import ConversationIn
from forge.agents.studio import AgentStudio
from forge.core.context import Actor
from forge.core.crypto import random_token, sha256
from forge.core.db import admin_scope, project_scope
from forge.core.errors import ProblemError
from forge.core.models import utcnow
from forge.widget.models import ChatWidget, WidgetSession

ORIGIN = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?$")
SESSION_TTL = timedelta(hours=24)
MAX_MESSAGE_CHARS = 2000
KEY_PREFIX = "fw_"
UNKNOWN = ProblemError(404, "not_found", "Unknown widget")
TERMINAL = ("succeeded", "failed", "cancelled")


def clean_origins(origins: list[str]) -> list[str]:
    out: list[str] = []
    for raw in origins:
        origin = raw.strip().rstrip("/")
        if origin != "*" and not ORIGIN.match(origin):
            raise ValueError(f"{raw!r} is not an origin like https://intranet.example.com")
        if origin.lower() not in out:
            out.append(origin.lower())
    return out


class WidgetIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    agent_id: uuid.UUID
    title: str = Field(default="", max_length=80)
    welcome: str = Field(default="", max_length=500)
    allowed_origins: list[str] = Field(default_factory=list, max_length=20)
    enabled: bool = True
    messages_per_minute: int = Field(default=10, ge=1, le=600)
    daily_limit: int = Field(default=1000, ge=1, le=1_000_000)

    @field_validator("allowed_origins")
    @classmethod
    def _origins(cls, v: list[str]) -> list[str]:
        return clean_origins(v)


class WidgetUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=80)
    agent_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=80)
    welcome: str | None = Field(default=None, max_length=500)
    allowed_origins: list[str] | None = Field(default=None, max_length=20)
    enabled: bool | None = None
    messages_per_minute: int | None = Field(default=None, ge=1, le=600)
    daily_limit: int | None = Field(default=None, ge=1, le=1_000_000)

    @field_validator("allowed_origins")
    @classmethod
    def _origins(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else clean_origins(v)


class WidgetOut(BaseModel):
    id: uuid.UUID
    name: str
    agent_id: uuid.UUID
    agent_name: str
    public_key: str
    title: str
    welcome: str
    allowed_origins: list[str]
    enabled: bool
    messages_per_minute: int
    daily_limit: int
    created_at: datetime


class WidgetPublicConfig(BaseModel):
    title: str
    welcome: str


class WidgetSessionOut(BaseModel):
    session: str


class WidgetMessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session: str = Field(min_length=10, max_length=100)
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class WidgetMessageOut(BaseModel):
    run_id: uuid.UUID


class WidgetAnswerOut(BaseModel):
    status: str
    output: str | None = None
    error: str | None = None


class Widgets:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        agents: AgentStudio,
        cache: redis.Redis,
    ) -> None:
        self._sessions = sessions
        self._agents = agents
        self._redis = cache

    # ------------------------------------------------------------------ administration
    @staticmethod
    async def _out(s: AsyncSession, w: ChatWidget) -> WidgetOut:
        agent = await s.get(Agent, w.agent_id)
        return WidgetOut(
            id=w.id,
            name=w.name,
            agent_id=w.agent_id,
            agent_name=agent.name if agent else "",
            public_key=w.public_key,
            title=w.title,
            welcome=w.welcome,
            allowed_origins=list(w.allowed_origins),
            enabled=w.enabled,
            messages_per_minute=w.messages_per_minute,
            daily_limit=w.daily_limit,
            created_at=w.created_at,
        )

    async def list(self, actor: Actor, project_id: uuid.UUID) -> list[WidgetOut]:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            rows = (await s.execute(select(ChatWidget).order_by(ChatWidget.name))).scalars().all()
            return [await self._out(s, w) for w in rows]

    async def create(self, actor: Actor, project_id: uuid.UUID, body: WidgetIn) -> WidgetOut:
        assert actor.id is not None
        async with project_scope(self._sessions, project_id, actor.id) as s:
            if await s.get(Agent, body.agent_id) is None:
                raise ProblemError(422, "unknown_agent", "Agent not found")
            w = ChatWidget(
                project_id=project_id,
                agent_id=body.agent_id,
                name=body.name,
                public_key=KEY_PREFIX + random_token(18),
                title=body.title,
                welcome=body.welcome,
                allowed_origins=body.allowed_origins,
                enabled=body.enabled,
                messages_per_minute=body.messages_per_minute,
                daily_limit=body.daily_limit,
                created_by=actor.id,
            )
            s.add(w)
            try:
                await s.flush()
            except IntegrityError as exc:
                raise ProblemError(409, "name_taken", "A widget with this name exists") from exc
            await audit.record(
                s,
                actor,
                "widget.create",
                "chat_widget",
                w.id,
                {"name": w.name, "agent": str(w.agent_id), "origins": w.allowed_origins},
                project_id=project_id,
            )
            return await self._out(s, w)

    async def update(
        self, actor: Actor, project_id: uuid.UUID, widget_id: uuid.UUID, body: WidgetUpdateIn
    ) -> WidgetOut:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            w = await s.get(ChatWidget, widget_id)
            if w is None:
                raise UNKNOWN
            changes = body.model_dump(exclude_none=True)
            if "agent_id" in changes and await s.get(Agent, changes["agent_id"]) is None:
                raise ProblemError(422, "unknown_agent", "Agent not found")
            for field, value in changes.items():
                setattr(w, field, value)
            try:
                await s.flush()
            except IntegrityError as exc:
                raise ProblemError(409, "name_taken", "A widget with this name exists") from exc
            await audit.record(
                s,
                actor,
                "widget.update",
                "chat_widget",
                w.id,
                {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in changes.items()},
                project_id=project_id,
            )
            return await self._out(s, w)

    async def delete(self, actor: Actor, project_id: uuid.UUID, widget_id: uuid.UUID) -> None:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            w = await s.get(ChatWidget, widget_id)
            if w is None:
                raise UNKNOWN
            await audit.record(
                s,
                actor,
                "widget.delete",
                "chat_widget",
                w.id,
                {"name": w.name},
                project_id=project_id,
            )
            await s.execute(delete(WidgetSession).where(WidgetSession.widget_id == w.id))
            await s.delete(w)

    # ------------------------------------------------------------------ public side
    async def find(self, key: str) -> ChatWidget:
        """The widget behind a public key, enabled ones only."""
        async with admin_scope(self._sessions) as s:
            w = (
                await s.execute(select(ChatWidget).where(ChatWidget.public_key == key))
            ).scalar_one_or_none()
            if w is None or not w.enabled:
                raise UNKNOWN
            s.expunge(w)
            return w

    @staticmethod
    def origin_allowed(w: ChatWidget, origin: str | None) -> bool:
        """Browsers always send `Origin` on these calls; other clients may omit it."""
        if origin is None:
            return True
        return "*" in w.allowed_origins or origin.lower().rstrip("/") in w.allowed_origins

    @staticmethod
    def _actor(w: ChatWidget) -> Actor:
        return Actor(id=w.created_by, label=f"widget:{w.id}")

    async def _limit(self, w: ChatWidget, visitor: str) -> None:
        minute = f"widget:rl:{w.id}:{visitor}"
        day = f"widget:day:{w.id}:{utcnow():%Y%m%d}"
        used = await self._redis.incr(minute)
        if used == 1:
            await self._redis.expire(minute, 60)
        if used > w.messages_per_minute:
            raise ProblemError(429, "rate_limited", "Too many messages; wait a moment")
        total = await self._redis.incr(day)
        if total == 1:
            await self._redis.expire(day, 86_400)
        if total > w.daily_limit:
            raise ProblemError(429, "daily_limit", "This assistant has reached its daily limit")

    async def open_session(self, w: ChatWidget) -> WidgetSessionOut:
        actor = self._actor(w)
        conversation = await self._agents.create_conversation(
            actor,
            w.project_id,
            ConversationIn(agent_id=w.agent_id, title=f"[widget] {w.name}"[:200]),
            is_builder=False,
            source="widget",
        )
        token = random_token(24)
        async with project_scope(self._sessions, w.project_id, actor.id) as s:
            s.add(
                WidgetSession(
                    token_hash=sha256(token),
                    project_id=w.project_id,
                    widget_id=w.id,
                    conversation_id=conversation.id,
                )
            )
        return WidgetSessionOut(session=token)

    async def _session(self, w: ChatWidget, token: str) -> WidgetSession:
        async with project_scope(self._sessions, w.project_id, w.created_by) as s:
            row = (
                await s.execute(
                    select(WidgetSession).where(
                        WidgetSession.token_hash == sha256(token),
                        WidgetSession.widget_id == w.id,
                    )
                )
            ).scalar_one_or_none()
            if row is None or utcnow() - row.last_seen_at > SESSION_TTL:
                raise ProblemError(401, "session_expired", "The conversation has expired")
            row.last_seen_at = utcnow()
            s.expunge(row)
            return row

    async def send(self, w: ChatWidget, visitor: str, body: WidgetMessageIn) -> WidgetMessageOut:
        session = await self._session(w, body.session)
        await self._limit(w, visitor)
        run = await self._agents.send_message(
            self._actor(w), w.project_id, session.conversation_id, body.content
        )
        return WidgetMessageOut(run_id=run.id)

    async def answer(self, w: ChatWidget, session_token: str, run_id: uuid.UUID) -> WidgetAnswerOut:
        session = await self._session(w, session_token)
        run = await self._agents.get_run(self._actor(w), w.project_id, run_id, can_see_all=True)
        if run.conversation_id != session.conversation_id:
            raise UNKNOWN
        if run.status not in TERMINAL:
            return WidgetAnswerOut(status="running")
        if run.status == "succeeded":
            return WidgetAnswerOut(status="done", output=run.output or "")
        return WidgetAnswerOut(
            status="failed", error="The assistant could not answer right now. Please try again."
        )

    async def published(self, w: ChatWidget) -> bool:
        async with project_scope(self._sessions, w.project_id, w.created_by) as s:
            return (
                await s.execute(
                    select(AgentTag.version).where(
                        AgentTag.agent_id == w.agent_id, AgentTag.tag == "prod"
                    )
                )
            ).first() is not None
