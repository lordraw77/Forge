"""Group -> Role Name mappings and the memberships they produce (E2.1, US-2.1.5).

Mappings are platform configuration managed by the Super Admin, so this code runs as
`forge_admin`. Memberships it creates carry `source = 'mapped'`; manual ones are never touched.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import audit
from forge.core.context import Actor
from forge.core.db import admin_scope
from forge.core.errors import ProblemError
from forge.tenancy.models import GroupMapping, Membership, Project, RoleName, RoleType

# Higher wins when several mappings give a user different Role Names in one project.
RANK = {"viewer": 0, "end_user": 1, "operator": 2, "builder": 3, "project_admin": 4}


@dataclass
class MappingRow:
    mapping: GroupMapping
    project_name: str
    role_name: str
    role_type: str


@dataclass
class SyncResult:
    added: int = 0
    removed: int = 0
    changed: int = 0


class GroupMappings:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def list_mappings(self, provider_id: uuid.UUID | None = None) -> list[MappingRow]:
        async with admin_scope(self._sessions) as s:
            stmt = (
                select(GroupMapping, Project.name, RoleName.name, RoleType.key)
                .join(Project, Project.id == GroupMapping.project_id)
                .join(RoleName, RoleName.id == GroupMapping.role_name_id)
                .join(RoleType, RoleType.id == RoleName.role_type_id)
                .order_by(GroupMapping.external_group, Project.name)
            )
            if provider_id:
                stmt = stmt.where(GroupMapping.provider_id == provider_id)
            return [MappingRow(*row) for row in (await s.execute(stmt)).all()]

    async def create(
        self,
        actor: Actor,
        provider_id: uuid.UUID,
        project_id: uuid.UUID,
        role_name_id: uuid.UUID,
        external_group: str,
    ) -> GroupMapping:
        group = external_group.strip()
        if not group:
            raise ProblemError(422, "invalid_mapping", "External group is required")
        async with admin_scope(self._sessions, actor.id) as s:
            project = await s.get(Project, project_id)
            role = await s.get(RoleName, role_name_id)
            if project is None or project.status != "active":
                raise ProblemError(422, "invalid_mapping", "Project not found or not active")
            if role is None or role.project_id != project_id or role.status != "active":
                raise ProblemError(422, "invalid_mapping", "Role Name not found or inactive")
            dup = (
                await s.execute(
                    select(GroupMapping.id).where(
                        GroupMapping.provider_id == provider_id,
                        GroupMapping.project_id == project_id,
                        GroupMapping.role_name_id == role_name_id,
                        GroupMapping.external_group.ilike(group),
                    )
                )
            ).first()
            if dup:
                raise ProblemError(409, "mapping_exists", "This mapping already exists")
            row = GroupMapping(
                provider_id=provider_id,
                project_id=project_id,
                role_name_id=role_name_id,
                external_group=group,
                created_by=actor.id,
            )
            s.add(row)
            await s.flush()
            await audit.record(
                s,
                actor,
                "idp.mapping_create",
                "identity_provider",
                provider_id,
                {"group": group, "project_id": str(project_id), "role_name_id": str(role_name_id)},
            )
            return row

    async def delete(self, actor: Actor, mapping_id: uuid.UUID) -> None:
        async with admin_scope(self._sessions, actor.id) as s:
            row = await s.get(GroupMapping, mapping_id)
            if row is None:
                raise ProblemError(404, "not_found", "Mapping not found")
            await audit.record(
                s,
                actor,
                "idp.mapping_delete",
                "identity_provider",
                row.provider_id,
                {"group": row.external_group, "project_id": str(row.project_id)},
            )
            await s.delete(row)

    async def apply(
        self, actor: Actor, user_id: uuid.UUID, provider_id: uuid.UUID, groups: list[str]
    ) -> SyncResult:
        """Make the user's `mapped` memberships match the groups they hold now."""
        wanted = {g.lower() for g in groups}
        result = SyncResult()
        async with admin_scope(self._sessions, user_id) as s:
            rows = (
                await s.execute(
                    select(GroupMapping, RoleType.key)
                    .join(RoleName, RoleName.id == GroupMapping.role_name_id)
                    .join(RoleType, RoleType.id == RoleName.role_type_id)
                    .join(Project, Project.id == GroupMapping.project_id)
                    .where(
                        GroupMapping.provider_id == provider_id,
                        RoleName.status == "active",
                        Project.status == "active",
                    )
                )
            ).all()
            desired: dict[uuid.UUID, tuple[int, uuid.UUID]] = {}
            for mapping, key in rows:
                if mapping.external_group.lower() not in wanted:
                    continue
                rank = RANK.get(key, 0)
                best = desired.get(mapping.project_id)
                if best is None or rank > best[0]:
                    desired[mapping.project_id] = (rank, mapping.role_name_id)
            current = {
                m.project_id: m
                for m in (
                    await s.execute(select(Membership).where(Membership.user_id == user_id))
                ).scalars()
            }
            for project_id, (_, role_name_id) in desired.items():
                existing = current.get(project_id)
                if existing is None:
                    s.add(
                        Membership(
                            project_id=project_id,
                            user_id=user_id,
                            role_name_id=role_name_id,
                            source="mapped",
                        )
                    )
                    result.added += 1
                elif existing.source == "mapped" and existing.role_name_id != role_name_id:
                    existing.role_name_id = role_name_id
                    result.changed += 1
            for project_id, membership in current.items():
                if membership.source == "mapped" and project_id not in desired:
                    await s.execute(delete(Membership).where(Membership.id == membership.id))
                    result.removed += 1
            if result.added or result.removed or result.changed:
                await audit.record(
                    s,
                    actor,
                    "idp.memberships_synced",
                    "user",
                    user_id,
                    {"added": result.added, "removed": result.removed, "changed": result.changed},
                )
        return result

    async def drop_user(self, user_id: uuid.UUID) -> None:
        """Remove every mapped membership (user disabled in the directory)."""
        async with admin_scope(self._sessions, user_id) as s:
            await s.execute(
                delete(Membership).where(
                    Membership.user_id == user_id, Membership.source == "mapped"
                )
            )
