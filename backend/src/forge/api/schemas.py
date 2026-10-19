"""Request/response models of the M1.1 API. Inputs forbid unknown fields (threat model TB1)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- auth
class LoginIn(In):
    login: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class LoginTotpIn(In):
    challenge: str
    code: str | None = Field(default=None, max_length=10)
    recovery_code: str | None = Field(default=None, max_length=20)


class PasswordChangeIn(In):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class EulaOut(Out):
    version: str
    text: str


class EulaAcceptIn(In):
    version: str


class CodeIn(In):
    code: str = Field(min_length=6, max_length=10)


class TotpEnrollOut(Out):
    secret: str
    otpauth_uri: str


class RecoveryCodesOut(Out):
    recovery_codes: list[str]


class FlagsOut(Out):
    password_change_required: bool
    eula_required: bool
    totp_enrollment_required: bool


class UserOut(Out):
    id: uuid.UUID
    username: str
    email: str
    full_name: str
    auth_source: str
    is_super_admin: bool
    status: str
    totp_enabled: bool
    last_active_at: datetime | None
    locked_until: datetime | None = None
    created_at: datetime


class ProjectOut(Out):
    id: uuid.UUID
    name: str
    description: str
    status: str
    created_at: datetime


class MyProjectOut(ProjectOut):
    role_name: str | None
    role_type: str | None
    permissions: list[str]


class MeOut(Out):
    user: UserOut
    flags: FlagsOut
    projects: list[MyProjectOut]


class LoginOut(Out):
    status: Literal["ok", "totp_required"]
    challenge: str | None = None
    user: UserOut | None = None
    flags: FlagsOut | None = None


# ---- projects
class ProjectDetailOut(ProjectOut):
    permissions: list[str]
    role_name: str | None
    role_type: str | None


class MemberOut(Out):
    user_id: uuid.UUID
    username: str
    full_name: str
    email: str
    role_name_id: uuid.UUID
    role_name: str
    role_type: str
    last_active_at: datetime | None
    group_ids: list[uuid.UUID]


class InviteIn(In):
    identifiers: list[str] = Field(min_length=1, max_length=500)
    role_name_id: uuid.UUID


class InviteResultOut(Out):
    identifier: str
    status: str
    user_id: uuid.UUID | None


class MemberUpdateIn(In):
    role_name_id: uuid.UUID | None = None
    # The member's user groups, used by document ACLs (US-1.16.2).
    group_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)


class GroupIn(In):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)


class GroupOut(Out):
    id: uuid.UUID
    name: str
    description: str
    member_ids: list[uuid.UUID]
    created_at: datetime


class GroupMembersIn(In):
    user_ids: list[uuid.UUID] = Field(max_length=5000)


class AclPrincipal(Out):
    id: uuid.UUID
    name: str


class AclPrincipalsOut(Out):
    """Groups and roles a document ACL can name."""

    groups: list[AclPrincipal]
    roles: list[AclPrincipal]


class RoleOut(Out):
    id: uuid.UUID
    name: str
    role_type: str
    status: str


class RoleCreateIn(In):
    name: str = Field(min_length=2, max_length=60)
    role_type: Literal["project_admin", "builder", "operator", "end_user", "viewer"]


class RoleUpdateIn(In):
    name: str | None = Field(default=None, min_length=2, max_length=60)
    status: Literal["active", "inactive"] | None = None


class ApiKeyOut(Out):
    id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiKeyCreateIn(In):
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(min_length=1)
    expires_at: datetime | None = None


class ApiKeyCreatedOut(Out):
    key: ApiKeyOut
    token: str = Field(description="Shown once; store it securely")


# ---- admin
class UserCreateIn(In):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    email: EmailStr
    full_name: str = Field(default="", max_length=120)
    is_super_admin: bool = False
    password: str | None = Field(default=None, max_length=256)


class UserCreatedOut(Out):
    user: UserOut
    temporary_password: str | None = Field(default=None, description="Shown once")


class UserUpdateIn(In):
    full_name: str | None = Field(default=None, max_length=120)
    email: EmailStr | None = None
    status: Literal["active", "disabled"] | None = None
    is_super_admin: bool | None = None


class TemporaryPasswordOut(Out):
    temporary_password: str


class AdminProjectOut(ProjectOut):
    member_count: int | None
    delete_reason: str | None
    deleted_at: datetime | None


class ProjectCreateIn(In):
    name: str = Field(min_length=3, max_length=80)
    description: str = Field(default="", max_length=2000)
    admin_user_id: uuid.UUID


class ProjectCreatedOut(Out):
    project: ProjectOut
    api_key: str = Field(description="Project token, shown once")


class ProjectUpdateIn(In):
    name: str | None = Field(default=None, min_length=3, max_length=80)
    description: str | None = Field(default=None, max_length=2000)


class ProjectDeleteIn(In):
    reason: str = Field(min_length=5, max_length=1000)
    confirm_name: str


class AuditOut(Out):
    id: uuid.UUID
    project_id: uuid.UUID | None
    actor_id: uuid.UUID | None
    actor_label: str
    action: str
    target_type: str
    target_id: str | None
    diff: dict[str, Any]
    ip: str | None
    created_at: datetime


class SettingOut(Out):
    key: str
    value: dict[str, Any]
    version: int
