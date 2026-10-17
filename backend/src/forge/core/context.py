"""Who is acting and from where; passed to services for audit and authorization."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RequestContext:
    ip: str | None = None
    user_agent: str | None = None


@dataclass(frozen=True)
class Actor:
    """The authenticated principal as services see it."""

    id: uuid.UUID | None
    label: str
    is_super_admin: bool = False
    # Set for API keys: the key's project and scopes (ADR-003).
    api_key_project: uuid.UUID | None = None
    scopes: frozenset[str] = field(default_factory=frozenset)
    context: RequestContext = field(default_factory=RequestContext)


SYSTEM = Actor(id=None, label="system")
