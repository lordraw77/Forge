"""Public interface of the iam module (ADR-001)."""

from forge.iam.auth import AuthService, Flags, IssuedSession, LoginOutcome, actor_for
from forge.iam.external import ExternalAuth
from forge.iam.models import IdentityProvider, User
from forge.iam.providers import IdentityProviders
from forge.iam.tokens import TokenCodec, TokenError

__all__ = [
    "AuthService",
    "ExternalAuth",
    "Flags",
    "IdentityProvider",
    "IdentityProviders",
    "IssuedSession",
    "LoginOutcome",
    "TokenCodec",
    "TokenError",
    "User",
    "actor_for",
]
