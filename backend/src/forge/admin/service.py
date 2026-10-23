"""Public interface of the admin module (ADR-001)."""

from forge.admin import audit, secrets
from forge.admin.settings_store import (
    DEDICATED,
    REGISTRY,
    Eula,
    GatewayLimits,
    LocalLogin,
    LockoutPolicy,
    PasswordPolicy,
    SessionPolicy,
    SettingsStore,
    SmtpSettings,
    TotpPolicy,
    UnknownSettingError,
    VaultSettings,
    listen_dsn,
    notify,
)
from forge.admin.vault import VaultLink

__all__ = [
    "DEDICATED",
    "REGISTRY",
    "Eula",
    "GatewayLimits",
    "LocalLogin",
    "LockoutPolicy",
    "PasswordPolicy",
    "SessionPolicy",
    "SettingsStore",
    "SmtpSettings",
    "TotpPolicy",
    "UnknownSettingError",
    "VaultLink",
    "VaultSettings",
    "audit",
    "listen_dsn",
    "notify",
    "secrets",
]
