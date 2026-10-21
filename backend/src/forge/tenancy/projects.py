"""Projects, role names, memberships, permissions and API keys (E1.1, ADR-002/003)."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, func, select, text, true, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import audit
from forge.core.context import Actor
from forge.core.crypto import constant_time_equals, random_token, sha256
from forge.core.db import admin_scope, project_scope
from forge.core.errors import ProblemError
from forge.core.models import utcnow
from forge.tenancy.models import (
    ApiKey,
    Membership,
    Project,
    RoleName,
    RoleType,
    UserGroup,
    UserGroupMember,
)

DEFAULT_ROLE_NAMES = {
    "project_admin": "Project Admin",
    "builder": "Builder",
    "operator": "Operator",
    "end_user": "User",
    "viewer": "Viewer",
}
DEFAULT_KEY_SCOPES = ["llm.invoke", "usecase.run", "run.read"]
KEY_PREFIX = "fk"
TOUCH_EVERY_S = 60.0
MAX_INVITES = 500

NOT_FOUND = ProblemError(404, "not_found", "Project not found")


@dataclass(frozen=True)
class ProjectAccess:
    project: Project
    permissions: frozenset[str]
    role_name: str | None
    role_type: str | None


@dataclass(frozen=True)
class ApiKeyPrincipal:
    key_id: uuid.UUID
    project_id: uuid.UUID
    scopes: frozenset[str]
    created_by: uuid.UUID | None


@dataclass(frozen=True)
class Approver:
    user_id: uuid.UUID
    email: str
    full_name: str
    role_name: str


@dataclass
class InviteResult:
    identifier: str
    status: str  # added | already_member | unknown_user
    user_id: uuid.UUID | None = None


@dataclass
class MemberRow:
    user_id: uuid.UUID
    username: str
    full_name: str
    email: str
    role_name_id: uuid.UUID
    role_name: str
    role_type: str
    last_active_at: datetime | None
    group_ids: list[uuid.UUID]


@dataclass
class GroupRow:
    id: uuid.UUID
    name: str
    description: str
    member_ids: list[uuid.UUID]
    created_at: datetime


@dataclass
class AclPrincipals:
    """Groups and roles of a project, for document ACLs (id -> name)."""

    groups: dict[uuid.UUID, str]
    roles: dict[uuid.UUID, str]


@dataclass
class ProjectSummary:
    project: Project
    role_name: str | None = None
    role_type: str | None = None
    permissions: list[str] = field(default_factory=list)
    member_count: int | None = None


class TenancyService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._touched: dict[uuid.UUID, float] = {}

    # ---- role types
    async def role_types(self, s: AsyncSession) -> dict[str, RoleType]:
        return {rt.key: rt for rt in (await s.execute(select(RoleType))).scalars()}

    # ---- projects (Super Admin console: forge_admin path)
    async def create_project(
        self, actor: Actor, name: str, description: str, admin_user_id: uuid.UUID
    ) -> tuple[Project, str]:
        async with admin_scope(self._sessions, actor.id) as s:
            await self._ensure_name_free(s, name)
            if (
                await s.execute(text("SELECT 1 FROM app_user WHERE id = :u"), {"u": admin_user_id})
            ).first() is None:
                raise ProblemError(422, "unknown_user", "Project Admin user does not exist")
            project = Project(name=name, description=description, created_by=actor.id)
            s.add(project)
            await s.flush()
            types = await self.role_types(s)
            names: dict[str, RoleName] = {}
            for key, label in DEFAULT_ROLE_NAMES.items():
                names[key] = RoleName(
                    project_id=project.id,
                    name=label,
                    role_type_id=types[key].id,
                    created_by=actor.id,
                )
                s.add(names[key])
            await s.flush()
            s.add(
                Membership(
                    project_id=project.id,
                    user_id=admin_user_id,
                    role_name_id=names["project_admin"].id,
                    created_by=actor.id,
                )
            )
            # Owned by the Project Admin: key permissions = scopes ∩ owner's permissions (ADR-003).
            token, api_key = self._new_key(
                project.id, "Project token", DEFAULT_KEY_SCOPES, None, admin_user_id
            )
            s.add(api_key)
            await audit.record(
                s,
                actor,
                "project.create",
                "project",
                project.id,
                {"name": name, "admin_user_id": str(admin_user_id)},
                project_id=project.id,
            )
            return project, token

    async def _ensure_name_free(
        self, s: AsyncSession, name: str, exclude: uuid.UUID | None = None
    ) -> None:
        if not 3 <= len(name.strip()) <= 80:
            raise ProblemError(422, "invalid_name", "Project name must be 3 to 80 characters")
        stmt = select(Project.id).where(
            func.lower(Project.name) == name.strip().lower(), Project.status != "deleted"
        )
        if exclude:
            stmt = stmt.where(Project.id != exclude)
        if (await s.execute(stmt)).first():
            raise ProblemError(409, "project_exists", "A project with this name already exists")

    async def list_all_projects(self, include_deleted: bool = False) -> list[ProjectSummary]:
        async with admin_scope(self._sessions) as s:
            counts = (
                select(Membership.project_id, func.count().label("n"))
                .group_by(Membership.project_id)
                .subquery()
            )
            stmt = (
                select(Project, func.coalesce(counts.c.n, 0))
                .outerjoin(counts, counts.c.project_id == Project.id)
                .order_by(func.lower(Project.name))
            )
            if not include_deleted:
                stmt = stmt.where(Project.status != "deleted")
            return [
                ProjectSummary(project=p, member_count=n) for p, n in (await s.execute(stmt)).all()
            ]

    async def update_project(
        self, actor: Actor, project_id: uuid.UUID, name: str | None, description: str | None
    ) -> Project:
        async with admin_scope(self._sessions, actor.id) as s:
            project = await self._get_live(s, project_id)
            before = {"name": project.name, "description": project.description}
            if name is not None and name != project.name:
                await self._ensure_name_free(s, name, exclude=project.id)
                project.name = name.strip()
            if description is not None:
                project.description = description
            await audit.record(
                s,
                actor,
                "project.update",
                "project",
                project.id,
                {
                    "before": before,
                    "after": {"name": project.name, "description": project.description},
                },
                project_id=project.id,
            )
            return project

    async def set_status(self, actor: Actor, project_id: uuid.UUID, status: str) -> Project:
        if status not in ("active", "archived"):
            raise ProblemError(422, "invalid_status", "Status must be active or archived")
        async with admin_scope(self._sessions, actor.id) as s:
            project = await self._get_live(s, project_id)
            before, project.status = project.status, status
            await audit.record(
                s,
                actor,
                f"project.{'archive' if status == 'archived' else 'restore'}",
                "project",
                project.id,
                {"before": before, "after": status},
                project_id=project.id,
            )
            return project

    async def delete_project(
        self, actor: Actor, project_id: uuid.UUID, reason: str, confirm_name: str
    ) -> None:
        """Soft delete, purged after 30 days. Memberships are removed, users kept."""
        if len(reason.strip()) < 5:
            raise ProblemError(422, "reason_required", "A deletion reason is required")
        async with admin_scope(self._sessions, actor.id) as s:
            project = await self._get_live(s, project_id)
            if confirm_name.strip() != project.name:
                raise ProblemError(422, "confirmation_mismatch", "Type the project name to confirm")
            project.status, project.deleted_at, project.delete_reason = (
                "deleted",
                utcnow(),
                reason.strip(),
            )
            await s.execute(delete(Membership).where(Membership.project_id == project.id))
            await s.execute(
                update(ApiKey)
                .where(ApiKey.project_id == project.id, ApiKey.revoked_at.is_(None))
                .values(revoked_at=utcnow())
            )
            await audit.record(
                s,
                actor,
                "project.delete",
                "project",
                project.id,
                {"reason": reason.strip()},
                project_id=project.id,
            )

    async def _get_live(self, s: AsyncSession, project_id: uuid.UUID) -> Project:
        project = await s.get(Project, project_id)
        if project is None or project.status == "deleted":
            raise NOT_FOUND
        return project

    # ---- membership-based access
    async def my_projects(self, user_id: uuid.UUID) -> list[ProjectSummary]:
        async with project_scope(self._sessions, None, user_id) as s:
            rows = (
                await s.execute(
                    select(Project, RoleName.name, RoleType.key, RoleType.permissions)
                    .join(Membership, Membership.project_id == Project.id)
                    .join(RoleName, RoleName.id == Membership.role_name_id)
                    .join(RoleType, RoleType.id == RoleName.role_type_id)
                    .where(Membership.user_id == user_id, Project.status != "deleted")
                    .order_by(func.lower(Project.name))
                )
            ).all()
            return [ProjectSummary(p, rn, rt, sorted(perms)) for p, rn, rt, perms in rows]

    async def access(self, actor: Actor, project_id: uuid.UUID) -> ProjectAccess:
        """Resolve the actor's permissions in a project; 404 when none (no existence leak)."""
        async with project_scope(self._sessions, project_id, actor.id) as s:
            project = await s.get(Project, project_id)
            if project is None or project.status == "deleted":
                raise NOT_FOUND
            if actor.api_key_project is not None:
                if actor.api_key_project != project_id:
                    raise NOT_FOUND
                creator = (
                    await self._member_permissions(s, project_id, actor.id) if actor.id else None
                )
                perms = frozenset(actor.scopes) & (creator[2] if creator else frozenset())
                return ProjectAccess(project, perms, None, None)
            member = await self._member_permissions(s, project_id, actor.id) if actor.id else None
            if member:
                return ProjectAccess(project, member[2], member[0], member[1])
            if actor.is_super_admin:
                types = await self.role_types(s)
                return ProjectAccess(
                    project, frozenset(types["project_admin"].permissions), None, "super_admin"
                )
            raise NOT_FOUND

    async def _member_permissions(
        self, s: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID | None
    ) -> tuple[str, str, frozenset[str]] | None:
        row = (
            await s.execute(
                select(RoleName.name, RoleType.key, RoleType.permissions, RoleName.status)
                .join(Membership, Membership.role_name_id == RoleName.id)
                .join(RoleType, RoleType.id == RoleName.role_type_id)
                .where(Membership.project_id == project_id, Membership.user_id == user_id)
            )
        ).first()
        if row is None or row[3] != "active":
            return None
        return row[0], row[1], frozenset(row[2])

    # ---- members (project scope)
    async def list_members(
        self, project_id: uuid.UUID, user_id: uuid.UUID, search: str | None = None
    ) -> list[MemberRow]:
        async with project_scope(self._sessions, project_id, user_id) as s:
            sql = """
                SELECT u.id, u.username, u.full_name, u.email,
                       rn.id, rn.name, rt.key, u.last_active_at,
                       coalesce(array(SELECT g.group_id FROM user_group_member g
                                      WHERE g.project_id = m.project_id AND g.user_id = u.id),
                                '{}') AS group_ids
                FROM membership m
                JOIN app_user u ON u.id = m.user_id
                JOIN role_name rn ON rn.id = m.role_name_id
                JOIN role_type rt ON rt.id = rn.role_type_id
                WHERE m.project_id = :p
                  AND (CAST(:q AS text) IS NULL
                       OR lower(u.full_name) LIKE :q OR lower(u.username) LIKE :q)
                ORDER BY lower(u.full_name), lower(u.username)
            """
            q = f"%{search.lower()}%" if search else None
            rows = (await s.execute(text(sql), {"p": project_id, "q": q})).all()
            return [MemberRow(*r) for r in rows]

    async def invite(
        self, actor: Actor, project_id: uuid.UUID, identifiers: list[str], role_name_id: uuid.UUID
    ) -> list[InviteResult]:
        if not identifiers or len(identifiers) > MAX_INVITES:
            raise ProblemError(
                422, "invalid_invite", f"Invite 1 to {MAX_INVITES} users per request"
            )
        results: list[InviteResult] = []
        async with project_scope(self._sessions, project_id, actor.id) as s:
            await self._role_in_project(s, project_id, role_name_id)
            for ident in dict.fromkeys(i.strip() for i in identifiers if i.strip()):
                row = (
                    await s.execute(
                        text(
                            "SELECT id FROM app_user WHERE status = 'active' "
                            "AND (lower(username) = lower(:i) OR lower(email) = lower(:i))"
                        ),
                        {"i": ident},
                    )
                ).first()
                if row is None:
                    results.append(InviteResult(ident, "unknown_user"))
                    continue
                uid = row[0]
                exists = (
                    await s.execute(
                        select(Membership.id).where(
                            Membership.project_id == project_id, Membership.user_id == uid
                        )
                    )
                ).first()
                if exists:
                    results.append(InviteResult(ident, "already_member", uid))
                    continue
                s.add(
                    Membership(
                        project_id=project_id,
                        user_id=uid,
                        role_name_id=role_name_id,
                        created_by=actor.id,
                    )
                )
                results.append(InviteResult(ident, "added", uid))
            added = [str(r.user_id) for r in results if r.status == "added"]
            if added:
                await audit.record(
                    s,
                    actor,
                    "member.invite",
                    "project",
                    project_id,
                    {"user_ids": added, "role_name_id": str(role_name_id)},
                    project_id=project_id,
                )
        return results

    async def update_member(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        role_name_id: uuid.UUID | None = None,
        group_ids: list[uuid.UUID] | None = None,
    ) -> None:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            m = (
                await s.execute(
                    select(Membership).where(
                        Membership.project_id == project_id, Membership.user_id == user_id
                    )
                )
            ).scalar_one_or_none()
            if m is None:
                raise ProblemError(404, "not_found", "Member not found")
            diff: dict[str, object] = {}
            if role_name_id is not None and role_name_id != m.role_name_id:
                await self._role_in_project(s, project_id, role_name_id)
                diff["role"] = [str(m.role_name_id), str(role_name_id)]
                m.role_name_id = role_name_id
                await self._keep_one_admin(s, project_id)
            if group_ids is not None:
                wanted = set(group_ids)
                known = set(
                    (
                        await s.execute(
                            select(UserGroup.id).where(
                                UserGroup.project_id == project_id, UserGroup.id.in_(wanted)
                            )
                        )
                    ).scalars()
                )
                if wanted - known:
                    raise ProblemError(422, "unknown_group", "Unknown group for this project")
                current = set(
                    (
                        await s.execute(
                            select(UserGroupMember.group_id).where(
                                UserGroupMember.project_id == project_id,
                                UserGroupMember.user_id == user_id,
                            )
                        )
                    ).scalars()
                )
                for gid in current - wanted:
                    await s.execute(
                        delete(UserGroupMember).where(
                            UserGroupMember.group_id == gid, UserGroupMember.user_id == user_id
                        )
                    )
                for gid in wanted - current:
                    s.add(UserGroupMember(group_id=gid, user_id=user_id, project_id=project_id))
                if current != wanted:
                    diff["groups"] = [sorted(map(str, current)), sorted(map(str, wanted))]
            if diff:
                await audit.record(
                    s, actor, "member.update", "user", user_id, diff, project_id=project_id
                )

    # ------------------------------------------------------------ user groups
    async def list_groups(self, project_id: uuid.UUID, user_id: uuid.UUID | None) -> list[GroupRow]:
        async with project_scope(self._sessions, project_id, user_id) as s:
            groups = list(
                (
                    await s.execute(
                        select(UserGroup)
                        .where(UserGroup.project_id == project_id)
                        .order_by(func.lower(UserGroup.name))
                    )
                ).scalars()
            )
            members: dict[uuid.UUID, list[uuid.UUID]] = {g.id: [] for g in groups}
            for gid, uid in (
                await s.execute(
                    select(UserGroupMember.group_id, UserGroupMember.user_id).where(
                        UserGroupMember.project_id == project_id
                    )
                )
            ).all():
                members.setdefault(gid, []).append(uid)
            return [
                GroupRow(g.id, g.name, g.description, members[g.id], g.created_at) for g in groups
            ]

    async def save_group(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        name: str,
        description: str,
        group_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            clash = (
                await s.execute(
                    select(UserGroup.id).where(
                        UserGroup.project_id == project_id,
                        func.lower(UserGroup.name) == name.lower(),
                        UserGroup.id != group_id if group_id else true(),
                    )
                )
            ).first()
            if clash:
                raise ProblemError(409, "name_taken", f"A group named '{name}' already exists")
            if group_id is None:
                group = UserGroup(
                    project_id=project_id, name=name, description=description, created_by=actor.id
                )
                s.add(group)
                await s.flush()
                action = "group.create"
            else:
                existing = await s.get(UserGroup, group_id)
                if existing is None or existing.project_id != project_id:
                    raise ProblemError(404, "not_found", "Group not found")
                group = existing
                group.name, group.description = name, description
                action = "group.update"
            await audit.record(
                s, actor, action, "user_group", group.id, {"name": name}, project_id=project_id
            )
            return group.id

    async def set_group_members(
        self, actor: Actor, project_id: uuid.UUID, group_id: uuid.UUID, user_ids: list[uuid.UUID]
    ) -> None:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            group = await s.get(UserGroup, group_id)
            if group is None or group.project_id != project_id:
                raise ProblemError(404, "not_found", "Group not found")
            wanted = set(user_ids)
            members = set(
                (
                    await s.execute(
                        select(Membership.user_id).where(
                            Membership.project_id == project_id, Membership.user_id.in_(wanted)
                        )
                    )
                ).scalars()
            )
            if wanted - members:
                raise ProblemError(422, "not_a_member", "Only project members can join a group")
            await s.execute(delete(UserGroupMember).where(UserGroupMember.group_id == group_id))
            for uid in wanted:
                s.add(UserGroupMember(group_id=group_id, user_id=uid, project_id=project_id))
            await audit.record(
                s,
                actor,
                "group.members",
                "user_group",
                group_id,
                {"user_ids": sorted(map(str, wanted))},
                project_id=project_id,
            )

    async def delete_group(self, actor: Actor, project_id: uuid.UUID, group_id: uuid.UUID) -> None:
        """Documents that listed only this group become readable by nobody (fail closed)."""
        async with project_scope(self._sessions, project_id, actor.id) as s:
            group = await s.get(UserGroup, group_id)
            if group is None or group.project_id != project_id:
                raise ProblemError(404, "not_found", "Group not found")
            await audit.record(
                s,
                actor,
                "group.delete",
                "user_group",
                group.id,
                {"name": group.name},
                project_id=project_id,
            )
            await s.delete(group)

    async def acl_principals(self, project_id: uuid.UUID) -> AclPrincipals:
        async with project_scope(self._sessions, project_id) as s:
            groups = {
                gid: name
                for gid, name in (
                    await s.execute(
                        select(UserGroup.id, UserGroup.name).where(
                            UserGroup.project_id == project_id
                        )
                    )
                ).all()
            }
            roles = {
                rid: name
                for rid, name in (
                    await s.execute(
                        select(RoleName.id, RoleName.name).where(RoleName.project_id == project_id)
                    )
                ).all()
            }
            return AclPrincipals(groups, roles)

    async def acl_terms(self, project_id: uuid.UUID, user_id: uuid.UUID | None) -> list[str]:
        """What a reader matches in document ACLs (US-1.16.2): `group:<id>` for each of their
        groups and `role:<role_name_id>` for their role. None for non-members and API keys."""
        if user_id is None:
            return []
        async with project_scope(self._sessions, project_id, user_id) as s:
            role = (
                await s.execute(
                    select(Membership.role_name_id).where(
                        Membership.project_id == project_id, Membership.user_id == user_id
                    )
                )
            ).scalar_one_or_none()
            groups = (
                await s.execute(
                    select(UserGroupMember.group_id).where(
                        UserGroupMember.project_id == project_id,
                        UserGroupMember.user_id == user_id,
                    )
                )
            ).scalars()
            terms = [f"group:{g}" for g in groups]
            if role is not None:
                terms.append(f"role:{role}")
            return sorted(terms)

    async def remove_member(self, actor: Actor, project_id: uuid.UUID, user_id: uuid.UUID) -> None:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            result = await s.execute(
                delete(Membership).where(
                    Membership.project_id == project_id, Membership.user_id == user_id
                )
            )
            if result.rowcount == 0:  # type: ignore[attr-defined]
                raise ProblemError(404, "not_found", "Member not found")
            await self._keep_one_admin(s, project_id)
            await audit.record(s, actor, "member.remove", "user", user_id, project_id=project_id)

    async def _keep_one_admin(self, s: AsyncSession, project_id: uuid.UUID) -> None:
        await s.flush()
        admins = (
            await s.execute(
                select(func.count())
                .select_from(Membership)
                .join(RoleName, RoleName.id == Membership.role_name_id)
                .join(RoleType, RoleType.id == RoleName.role_type_id)
                .where(Membership.project_id == project_id, RoleType.key == "project_admin")
            )
        ).scalar_one()
        if admins == 0:
            raise ProblemError(409, "last_admin", "A project needs at least one Project Admin")

    # ---- role names
    async def list_roles(
        self, project_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[tuple[RoleName, str]]:
        async with project_scope(self._sessions, project_id, user_id) as s:
            rows = (
                await s.execute(
                    select(RoleName, RoleType.key)
                    .join(RoleType, RoleType.id == RoleName.role_type_id)
                    .where(RoleName.project_id == project_id)
                    .order_by(func.lower(RoleName.name))
                )
            ).all()
            return [(r, k) for r, k in rows]

    async def member_role_name_id(
        self, project_id: uuid.UUID, user_id: uuid.UUID | None
    ) -> uuid.UUID | None:
        """The Role Name a user (or the creator of an API key) holds in the project."""
        if user_id is None:
            return None
        async with project_scope(self._sessions, project_id, user_id) as s:
            return (
                await s.execute(
                    select(Membership.role_name_id).where(
                        Membership.project_id == project_id, Membership.user_id == user_id
                    )
                )
            ).scalar_one_or_none()

    async def approvers(
        self, project_id: uuid.UUID, role_names: list[str] | None = None
    ) -> list[Approver]:
        """Active members who may decide approvals (permission `approval.decide`). With
        `role_names` only those Role Names qualify, plus Project Admins who always can."""
        async with project_scope(self._sessions, project_id, None) as s:
            rows = (
                await s.execute(
                    text(
                        """
                        SELECT u.id, u.email, u.full_name, rn.name
                        FROM membership m
                        JOIN app_user u ON u.id = m.user_id
                        JOIN role_name rn ON rn.id = m.role_name_id
                        JOIN role_type rt ON rt.id = rn.role_type_id
                        WHERE m.project_id = :p AND u.status = 'active' AND rn.status = 'active'
                          AND 'approval.decide' = ANY(rt.permissions)
                          AND (cardinality(CAST(:names AS text[])) = 0
                               OR rn.name = ANY(CAST(:names AS text[]))
                               OR rt.key = 'project_admin')
                        ORDER BY lower(u.full_name), u.id
                        """
                    ),
                    {"p": project_id, "names": list(role_names or [])},
                )
            ).all()
            return [Approver(r[0], r[1], r[2], r[3]) for r in rows]

    async def role_name_of(
        self, project_id: uuid.UUID, user_id: uuid.UUID | None, role_id: uuid.UUID
    ) -> str:
        """Name of a Role Name of the project; 404 when it belongs elsewhere."""
        async with project_scope(self._sessions, project_id, user_id) as s:
            role = await s.get(RoleName, role_id)
            if role is None or role.project_id != project_id:
                raise ProblemError(404, "not_found", "Role Name not found")
            return role.name

    async def create_role(
        self, actor: Actor, project_id: uuid.UUID, name: str, role_type: str
    ) -> RoleName:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            types = await self.role_types(s)
            if role_type not in types:
                raise ProblemError(422, "unknown_role_type", "Unknown role type")
            await self._role_name_free(s, project_id, name)
            role = RoleName(
                project_id=project_id,
                name=name.strip(),
                role_type_id=types[role_type].id,
                created_by=actor.id,
            )
            s.add(role)
            await s.flush()
            await audit.record(
                s,
                actor,
                "role.create",
                "role_name",
                role.id,
                {"name": name, "role_type": role_type},
                project_id=project_id,
            )
            return role

    async def update_role(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        role_id: uuid.UUID,
        name: str | None,
        status: str | None,
    ) -> RoleName:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            role = await self._role_in_project(s, project_id, role_id)
            before = {"name": role.name, "status": role.status}
            if name is not None and name.strip().lower() != role.name.lower():
                await self._role_name_free(s, project_id, name)
                role.name = name.strip()
            if status is not None:
                if status not in ("active", "inactive"):
                    raise ProblemError(422, "invalid_status", "Status must be active or inactive")
                role.status = status
            await audit.record(
                s,
                actor,
                "role.update",
                "role_name",
                role.id,
                {"before": before, "after": {"name": role.name, "status": role.status}},
                project_id=project_id,
            )
            return role

    async def _role_in_project(
        self, s: AsyncSession, project_id: uuid.UUID, role_id: uuid.UUID
    ) -> RoleName:
        role = await s.get(RoleName, role_id)
        if role is None or role.project_id != project_id:
            raise ProblemError(422, "unknown_role", "Role does not belong to this project")
        return role

    async def _role_name_free(self, s: AsyncSession, project_id: uuid.UUID, name: str) -> None:
        if not 2 <= len(name.strip()) <= 60:
            raise ProblemError(422, "invalid_name", "Role name must be 2 to 60 characters")
        taken = (
            await s.execute(
                select(RoleName.id).where(
                    RoleName.project_id == project_id,
                    func.lower(RoleName.name) == name.strip().lower(),
                )
            )
        ).first()
        if taken:
            raise ProblemError(409, "role_exists", "A role with this name already exists")

    # ---- API keys
    def _new_key(
        self,
        project_id: uuid.UUID,
        name: str,
        scopes: list[str],
        expires_at: datetime | None,
        created_by: uuid.UUID | None,
    ) -> tuple[str, ApiKey]:
        prefix = random_token(6).replace("-", "a").replace("_", "b")[:8]
        secret = random_token(32)
        token = f"{KEY_PREFIX}_{prefix}_{secret}"
        return token, ApiKey(
            project_id=project_id,
            name=name,
            prefix=prefix,
            hash=sha256(token),
            scopes=sorted(set(scopes)),
            expires_at=expires_at,
            created_by=created_by,
        )

    async def list_keys(self, project_id: uuid.UUID, user_id: uuid.UUID) -> list[ApiKey]:
        async with project_scope(self._sessions, project_id, user_id) as s:
            stmt = (
                select(ApiKey)
                .where(ApiKey.project_id == project_id)
                .order_by(ApiKey.created_at.desc())
            )
            return list((await s.execute(stmt)).scalars())

    async def create_key(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        name: str,
        scopes: list[str],
        expires_at: datetime | None,
        allowed: frozenset[str],
    ) -> tuple[str, ApiKey]:
        if not scopes or not set(scopes) <= allowed:
            raise ProblemError(
                422, "invalid_scopes", "Scopes must be a non-empty subset of your permissions"
            )
        if expires_at is not None and expires_at <= utcnow():
            raise ProblemError(422, "invalid_expiry", "Expiry must be in the future")
        async with project_scope(self._sessions, project_id, actor.id) as s:
            token, key = self._new_key(
                project_id, name.strip() or "API key", scopes, expires_at, actor.id
            )
            s.add(key)
            await s.flush()
            await audit.record(
                s,
                actor,
                "api_key.create",
                "api_key",
                key.id,
                {"name": key.name, "scopes": key.scopes},
                project_id=project_id,
            )
            return token, key

    async def revoke_key(self, actor: Actor, project_id: uuid.UUID, key_id: uuid.UUID) -> None:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            key = await s.get(ApiKey, key_id)
            if key is None or key.project_id != project_id:
                raise ProblemError(404, "not_found", "API key not found")
            if key.revoked_at is None:
                key.revoked_at = utcnow()
                await audit.record(
                    s, actor, "api_key.revoke", "api_key", key.id, project_id=project_id
                )

    async def resolve_key(self, token: str) -> ApiKeyPrincipal | None:
        parts = token.split("_", 2)
        if len(parts) != 3 or parts[0] != KEY_PREFIX:
            return None
        async with project_scope(self._sessions, None) as s:
            row = (
                await s.execute(text("SELECT * FROM resolve_api_key(:p)"), {"p": parts[1]})
            ).first()
            if row is None or not constant_time_equals(bytes(row.hash), sha256(token)):
                return None
            if row.revoked_at is not None or (
                row.expires_at is not None and row.expires_at <= utcnow()
            ):
                return None
        # `last_used_at` has minute resolution: touching it costs a transaction, so once per
        # minute per process is enough (the SQL guard still covers several processes).
        now = time.monotonic()
        if now - self._touched.get(row.id, -TOUCH_EVERY_S) >= TOUCH_EVERY_S:
            self._touched[row.id] = now
            async with project_scope(self._sessions, row.project_id) as s:
                await s.execute(
                    text(
                        "UPDATE api_key SET last_used_at = now() WHERE id = :k "
                        "AND (last_used_at IS NULL OR last_used_at < now() - interval '1 minute')"
                    ),
                    {"k": row.id},
                )
        return ApiKeyPrincipal(row.id, row.project_id, frozenset(row.scopes), row.created_by)
