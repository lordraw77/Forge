"""LDAP / Active Directory client (E2.1, US-2.1.1 and US-2.1.2).

ldap3 is blocking: callers run these functions with `asyncio.to_thread`. Every filter value
that comes from a user or a directory entry is escaped (RFC 4515).
"""

from __future__ import annotations

import os
import ssl
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import certifi
from ldap3 import SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPBindError, LDAPException
from ldap3.utils.conv import escape_filter_chars
from pydantic import BaseModel, Field, model_validator

DEFAULT_USER_FILTER = "(|(uid={login})(sAMAccountName={login})(mail={login}))"
AD_ACCOUNT_DISABLED = 0x2


class DirectoryError(Exception):
    """The directory could not be reached or the service account was refused."""


class LdapConfig(BaseModel):
    url: str = Field(min_length=8, max_length=300)
    start_tls: bool = False
    allow_insecure: bool = False
    ca_pem: str = Field(default="", max_length=20000)
    bind_dn: str = Field(max_length=500)
    user_base_dn: str = Field(max_length=500)
    user_filter: str = Field(default=DEFAULT_USER_FILTER, max_length=500)
    username_attr: str = "uid"
    email_attr: str = "mail"
    name_attr: str = "cn"
    active_directory: bool = False  # also reads userAccountControl to honour disabled accounts
    id_attr: str = ""  # stable id (entryUUID, objectGUID); empty = the DN
    group_base_dn: str = Field(default="", max_length=500)
    group_filter: str = Field(default="(member={dn})", max_length=500)
    group_name_attr: str = "cn"
    member_of_attr: str = "memberOf"
    timeout_s: int = Field(default=10, ge=1, le=60)
    sync_interval_min: int = Field(default=60, ge=0, le=10080)

    @model_validator(mode="after")
    def _transport(self) -> LdapConfig:
        scheme = urlparse(self.url).scheme
        if scheme not in ("ldap", "ldaps"):
            raise ValueError("url must start with ldap:// or ldaps://")
        if scheme == "ldap" and not self.start_tls and not self.allow_insecure:
            raise ValueError("ldap:// without StartTLS needs allow_insecure")
        if "{login}" not in self.user_filter:
            raise ValueError("user_filter must contain {login}")
        return self


@dataclass
class ExternalIdentity:
    external_id: str
    username: str
    email: str
    full_name: str = ""
    groups: list[str] = field(default_factory=list)
    disabled: bool = False


def _server(cfg: LdapConfig) -> Server:
    parsed = urlparse(cfg.url)
    tls = Tls(
        validate=ssl.CERT_REQUIRED,
        ca_certs_data=cfg.ca_pem or None,
        ca_certs_file=None if cfg.ca_pem else os.environ.get("SSL_CERT_FILE") or certifi.where(),
    )
    return Server(
        parsed.hostname or "",
        port=parsed.port,
        use_ssl=parsed.scheme == "ldaps",
        tls=tls,
        connect_timeout=cfg.timeout_s,
    )


def _connect(cfg: LdapConfig, user: str, password: str) -> Connection:
    conn = Connection(
        _server(cfg),
        user=user,
        password=password,
        receive_timeout=cfg.timeout_s,
        raise_exceptions=True,
    )
    try:
        conn.open()
        if cfg.start_tls:
            conn.start_tls()
        conn.bind()
    except LDAPBindError:
        raise
    except LDAPException as exc:
        raise DirectoryError(str(exc)) from exc
    return conn


def _service(cfg: LdapConfig, bind_password: str) -> Connection:
    try:
        return _connect(cfg, cfg.bind_dn, bind_password)
    except LDAPBindError as exc:
        raise DirectoryError(f"service account refused: {exc}") from exc


def _first(value: Any) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, bytes):
        return value.hex()
    return str(value or "")


def _attributes(cfg: LdapConfig) -> list[str]:
    wanted = [cfg.username_attr, cfg.email_attr, cfg.name_attr]
    if cfg.active_directory:
        wanted.append("userAccountControl")
    if cfg.id_attr:
        wanted.append(cfg.id_attr)
    if not cfg.group_base_dn:
        wanted.append(cfg.member_of_attr)
    return list(dict.fromkeys(wanted))


def _identity(cfg: LdapConfig, conn: Connection, entry: Any) -> ExternalIdentity:
    attrs = entry["attributes"]
    dn = entry["dn"]
    uac = _first(attrs.get("userAccountControl"))
    disabled = bool(uac.isdigit() and int(uac) & AD_ACCOUNT_DISABLED)
    groups = _groups(cfg, conn, dn, attrs)
    return ExternalIdentity(
        external_id=_first(attrs.get(cfg.id_attr)) if cfg.id_attr else dn,
        username=_first(attrs.get(cfg.username_attr)) or dn,
        email=_first(attrs.get(cfg.email_attr)).lower(),
        full_name=_first(attrs.get(cfg.name_attr)),
        groups=groups,
        disabled=disabled,
    )


def _groups(cfg: LdapConfig, conn: Connection, dn: str, attrs: Any) -> list[str]:
    found: list[str] = []
    if cfg.group_base_dn:
        flt = cfg.group_filter.replace("{dn}", escape_filter_chars(dn))
        flt = flt.replace("{login}", escape_filter_chars(_first(attrs.get(cfg.username_attr))))
        conn.search(
            cfg.group_base_dn,
            flt,
            SUBTREE,
            attributes=[cfg.group_name_attr],
            size_limit=1000,
        )
        for entry in conn.response or []:
            if entry.get("type") != "searchResEntry":
                continue
            found.append(entry["dn"])
            name = _first(entry["attributes"].get(cfg.group_name_attr))
            if name:
                found.append(name)
    else:
        for value in attrs.get(cfg.member_of_attr) or []:
            found.append(str(value))
            first_rdn = str(value).split(",", 1)[0]
            if "=" in first_rdn:
                found.append(first_rdn.split("=", 1)[1])
    return list(dict.fromkeys(found))


def _search_user(cfg: LdapConfig, conn: Connection, login: str) -> list[Any]:
    flt = cfg.user_filter.replace("{login}", escape_filter_chars(login))
    conn.search(cfg.user_base_dn, flt, SUBTREE, attributes=_attributes(cfg), size_limit=2)
    return [e for e in conn.response or [] if e.get("type") == "searchResEntry"]


def lookup(cfg: LdapConfig, bind_password: str, login: str) -> ExternalIdentity | None:
    conn = _service(cfg, bind_password)
    try:
        entries = _search_user(cfg, conn, login)
        if len(entries) != 1:  # none, or ambiguous: never guess
            return None
        return _identity(cfg, conn, entries[0])
    except LDAPException as exc:
        raise DirectoryError(str(exc)) from exc
    finally:
        conn.unbind()


def authenticate(
    cfg: LdapConfig, bind_password: str, login: str, password: str
) -> ExternalIdentity | None:
    """Service-account search, then a bind as the user; None = unknown user or bad password."""
    if not password or not login.strip():  # an empty password would be an anonymous bind
        return None
    conn = _service(cfg, bind_password)
    try:
        entries = _search_user(cfg, conn, login)
        if len(entries) != 1:
            return None
        identity = _identity(cfg, conn, entries[0])
        dn = entries[0]["dn"]
    except LDAPException as exc:
        raise DirectoryError(str(exc)) from exc
    finally:
        conn.unbind()
    if identity.disabled:
        return None
    try:
        _connect(cfg, dn, password).unbind()
    except LDAPBindError:
        return None
    return identity


def all_users(cfg: LdapConfig, bind_password: str) -> list[ExternalIdentity]:
    """Every user matching the filter with a wildcard login (for sync, US-2.1.6)."""
    conn = _service(cfg, bind_password)
    try:
        flt = cfg.user_filter.replace("{login}", "*")
        conn.search(cfg.user_base_dn, flt, SUBTREE, attributes=_attributes(cfg), paged_size=500)
        entries = [e for e in conn.response or [] if e.get("type") == "searchResEntry"]
        return [_identity(cfg, conn, e) for e in entries]
    except LDAPException as exc:
        raise DirectoryError(str(exc)) from exc
    finally:
        conn.unbind()


def check_connection(cfg: LdapConfig, bind_password: str) -> str:
    conn = _service(cfg, bind_password)
    try:
        conn.search(cfg.user_base_dn, "(objectClass=*)", SUBTREE, size_limit=1)
        return f"bound as {cfg.bind_dn}; base DN reachable"
    except LDAPException as exc:
        raise DirectoryError(str(exc)) from exc
    finally:
        conn.unbind()
