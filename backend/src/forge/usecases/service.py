"""Use cases (M2.3a): catalogs and categories, access control by Role Name, use case library,
published versions, lifecycle, rollback and bundle export/import. Public interface of the
usecases module (ADR-001). The graph engine and runs follow in M2.3b."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import audit
from forge.agents.service import AgentStudio
from forge.core.context import Actor
from forge.core.db import project_scope
from forge.core.errors import ProblemError
from forge.core.models import utcnow
from forge.prompts.service import PromptStudio, ReferenceOut
from forge.tenancy.service import TenancyService
from forge.tools.service import ToolReferenceOut, ToolStudio
from forge.usecases import definition as defs
from forge.usecases.definition import extract_refs as extract_refs
from forge.usecases.models import (
    Catalog,
    CatalogAccess,
    Category,
    CategoryExclusion,
    UseCase,
    UseCaseVersion,
)
from forge.usecases.schemas import (
    CatalogAccessIn,
    CatalogAccessItem,
    CatalogAccessOut,
    CatalogIn,
    CatalogOut,
    CatalogUseCase,
    CategoryIn,
    CategoryOut,
    UseCaseBody,
    UseCaseCreateIn,
    UseCaseDetail,
    UseCaseIssueOut,
    UseCaseOut,
    UseCasePage,
    UseCaseReferenceOut,
    UseCaseUpdateIn,
    UseCaseVersionDetail,
    UseCaseVersionDiff,
    UseCaseVersionSummary,
)

NOT_FOUND = ProblemError(404, "not_found", "Use case not found")
CATALOG_NOT_FOUND = ProblemError(404, "not_found", "Catalog not found")
CATEGORY_NOT_FOUND = ProblemError(404, "not_found", "Category not found")
BUILTIN_CATALOGS = {
    "Forge.Software": "Software engineering: requirements, code, testing, documentation",
    "Forge.ITOps": "IT operations: incidents, monitoring, changes, runbooks",
    "Forge.BizOps": "Business operations: reporting, finance, HR, customer service",
}
SNAPSHOT_FIELDS = (
    "description",
    "pattern",
    "input_fields",
    "output_type",
    "output_schema",
    "defaults",
    "definition",
)

ReferenceCheck = Callable[[AsyncSession, uuid.UUID], Awaitable[list[UseCaseReferenceOut]]]


class UseCaseStudio:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        agents: AgentStudio,
        tools: ToolStudio,
        prompts: PromptStudio,
        tenancy: TenancyService,
    ) -> None:
        self._sessions = sessions
        self._tenancy = tenancy
        self._agents = agents
        self._tools = tools
        self._prompts = prompts
        self._reference_checks: list[ReferenceCheck] = []
        self._run_checks: list[ReferenceCheck] = []
        agents.add_reference_check(self.agent_references)
        tools.add_reference_check(self.tool_references)
        prompts.add_reference_check(self.prompt_references)

    def add_reference_check(self, check: ReferenceCheck) -> None:
        """Modules that refer to use cases register who does (blocks delete)."""
        self._reference_checks.append(check)

    def add_run_check(self, check: ReferenceCheck) -> None:
        """The runs module registers who has runs (a use case with runs is only deprecated)."""
        self._run_checks.append(check)

    @asynccontextmanager
    async def _session(self, actor: Actor, project_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            yield s

    # ================================================================== catalogs
    @staticmethod
    async def _ensure_builtin(s: AsyncSession, project_id: uuid.UUID, actor: Actor) -> None:
        """Built-in catalogs exist from the first access of a project (idempotent)."""
        have = (
            await s.execute(select(func.count()).select_from(Catalog).where(Catalog.builtin))
        ).scalar_one()
        if have >= len(BUILTIN_CATALOGS):
            return
        for name, description in BUILTIN_CATALOGS.items():
            await s.execute(
                pg_insert(Catalog)
                .values(
                    id=uuid.uuid4(),
                    project_id=project_id,
                    name=name,
                    description=description,
                    builtin=True,
                    created_by=actor.id,
                )
                .on_conflict_do_nothing()
            )

    async def _catalog_out(
        self, s: AsyncSession, catalogs: list[Catalog], *, only: set[uuid.UUID] | None = None
    ) -> list[CatalogOut]:
        cats = (
            (await s.execute(select(Category).order_by(func.lower(Category.name)))).scalars().all()
        )
        counts = dict(
            (r[0], r[1])
            for r in (
                await s.execute(
                    select(UseCase.category_id, func.count())
                    .where(UseCase.category_id.is_not(None))
                    .group_by(UseCase.category_id)
                )
            ).all()
        )
        out: list[CatalogOut] = []
        for c in catalogs:
            mine = [
                CategoryOut(
                    id=k.id,
                    catalog_id=k.catalog_id,
                    name=k.name,
                    description=k.description,
                    use_cases=counts.get(k.id, 0),
                )
                for k in cats
                if k.catalog_id == c.id and (only is None or k.id in only)
            ]
            out.append(
                CatalogOut(
                    id=c.id,
                    name=c.name,
                    description=c.description,
                    status=c.status,
                    builtin=c.builtin,
                    categories=mine,
                    use_cases=sum(k.use_cases for k in mine),
                )
            )
        return out

    async def _role_name_id(
        self, s: AsyncSession, project_id: uuid.UUID, actor: Actor
    ) -> uuid.UUID | None:
        return await self._tenancy.member_role_name_id(project_id, actor.id)

    async def _allowed_categories(
        self, s: AsyncSession, project_id: uuid.UUID, actor: Actor
    ) -> set[uuid.UUID]:
        """Categories the actor's Role Name can see (active catalogs only)."""
        role_id = await self._role_name_id(s, project_id, actor)
        if role_id is None:
            return set()
        rows = (
            await s.execute(
                select(Category.id)
                .join(CatalogAccess, CatalogAccess.catalog_id == Category.catalog_id)
                .join(Catalog, Catalog.id == Category.catalog_id)
                .where(
                    CatalogAccess.role_name_id == role_id,
                    Catalog.status == "active",
                    Category.id.not_in(
                        select(CategoryExclusion.category_id).where(
                            CategoryExclusion.role_name_id == role_id
                        )
                    ),
                )
            )
        ).scalars()
        return set(rows)

    async def list_catalogs(
        self, actor: Actor, project_id: uuid.UUID, *, see_all: bool
    ) -> list[CatalogOut]:
        """`see_all` (builders and admins) lists every catalog; others only what their Role
        Name was granted."""
        async with self._session(actor, project_id) as s:
            await self._ensure_builtin(s, project_id, actor)
            await s.flush()
            catalogs = list(
                (await s.execute(select(Catalog).order_by(func.lower(Catalog.name)))).scalars()
            )
            if see_all:
                return await self._catalog_out(s, catalogs)
            allowed = await self._allowed_categories(s, project_id, actor)
            rows = await self._catalog_out(s, catalogs, only=allowed)
            return [c for c in rows if c.categories]

    async def _catalog(self, s: AsyncSession, catalog_id: uuid.UUID) -> Catalog:
        row = (
            await s.execute(select(Catalog).where(Catalog.id == catalog_id).with_for_update())
        ).scalar_one_or_none()
        if row is None:
            raise CATALOG_NOT_FOUND
        return row

    async def create_catalog(
        self, actor: Actor, project_id: uuid.UUID, body: CatalogIn
    ) -> CatalogOut:
        async with self._session(actor, project_id) as s:
            c = Catalog(
                project_id=project_id,
                name=body.name,
                description=body.description,
                status=body.status,
                created_by=actor.id,
            )
            s.add(c)
            await self._flush(s, f"A catalog named '{body.name}' already exists")
            await audit.record(
                s, actor, "catalog.create", "catalog", c.id, {"name": c.name}, project_id=project_id
            )
            return (await self._catalog_out(s, [c]))[0]

    async def update_catalog(
        self, actor: Actor, project_id: uuid.UUID, catalog_id: uuid.UUID, body: CatalogIn
    ) -> CatalogOut:
        async with self._session(actor, project_id) as s:
            c = await self._catalog(s, catalog_id)
            if c.builtin and body.name != c.name:
                raise ProblemError(409, "builtin_catalog", "A built-in catalog cannot be renamed")
            c.name, c.description, c.status = body.name, body.description, body.status
            await self._flush(s, f"A catalog named '{body.name}' already exists")
            await audit.record(
                s,
                actor,
                "catalog.update",
                "catalog",
                c.id,
                {"name": c.name, "status": c.status},
                project_id=project_id,
            )
            return (await self._catalog_out(s, [c]))[0]

    async def delete_catalog(
        self, actor: Actor, project_id: uuid.UUID, catalog_id: uuid.UUID
    ) -> None:
        async with self._session(actor, project_id) as s:
            c = await self._catalog(s, catalog_id)
            if c.builtin:
                raise ProblemError(
                    409, "builtin_catalog", "A built-in catalog cannot be deleted; disable it"
                )
            refs = await self._catalog_references(s, catalog_id=c.id)
            if refs:
                raise ProblemError(
                    409,
                    "catalog_in_use",
                    "Use cases are filed in this catalog",
                    [r.model_dump(mode="json") for r in refs],
                )
            await audit.record(
                s, actor, "catalog.delete", "catalog", c.id, {"name": c.name}, project_id=project_id
            )
            await s.delete(c)

    async def create_category(
        self, actor: Actor, project_id: uuid.UUID, catalog_id: uuid.UUID, body: CategoryIn
    ) -> CatalogOut:
        async with self._session(actor, project_id) as s:
            c = await self._catalog(s, catalog_id)
            k = Category(
                project_id=project_id,
                catalog_id=c.id,
                name=body.name,
                description=body.description,
                created_by=actor.id,
            )
            s.add(k)
            await self._flush(s, f"A category named '{body.name}' already exists in this catalog")
            await audit.record(
                s,
                actor,
                "category.create",
                "category",
                k.id,
                {"name": k.name, "catalog": c.name},
                project_id=project_id,
            )
            return (await self._catalog_out(s, [c]))[0]

    async def _category(self, s: AsyncSession, category_id: uuid.UUID) -> Category:
        row = (
            await s.execute(select(Category).where(Category.id == category_id).with_for_update())
        ).scalar_one_or_none()
        if row is None:
            raise CATEGORY_NOT_FOUND
        return row

    async def update_category(
        self, actor: Actor, project_id: uuid.UUID, category_id: uuid.UUID, body: CategoryIn
    ) -> CatalogOut:
        async with self._session(actor, project_id) as s:
            k = await self._category(s, category_id)
            k.name, k.description = body.name, body.description
            await self._flush(s, f"A category named '{body.name}' already exists in this catalog")
            await audit.record(
                s,
                actor,
                "category.update",
                "category",
                k.id,
                {"name": k.name},
                project_id=project_id,
            )
            return (await self._catalog_out(s, [await self._catalog(s, k.catalog_id)]))[0]

    async def delete_category(
        self, actor: Actor, project_id: uuid.UUID, category_id: uuid.UUID
    ) -> None:
        async with self._session(actor, project_id) as s:
            k = await self._category(s, category_id)
            refs = await self._catalog_references(s, category_id=k.id)
            if refs:
                raise ProblemError(
                    409,
                    "category_in_use",
                    "Use cases are filed in this category",
                    [r.model_dump(mode="json") for r in refs],
                )
            await audit.record(
                s,
                actor,
                "category.delete",
                "category",
                k.id,
                {"name": k.name},
                project_id=project_id,
            )
            await s.delete(k)

    @staticmethod
    async def _catalog_references(
        s: AsyncSession,
        *,
        catalog_id: uuid.UUID | None = None,
        category_id: uuid.UUID | None = None,
    ) -> list[UseCaseReferenceOut]:
        stmt = select(UseCase.id, UseCase.name)
        if catalog_id:
            stmt = stmt.where(UseCase.catalog_id == catalog_id)
        if category_id:
            stmt = stmt.where(UseCase.category_id == category_id)
        rows = (await s.execute(stmt)).all()
        return [UseCaseReferenceOut(kind="use_case", id=i, name=n) for i, n in rows]

    @staticmethod
    async def _flush(s: AsyncSession, message: str) -> None:
        try:
            await s.flush()
        except IntegrityError as exc:
            raise ProblemError(409, "name_taken", message) from exc

    # ------------------------------------------------------------------ access
    async def get_access(
        self, actor: Actor, project_id: uuid.UUID, role_name_id: uuid.UUID
    ) -> CatalogAccessOut:
        async with self._session(actor, project_id) as s:
            await self._ensure_builtin(s, project_id, actor)
            await s.flush()
            role_name = await self._tenancy.role_name_of(project_id, actor.id, role_name_id)
            granted = set(
                (
                    await s.execute(
                        select(CatalogAccess.catalog_id).where(
                            CatalogAccess.role_name_id == role_name_id
                        )
                    )
                ).scalars()
            )
            excluded = list(
                (
                    await s.execute(
                        select(CategoryExclusion.category_id, Category.catalog_id)
                        .join(Category, Category.id == CategoryExclusion.category_id)
                        .where(CategoryExclusion.role_name_id == role_name_id)
                    )
                ).all()
            )
            catalogs = (
                (await s.execute(select(Catalog).order_by(func.lower(Catalog.name))))
                .scalars()
                .all()
            )
            return CatalogAccessOut(
                role_name_id=role_name_id,
                role_name=role_name,
                catalogs=[
                    CatalogAccessItem(
                        catalog_id=c.id,
                        enabled=c.id in granted,
                        excluded_categories=[k for k, cat in excluded if cat == c.id],
                    )
                    for c in catalogs
                ],
            )

    async def set_access(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        role_name_id: uuid.UUID,
        body: CatalogAccessIn,
    ) -> CatalogAccessOut:
        async with self._session(actor, project_id) as s:
            role_name = await self._tenancy.role_name_of(project_id, actor.id, role_name_id)
            catalogs = {c.id for c in (await s.execute(select(Catalog))).scalars()}
            categories = {k.id: k.catalog_id for k in (await s.execute(select(Category))).scalars()}
            for item in body.catalogs:
                if item.catalog_id not in catalogs:
                    raise ProblemError(422, "unknown_catalog", "Unknown catalog")
                for kid in item.excluded_categories:
                    if categories.get(kid) != item.catalog_id:
                        raise ProblemError(
                            422, "unknown_category", "A category does not belong to its catalog"
                        )
            await s.execute(delete(CatalogAccess).where(CatalogAccess.role_name_id == role_name_id))
            await s.execute(
                delete(CategoryExclusion).where(CategoryExclusion.role_name_id == role_name_id)
            )
            for item in body.catalogs:
                if not item.enabled:
                    continue
                s.add(
                    CatalogAccess(
                        project_id=project_id, role_name_id=role_name_id, catalog_id=item.catalog_id
                    )
                )
                for kid in dict.fromkeys(item.excluded_categories):
                    s.add(
                        CategoryExclusion(
                            project_id=project_id, role_name_id=role_name_id, category_id=kid
                        )
                    )
            await audit.record(
                s,
                actor,
                "catalog.access",
                "role_name",
                role_name_id,
                {
                    "role": role_name,
                    "catalogs": [str(i.catalog_id) for i in body.catalogs if i.enabled],
                },
                project_id=project_id,
            )
            await s.flush()
        return await self.get_access(actor, project_id, role_name_id)

    # ================================================================== use cases
    @staticmethod
    async def _use_case(s: AsyncSession, use_case_id: uuid.UUID, *, lock: bool = False) -> UseCase:
        stmt = select(UseCase).where(UseCase.id == use_case_id)
        if lock:
            stmt = stmt.with_for_update()
        row = (await s.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise NOT_FOUND
        return row

    @staticmethod
    async def _version(s: AsyncSession, uc: UseCase, number: int) -> UseCaseVersion:
        row = (
            await s.execute(
                select(UseCaseVersion).where(
                    UseCaseVersion.use_case_id == uc.id, UseCaseVersion.version == number
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise ProblemError(404, "version_not_found", f"Version {number} does not exist")
        return row

    @staticmethod
    async def _names(s: AsyncSession) -> tuple[dict[uuid.UUID, str], dict[uuid.UUID, str]]:
        catalogs = {c.id: c.name for c in (await s.execute(select(Catalog))).scalars()}
        categories = {k.id: k.name for k in (await s.execute(select(Category))).scalars()}
        return catalogs, categories

    @staticmethod
    def _out(
        uc: UseCase, catalogs: dict[uuid.UUID, str], categories: dict[uuid.UUID, str]
    ) -> UseCaseOut:
        return UseCaseOut(
            id=uc.id,
            name=uc.name,
            description=uc.description,
            icon=uc.icon,
            tags=list(uc.tags or []),
            catalog_id=uc.catalog_id,
            catalog=catalogs.get(uc.catalog_id) if uc.catalog_id else None,
            category_id=uc.category_id,
            category=categories.get(uc.category_id) if uc.category_id else None,
            pattern=uc.pattern,
            status=uc.status,
            latest_version=uc.latest_version,
            published_version=uc.published_version,
            dirty=uc.dirty,
            updated_at=uc.updated_at,
            created_at=uc.created_at,
        )

    async def _detail(self, s: AsyncSession, uc: UseCase) -> UseCaseDetail:
        catalogs, categories = await self._names(s)
        versions = list(
            (
                await s.execute(
                    select(UseCaseVersion.version)
                    .where(UseCaseVersion.use_case_id == uc.id)
                    .order_by(UseCaseVersion.version.desc())
                )
            ).scalars()
        )
        issues = defs.validate_definition(uc.pattern, uc.definition)
        base = self._out(uc, catalogs, categories)
        return UseCaseDetail(
            **base.model_dump(),
            input_fields=uc.input_fields,
            input_schema=defs.input_schema(uc.input_fields),
            output_type=uc.output_type,
            output_schema=uc.output_schema,
            defaults=uc.defaults,
            definition=uc.definition,
            issues=[UseCaseIssueOut(**i.as_dict()) for i in issues],
            versions=versions,
        )

    async def _check_filing(self, s: AsyncSession, body: UseCaseBody) -> None:
        if body.catalog_id:
            await self._catalog_plain(s, body.catalog_id)
        if body.category_id:
            k = (
                await s.execute(select(Category).where(Category.id == body.category_id))
            ).scalar_one_or_none()
            if k is None or k.catalog_id != body.catalog_id:
                raise ProblemError(422, "unknown_category", "The category is not in that catalog")

    @staticmethod
    async def _catalog_plain(s: AsyncSession, catalog_id: uuid.UUID) -> Catalog:
        c = (await s.execute(select(Catalog).where(Catalog.id == catalog_id))).scalar_one_or_none()
        if c is None:
            raise ProblemError(422, "unknown_catalog", "Unknown catalog")
        return c

    @staticmethod
    def _structure(body: UseCaseBody, pattern: str) -> None:
        issues = defs.validate_definition(pattern, body.definition)
        if issues:
            raise ProblemError(
                422,
                "invalid_definition",
                "The workflow definition is not valid",
                [i.as_dict() for i in issues],
            )

    @staticmethod
    def _apply(uc: UseCase, body: UseCaseBody) -> None:
        uc.description = body.description
        uc.icon = body.icon
        uc.tags = list(dict.fromkeys(t.lower() for t in body.tags))
        uc.catalog_id = body.catalog_id
        uc.category_id = body.category_id
        uc.input_fields = [f.model_dump(mode="json") for f in body.input_fields]
        uc.output_type = body.output_type
        uc.output_schema = body.output_schema
        uc.defaults = body.defaults.model_dump(mode="json")
        uc.definition = body.definition
        uc.dirty = True

    async def create(
        self, actor: Actor, project_id: uuid.UUID, body: UseCaseCreateIn
    ) -> UseCaseDetail:
        self._structure(body, body.pattern)
        async with self._session(actor, project_id) as s:
            await self._check_filing(s, body)
            uc = UseCase(
                project_id=project_id,
                name=body.name,
                pattern=body.pattern,
                created_by=actor.id,
                updated_by=actor.id,
            )
            self._apply(uc, body)
            s.add(uc)
            await self._flush(s, f"A use case named '{body.name}' already exists")
            await audit.record(
                s,
                actor,
                "usecase.create",
                "use_case",
                uc.id,
                {"name": uc.name, "pattern": uc.pattern},
                project_id=project_id,
            )
            return await self._detail(s, uc)

    async def update(
        self, actor: Actor, project_id: uuid.UUID, use_case_id: uuid.UUID, body: UseCaseUpdateIn
    ) -> UseCaseDetail:
        async with self._session(actor, project_id) as s:
            uc = await self._use_case(s, use_case_id, lock=True)
            self._structure(body, uc.pattern)
            await self._check_filing(s, body)
            if body.name:
                uc.name = body.name
            self._apply(uc, body)
            uc.updated_by, uc.updated_at = actor.id, utcnow()
            await self._flush(s, f"A use case named '{uc.name}' already exists")
            await audit.record(
                s,
                actor,
                "usecase.update",
                "use_case",
                uc.id,
                {"name": uc.name},
                project_id=project_id,
            )
            return await self._detail(s, uc)

    async def get(
        self, actor: Actor, project_id: uuid.UUID, use_case_id: uuid.UUID
    ) -> UseCaseDetail:
        async with self._session(actor, project_id) as s:
            return await self._detail(s, await self._use_case(s, use_case_id))

    async def list_use_cases(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        *,
        q: str | None = None,
        status: str | None = None,
        catalog_id: uuid.UUID | None = None,
        category_id: uuid.UUID | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> UseCasePage:
        stmt = select(UseCase).order_by(UseCase.updated_at.desc(), UseCase.id.desc()).limit(200)
        if q:
            like = (
                "%" + q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            )
            stmt = stmt.where(
                or_(
                    func.lower(UseCase.name).like(like, escape="\\"),
                    func.lower(UseCase.description).like(like, escape="\\"),
                )
            )
        if status:
            stmt = stmt.where(UseCase.status == status)
        if catalog_id:
            stmt = stmt.where(UseCase.catalog_id == catalog_id)
        if category_id:
            stmt = stmt.where(UseCase.category_id == category_id)
        if since:
            stmt = stmt.where(UseCase.updated_at >= since)
        if until:
            stmt = stmt.where(UseCase.updated_at < until)
        async with self._session(actor, project_id) as s:
            rows = (await s.execute(stmt)).scalars().all()
            catalogs, categories = await self._names(s)
            return UseCasePage(items=[self._out(u, catalogs, categories) for u in rows])

    async def duplicate(
        self, actor: Actor, project_id: uuid.UUID, use_case_id: uuid.UUID, name: str
    ) -> UseCaseDetail:
        async with self._session(actor, project_id) as s:
            src = await self._use_case(s, use_case_id)
            copy = UseCase(
                project_id=project_id,
                name=name,
                pattern=src.pattern,
                catalog_id=src.catalog_id,
                category_id=src.category_id,
                tags=list(src.tags or []),
                created_by=actor.id,
                updated_by=actor.id,
            )
            for f in SNAPSHOT_FIELDS:
                setattr(copy, f, getattr(src, f))
            copy.icon = src.icon
            s.add(copy)
            await self._flush(s, f"A use case named '{name}' already exists")
            await audit.record(
                s,
                actor,
                "usecase.create",
                "use_case",
                copy.id,
                {"name": name, "copy_of": src.name},
                project_id=project_id,
            )
            return await self._detail(s, copy)

    # ------------------------------------------------------------------ lifecycle
    async def _published_versions(
        self, actor: Actor, project_id: uuid.UUID
    ) -> dict[str, dict[str, int]]:
        """Highest version that can be pinned, per kind and lower-case name: only published
        assets (agents and prompts tagged `prod`, tools with a published version)."""
        agents = {
            a.name.lower(): a.version
            for a in await self._agents.available(actor, project_id, include_drafts=False)
        }
        tools = {
            t.name.lower(): t.published_version
            for t in (await self._tools.list_tools(actor, project_id, published_only=True)).items
            if t.published_version
        }
        prompts: dict[str, int] = {}
        cursor = None
        while True:
            page = await self._prompts.list_prompts(
                actor, project_id, status="published", cursor=cursor, limit=200
            )
            prompts |= {
                p.name.lower(): p.published_version for p in page.items if p.published_version
            }
            cursor = page.next_cursor
            if not cursor:
                break
        return {"agent": agents, "tool": tools, "prompt": prompts}

    async def reference_issues(
        self, actor: Actor, project_id: uuid.UUID, pattern: str, definition: dict[str, Any]
    ) -> list[defs.Issue]:
        refs, _ = defs.extract_refs(pattern, definition)
        if not refs:
            return []
        published = await self._published_versions(actor, project_id)
        issues: list[defs.Issue] = []
        for r in refs:
            top = published[r.kind].get(r.name.lower())
            if top is None:
                issues.append(
                    defs.Issue(
                        "reference_not_published",
                        f"The {r.kind} '{r.name}' does not exist or is not published",
                        r.node,
                    )
                )
            elif r.version > top:
                issues.append(
                    defs.Issue(
                        "reference_version_unknown",
                        f"The {r.kind} '{r.name}' has no published version {r.version} "
                        f"(latest {top})",
                        r.node,
                    )
                )
        return issues

    async def readiness(self, actor: Actor, project_id: uuid.UUID, uc: UseCase) -> list[defs.Issue]:
        """Everything that blocks testing or publishing, with the nodes to highlight."""
        issues = defs.validate_definition(uc.pattern, uc.definition)
        issues += defs.completeness(uc.pattern, uc.definition)
        if not issues:
            issues += await self.reference_issues(actor, project_id, uc.pattern, uc.definition)
        return issues

    @staticmethod
    def _blocked(code: str, title: str, issues: list[defs.Issue]) -> ProblemError:
        return ProblemError(422, code, title, [i.as_dict() for i in issues])

    async def set_status(
        self, actor: Actor, project_id: uuid.UUID, use_case_id: uuid.UUID, status: str
    ) -> UseCaseDetail:
        async with self._session(actor, project_id) as s:
            uc = await self._use_case(s, use_case_id, lock=True)
            if status == "testing":
                if uc.status not in {"draft", "testing"}:
                    raise ProblemError(409, "bad_transition", "Only a draft can go to testing")
                issues = await self.readiness(actor, project_id, uc)
                if issues:
                    raise self._blocked(
                        "not_ready", "The use case is not ready for testing", issues
                    )
            elif status == "draft":
                if uc.status not in {"draft", "testing"}:
                    raise ProblemError(
                        409, "bad_transition", "A published use case is deprecated, not drafted"
                    )
            elif status == "deprecated":
                if uc.status != "published":
                    raise ProblemError(
                        409, "bad_transition", "Only a published use case can be deprecated"
                    )
            uc.status = status
            uc.updated_by = actor.id
            await audit.record(
                s,
                actor,
                f"usecase.{status}",
                "use_case",
                uc.id,
                {"name": uc.name},
                project_id=project_id,
            )
            await s.flush()
            return await self._detail(s, uc)

    async def publish(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        use_case_id: uuid.UUID,
        *,
        version: int | None,
        note: str,
    ) -> UseCaseDetail:
        async with self._session(actor, project_id) as s:
            uc = await self._use_case(s, use_case_id, lock=True)
            if version is not None:
                return await self._rollback(s, actor, uc, version)
            if uc.status == "published" and not uc.dirty:
                raise ProblemError(
                    409, "nothing_to_publish", "The draft equals the published version"
                )
            issues = await self.readiness(actor, project_id, uc)
            if uc.catalog_id is None or uc.category_id is None:
                issues.append(defs.Issue("no_category", "Choose a catalog and a category"))
            else:
                catalog = await self._catalog_plain(s, uc.catalog_id)
                if catalog.status != "active":
                    issues.append(
                        defs.Issue("catalog_disabled", f"The catalog '{catalog.name}' is disabled")
                    )
            if issues:
                raise self._blocked("not_ready", "The use case cannot be published yet", issues)
            uc.latest_version += 1
            s.add(
                UseCaseVersion(
                    project_id=project_id,
                    use_case_id=uc.id,
                    version=uc.latest_version,
                    name=uc.name,
                    description=uc.description,
                    pattern=uc.pattern,
                    input_fields=uc.input_fields,
                    input_schema=defs.input_schema(uc.input_fields),
                    output_type=uc.output_type,
                    output_schema=uc.output_schema,
                    defaults=uc.defaults,
                    definition=uc.definition,
                    note=note,
                    published_by=actor.id,
                )
            )
            uc.published_version = uc.latest_version
            uc.status, uc.dirty, uc.updated_by = "published", False, actor.id
            await audit.record(
                s,
                actor,
                "usecase.publish",
                "use_case",
                uc.id,
                {"name": uc.name, "version": uc.published_version},
                project_id=project_id,
            )
            await s.flush()
            return await self._detail(s, uc)

    async def _rollback(
        self, s: AsyncSession, actor: Actor, uc: UseCase, number: int
    ) -> UseCaseDetail:
        v = await self._version(s, uc, number)
        issues = await self.reference_issues(actor, uc.project_id, v.pattern, v.definition)
        if issues:
            raise self._blocked(
                "not_ready", "This version no longer resolves its references", issues
            )
        for f in SNAPSHOT_FIELDS:
            setattr(uc, f, getattr(v, f))
        uc.published_version = v.version
        uc.status, uc.dirty, uc.updated_by = "published", False, actor.id
        await audit.record(
            s,
            actor,
            "usecase.rollback",
            "use_case",
            uc.id,
            {"name": uc.name, "version": v.version},
            project_id=uc.project_id,
        )
        await s.flush()
        return await self._detail(s, uc)

    async def versions(
        self, actor: Actor, project_id: uuid.UUID, use_case_id: uuid.UUID
    ) -> list[UseCaseVersionSummary]:
        async with self._session(actor, project_id) as s:
            uc = await self._use_case(s, use_case_id)
            rows = (
                (
                    await s.execute(
                        select(UseCaseVersion)
                        .where(UseCaseVersion.use_case_id == uc.id)
                        .order_by(UseCaseVersion.version.desc())
                    )
                )
                .scalars()
                .all()
            )
            return [
                UseCaseVersionSummary(
                    version=v.version,
                    note=v.note,
                    published_at=v.published_at,
                    published_by=v.published_by,
                    live=v.version == uc.published_version and uc.status == "published",
                )
                for v in rows
            ]

    @staticmethod
    def _version_out(v: UseCaseVersion, uc: UseCase) -> UseCaseVersionDetail:
        return UseCaseVersionDetail(
            version=v.version,
            note=v.note,
            published_at=v.published_at,
            published_by=v.published_by,
            live=v.version == uc.published_version and uc.status == "published",
            name=v.name,
            description=v.description,
            pattern=v.pattern,
            input_fields=v.input_fields,
            input_schema=v.input_schema,
            output_type=v.output_type,
            output_schema=v.output_schema,
            defaults=v.defaults,
            definition=v.definition,
        )

    async def version(
        self, actor: Actor, project_id: uuid.UUID, use_case_id: uuid.UUID, number: int
    ) -> UseCaseVersionDetail:
        async with self._session(actor, project_id) as s:
            uc = await self._use_case(s, use_case_id)
            return self._version_out(await self._version(s, uc, number), uc)

    async def diff(
        self, actor: Actor, project_id: uuid.UUID, use_case_id: uuid.UUID, left: int, right: int
    ) -> UseCaseVersionDiff:
        async with self._session(actor, project_id) as s:
            uc = await self._use_case(s, use_case_id)
            a = self._version_out(await self._version(s, uc, left), uc)
            b = self._version_out(await self._version(s, uc, right), uc)
            fields = (
                "name",
                "description",
                "pattern",
                "input_fields",
                "output_type",
                "output_schema",
                "defaults",
                "definition",
            )
            changed = [f for f in fields if getattr(a, f) != getattr(b, f)]
            return UseCaseVersionDiff(left=a, right=b, changed=changed)

    # ------------------------------------------------------------------ delete / references
    async def delete(self, actor: Actor, project_id: uuid.UUID, use_case_id: uuid.UUID) -> None:
        async with self._session(actor, project_id) as s:
            uc = await self._use_case(s, use_case_id, lock=True)
            runs: list[UseCaseReferenceOut] = []
            for check in self._run_checks:
                runs += await check(s, uc.id)
            if runs:
                raise ProblemError(
                    409,
                    "use_case_has_runs",
                    "This use case has runs; deprecate it instead",
                    [r.model_dump(mode="json") for r in runs],
                )
            refs: list[UseCaseReferenceOut] = []
            for check in self._reference_checks:
                refs += await check(s, uc.id)
            if refs:
                raise ProblemError(
                    409,
                    "use_case_in_use",
                    "Other resources refer to this use case",
                    [r.model_dump(mode="json") for r in refs],
                )
            await audit.record(
                s,
                actor,
                "usecase.delete",
                "use_case",
                uc.id,
                {"name": uc.name, "versions": uc.latest_version},
                project_id=project_id,
            )
            await s.delete(uc)

    async def _users_of(self, s: AsyncSession, kind: str, name: str) -> list[tuple[uuid.UUID, str]]:
        """Use cases whose draft or live version pins the asset `name`."""
        needle = name.lower()
        rows = (await s.execute(select(UseCase))).scalars().all()
        found: dict[uuid.UUID, str] = {}
        live = {
            v.use_case_id: v
            for v in (
                await s.execute(
                    select(UseCaseVersion).join(
                        UseCase,
                        and_(
                            UseCase.id == UseCaseVersion.use_case_id,
                            UseCase.published_version == UseCaseVersion.version,
                        ),
                    )
                )
            )
            .scalars()
            .all()
        }
        for uc in rows:
            sources = [(uc.pattern, uc.definition)]
            if uc.id in live:
                sources.append((live[uc.id].pattern, live[uc.id].definition))
            for pattern, definition in sources:
                refs, _ = defs.extract_refs(pattern, definition)
                if any(r.kind == kind and r.name.lower() == needle for r in refs):
                    found[uc.id] = uc.name
        return list(found.items())

    async def agent_references(self, s: AsyncSession, agent_id: uuid.UUID) -> list[ReferenceOut]:
        name = await AgentStudio.name_in(s, agent_id)
        if not name:
            return []
        return [
            ReferenceOut(kind="use_case", id=i, name=n)
            for i, n in await self._users_of(s, "agent", name)
        ]

    async def tool_references(self, s: AsyncSession, tool_id: uuid.UUID) -> list[ToolReferenceOut]:
        name = await ToolStudio.name_in(s, tool_id)
        if not name:
            return []
        return [
            ToolReferenceOut(kind="use_case", id=i, name=n)
            for i, n in await self._users_of(s, "tool", name)
        ]

    async def prompt_references(self, s: AsyncSession, prompt_id: uuid.UUID) -> list[ReferenceOut]:
        name = await PromptStudio.name_in(s, prompt_id)
        if not name:
            return []
        return [
            ReferenceOut(kind="use_case", id=i, name=n)
            for i, n in await self._users_of(s, "prompt", name)
        ]

    # ================================================================== end-user catalog
    async def catalog_view(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        *,
        see_all: bool,
        q: str | None = None,
        category_id: uuid.UUID | None = None,
        tag: str | None = None,
    ) -> list[CatalogUseCase]:
        """Published use cases the caller may run; builders see every published one."""
        async with self._session(actor, project_id) as s:
            stmt = (
                select(UseCase, UseCaseVersion, Catalog.name, Category.name)
                .join(
                    UseCaseVersion,
                    and_(
                        UseCaseVersion.use_case_id == UseCase.id,
                        UseCaseVersion.version == UseCase.published_version,
                    ),
                )
                .join(Category, Category.id == UseCase.category_id)
                .join(Catalog, Catalog.id == UseCase.catalog_id)
                .where(UseCase.status == "published", Catalog.status == "active")
                .order_by(func.lower(UseCase.name))
                .limit(500)
            )
            if not see_all:
                allowed = await self._allowed_categories(s, project_id, actor)
                if not allowed:
                    return []
                stmt = stmt.where(UseCase.category_id.in_(sorted(allowed)))
            if category_id:
                stmt = stmt.where(UseCase.category_id == category_id)
            if tag:
                stmt = stmt.where(UseCase.tags.contains([tag.lower()]))
            if q:
                like = (
                    "%"
                    + q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    + "%"
                )
                stmt = stmt.where(
                    or_(
                        func.lower(UseCase.name).like(like, escape="\\"),
                        func.lower(UseCase.description).like(like, escape="\\"),
                    )
                )
            rows = (await s.execute(stmt)).all()
            return [
                CatalogUseCase(
                    id=uc.id,
                    name=v.name,
                    description=v.description,
                    icon=uc.icon,
                    tags=list(uc.tags or []),
                    catalog_id=uc.catalog_id,
                    catalog=cat,
                    category_id=uc.category_id,
                    category=kat,
                    version=v.version,
                    input_schema=v.input_schema,
                    input_fields=v.input_fields,
                    output_type=v.output_type,
                )
                for uc, v, cat, kat in rows
            ]

    async def catalog_item(
        self, actor: Actor, project_id: uuid.UUID, use_case_id: uuid.UUID, *, see_all: bool
    ) -> CatalogUseCase:
        """One catalog entry; 404 (not 403) when the caller cannot see it (US-2.4.2 #3)."""
        for item in await self.catalog_view(actor, project_id, see_all=see_all):
            if item.id == use_case_id:
                return item
        raise NOT_FOUND

    # ================================================================== bundles (ADR-018)
    async def export_use_case(
        self, actor: Actor, project_id: uuid.UUID, use_case_id: uuid.UUID, scope: str
    ) -> dict[str, Any]:
        async with self._session(actor, project_id) as s:
            uc = await self._use_case(s, use_case_id)
            catalogs, categories = await self._names(s)
            versions = list(
                (
                    await s.execute(
                        select(UseCaseVersion)
                        .where(UseCaseVersion.use_case_id == uc.id)
                        .order_by(UseCaseVersion.version)
                    )
                ).scalars()
            )
            if scope == "published":
                versions = [v for v in versions if v.version == uc.published_version]
            elif scope == "latest":
                versions = versions[-1:]
            return {
                "kind": "use_case",
                "name": uc.name,
                "description": uc.description,
                "icon": uc.icon,
                "tags": list(uc.tags or []),
                "pattern": uc.pattern,
                "catalog": catalogs.get(uc.catalog_id) if uc.catalog_id else None,
                "category": categories.get(uc.category_id) if uc.category_id else None,
                "input_fields": uc.input_fields,
                "output_type": uc.output_type,
                "output_schema": uc.output_schema,
                "defaults": uc.defaults,
                "definition": uc.definition,
                "history": [
                    {"version": v.version, "note": v.note, "definition": v.definition}
                    for v in versions
                ],
            }

    async def find_use_case(
        self, actor: Actor, project_id: uuid.UUID, name: str
    ) -> uuid.UUID | None:
        async with self._session(actor, project_id) as s:
            return (
                await s.execute(select(UseCase.id).where(func.lower(UseCase.name) == name.lower()))
            ).scalar_one_or_none()

    async def import_use_case(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        item: dict[str, Any],
        policy: str,
        *,
        dry_run: bool,
        source: str,
    ) -> dict[str, Any]:
        """Imports the working definition as a draft; published history is not recreated
        (publishing stays a deliberate act). References are re-pinned to the published version
        of the same name in this project and reported when missing."""
        name = str(item["name"])
        warnings: list[str] = []
        existing = await self.find_use_case(actor, project_id, name)
        if existing and policy == "skip":
            return {"kind": "use_case", "name": name, "action": "skipped", "id": str(existing)}
        target = name
        if existing and policy == "rename":
            target = f"{name} (imported)"[:80]
            n = 1
            while await self.find_use_case(actor, project_id, target):
                n += 1
                target = f"{name} (imported {n})"[:80]
            existing = None
        pattern = str(item["pattern"])
        definition = item.get("definition") or {}
        async with self._session(actor, project_id) as s:
            catalogs = {c.name.lower(): c.id for c in (await s.execute(select(Catalog))).scalars()}
            categories = {
                (k.catalog_id, k.name.lower()): k.id
                for k in (await s.execute(select(Category))).scalars()
            }
        catalog_id = catalogs.get(str(item.get("catalog") or "").lower())
        category_id = (
            categories.get((catalog_id, str(item.get("category") or "").lower()))
            if catalog_id
            else None
        )
        if item.get("catalog") and not category_id:
            warnings.append("catalog or category not found here; file it before publishing")
            catalog_id = category_id = None
        published = await self._published_versions(actor, project_id)
        definition, missing = _repin(pattern, definition, published)
        warnings += [f"{m} is not published in this project" for m in missing]
        if item.get("history"):
            warnings.append("published history is not imported; publish to create versions")
        body = UseCaseBody.model_validate(
            {
                "description": item.get("description") or "",
                "icon": item.get("icon") or "",
                "tags": item.get("tags") or [],
                "catalog_id": catalog_id,
                "category_id": category_id,
                "input_fields": item.get("input_fields") or [],
                "output_type": item.get("output_type") or "markdown",
                "output_schema": item.get("output_schema"),
                "defaults": item.get("defaults") or {},
                "definition": definition,
            }
        )
        self._structure(body, pattern)
        action = "new_versions" if existing else "created"
        result: dict[str, Any] = {
            "kind": "use_case",
            "name": name,
            "action": action,
            "warnings": warnings,
        }
        if target != name:
            result["action"], result["target"] = "renamed", target
        if dry_run:
            return result
        note = f"Imported from {source}"
        if existing:
            detail = await self.update(
                actor, project_id, existing, UseCaseUpdateIn(**body.model_dump(mode="python"))
            )
        else:
            detail = await self.create(
                actor,
                project_id,
                UseCaseCreateIn(name=target, pattern=pattern, **body.model_dump(mode="python")),
            )
        result["id"] = str(detail.id)
        result["versions"] = 0
        async with self._session(actor, project_id) as s:
            await audit.record(
                s,
                actor,
                "usecase.import",
                "use_case",
                detail.id,
                {"source": note},
                project_id=project_id,
            )
        return result


def _repin(
    pattern: str, definition: dict[str, Any], published: dict[str, dict[str, int]]
) -> tuple[dict[str, Any], list[str]]:
    """Point every reference at the published version of the same name in this project."""
    key = "agents" if pattern == "supervisor" else "nodes"
    out = dict(definition)
    items = out.get(key)
    missing: list[str] = []
    if not isinstance(items, list):
        return out, missing
    fresh: list[Any] = []
    for entry in items:
        if not isinstance(entry, dict):
            fresh.append(entry)
            continue
        entry = dict(entry)
        for ref_key, kind in defs.REF_KEYS.items():
            m = defs.REF.match(str(entry.get(ref_key) or ""))
            if not m:
                continue
            top = published[kind].get(m.group(1).lower())
            if top is None:
                missing.append(f"{kind} '{m.group(1)}'")
            else:
                entry[ref_key] = f"{m.group(1)}@{top}"
        fresh.append(entry)
    out[key] = fresh
    return out, list(dict.fromkeys(missing))
