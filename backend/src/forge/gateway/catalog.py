"""Registry administration: providers, models, enablement, aliases, project limits (E1.5, E1.9).

One code path serves both scopes: `scope=None` is the platform (Super Admin, `forge_admin`
session), a project id is that project (Project Admin, RLS-bound session, ADR-002). Every
write is audited and published on the config channel in the same transaction, then the local
registry is reloaded so the change applies to the next request (ADR-007).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import GatewayLimits, SettingsStore, audit, notify, secrets
from forge.core.context import Actor
from forge.core.crypto import Envelope
from forge.core.db import admin_scope, project_scope
from forge.core.errors import ProblemError
from forge.gateway.models import (
    GatewayProjectConfig,
    Model,
    ModelAlias,
    ModelEnablement,
    ModelProvider,
)
from forge.gateway.registry import (
    NOTIFY_KEY,
    STANDARD_ALIASES,
    ModelConfig,
    Provider,
    Registry,
    effective_limits,
)
from forge.gateway.schemas import (
    HALFVEC_FROM,
    NAME_PATTERN,
    RERANK_PROVIDERS,
    AliasOut,
    ModelDraft,
    ModelIn,
    ModelOut,
    ModelReference,
    ProjectGatewayConfigIn,
    ProjectGatewayConfigOut,
    ProviderIn,
    ProviderOut,
)

Scope = uuid.UUID | None
SECRET_KIND = "model_provider"  # noqa: S105 - a secret kind label, not a secret
_NAME_RE = re.compile(NAME_PATTERN)


def _scope_label(scope: Scope) -> str:
    return "platform" if scope is None else "project"


def _same_scope(row_project: uuid.UUID | None, scope: Scope) -> bool:
    return row_project == scope


class Catalog:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        envelope: Envelope,
        registry: Registry,
        store: SettingsStore,
    ) -> None:
        self._sessions = sessions
        self._envelope = envelope
        self._registry = registry
        self._store = store

    @asynccontextmanager
    async def _session(self, actor: Actor, scope: Scope) -> AsyncIterator[AsyncSession]:
        if scope is None:
            async with admin_scope(self._sessions, actor.id) as s:
                yield s
        else:
            async with project_scope(self._sessions, scope, actor.id) as s:
                yield s

    async def _changed(self, s: AsyncSession) -> None:
        await notify(s, NOTIFY_KEY)

    # ------------------------------------------------------------ providers
    async def list_providers(self, actor: Actor, scope: Scope) -> list[ProviderOut]:
        async with self._session(actor, scope) as s:
            counts = (
                select(Model.provider_id, func.count().label("n"))
                .group_by(Model.provider_id)
                .subquery()
            )
            stmt = (
                select(ModelProvider, func.coalesce(counts.c.n, 0))
                .outerjoin(counts, counts.c.provider_id == ModelProvider.id)
                .order_by(func.lower(ModelProvider.name))
            )
            stmt = stmt.where(
                ModelProvider.project_id.is_(None)
                if scope is None
                else ModelProvider.project_id == scope
            )
            return [self._provider_out(p, n) for p, n in (await s.execute(stmt)).all()]

    @staticmethod
    def _provider_out(p: ModelProvider, model_count: int = 0) -> ProviderOut:
        return ProviderOut(
            id=p.id,
            project_id=p.project_id,
            name=p.name,
            provider_type=p.provider_type,
            base_url=p.base_url,
            config=p.config,
            status=p.status,
            has_credentials=p.secret_id is not None,
            model_count=model_count,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )

    async def _provider_name_free(
        self, s: AsyncSession, scope: Scope, name: str, exclude: uuid.UUID | None = None
    ) -> None:
        stmt = select(ModelProvider.id).where(
            func.lower(ModelProvider.name) == name.lower(),
            ModelProvider.project_id.is_(None)
            if scope is None
            else ModelProvider.project_id == scope,
        )
        if exclude:
            stmt = stmt.where(ModelProvider.id != exclude)
        if (await s.execute(stmt)).first():
            raise ProblemError(409, "provider_exists", "A provider with this name already exists")

    async def create_provider(self, actor: Actor, scope: Scope, body: ProviderIn) -> ProviderOut:
        async with self._session(actor, scope) as s:
            await self._provider_name_free(s, scope, body.name)
            secret_id = None
            if body.api_key and body.api_key.get_secret_value():
                secret_id = await secrets.put(
                    s, self._envelope, SECRET_KIND, body.api_key.get_secret_value(), scope
                )
            row = ModelProvider(
                project_id=scope,
                name=body.name,
                provider_type=body.provider_type,
                base_url=body.base_url,
                secret_id=secret_id,
                config=body.config.model_dump(exclude_defaults=True),
                status=body.status,
                created_by=actor.id,
                updated_by=actor.id,
            )
            s.add(row)
            await s.flush()
            await audit.record(
                s,
                actor,
                "provider.create",
                "model_provider",
                row.id,
                self._provider_diff(row),
                project_id=scope,
            )
            await self._changed(s)
            out = self._provider_out(row)
        await self._registry.load()
        return out

    @staticmethod
    def _provider_diff(row: ModelProvider) -> dict[str, Any]:
        return {
            "name": row.name,
            "provider_type": row.provider_type,
            "base_url": row.base_url,
            "config": row.config,
            "status": row.status,
            "credentials": "set" if row.secret_id else "none",
        }

    async def _get_provider(
        self, s: AsyncSession, scope: Scope, provider_id: uuid.UUID
    ) -> ModelProvider:
        row = await s.get(ModelProvider, provider_id)
        if row is None or not _same_scope(row.project_id, scope):
            raise ProblemError(404, "not_found", "Provider not found")
        return row

    async def update_provider(
        self, actor: Actor, scope: Scope, provider_id: uuid.UUID, body: ProviderIn
    ) -> ProviderOut:
        async with self._session(actor, scope) as s:
            row = await self._get_provider(s, scope, provider_id)
            before = self._provider_diff(row)
            if body.name.lower() != row.name.lower():
                await self._provider_name_free(s, scope, body.name, exclude=row.id)
            if body.provider_type != row.provider_type:
                raise ProblemError(
                    422, "provider_type_immutable", "The provider type cannot be changed"
                )
            row.name, row.base_url, row.status = body.name, body.base_url, body.status
            row.config = body.config.model_dump(exclude_defaults=True)
            row.updated_by = actor.id
            if body.api_key is not None:  # write-only: null keeps, "" clears, value replaces
                old = row.secret_id
                value = body.api_key.get_secret_value()
                row.secret_id = (
                    await secrets.put(s, self._envelope, SECRET_KIND, value, scope)
                    if value
                    else None
                )
                await s.flush()
                if old is not None:
                    await secrets.delete(s, old)
            await s.flush()
            after = self._provider_diff(row)
            if body.api_key is not None:
                after["credentials"] = "replaced" if row.secret_id else "removed"
            await audit.record(
                s,
                actor,
                "provider.update",
                "model_provider",
                row.id,
                {"before": before, "after": after},
                project_id=scope,
            )
            await self._changed(s)
            count = (
                await s.execute(select(func.count()).where(Model.provider_id == row.id))
            ).scalar_one()
            out = self._provider_out(row, count)
        await self._registry.load()
        return out

    async def delete_provider(self, actor: Actor, scope: Scope, provider_id: uuid.UUID) -> None:
        async with self._session(actor, scope) as s:
            row = await self._get_provider(s, scope, provider_id)
            names = list(
                (await s.execute(select(Model.name).where(Model.provider_id == row.id))).scalars()
            )
            if names:
                raise ProblemError(
                    409,
                    "provider_in_use",
                    "Delete the provider's models first",
                    {"models": names},
                )
            secret_id = row.secret_id
            await s.delete(row)
            await s.flush()
            if secret_id:
                await secrets.delete(s, secret_id)
            await audit.record(
                s, actor, "provider.delete", "model_provider", row.id, {"name": row.name},
                project_id=scope,
            )  # fmt: skip
            await self._changed(s)
        await self._registry.load()

    # --------------------------------------------------------------- models
    async def list_models(self, actor: Actor, scope: Scope) -> list[ModelOut]:
        """Platform scope: platform models. Project scope: the project's own models plus the
        platform models enabled for it (read-only there)."""
        async with self._session(actor, scope) as s:
            stmt = select(Model, ModelProvider).join(
                ModelProvider, ModelProvider.id == Model.provider_id
            )
            if scope is None:
                stmt = stmt.where(Model.project_id.is_(None))
            rows = (await s.execute(stmt.order_by(func.lower(Model.name)))).all()
            enabled: dict[uuid.UUID, list[uuid.UUID]] = {}
            for e in (await s.execute(select(ModelEnablement))).scalars():
                enabled.setdefault(e.model_id, []).append(e.project_id)
        out = []
        for m, p in rows:
            hidden = not (m.all_projects or scope in enabled.get(m.id, []))
            if scope is not None and m.project_id is None and hidden:
                continue
            out.append(self._model_out(m, p, enabled.get(m.id, []) if scope is None else []))
        return out

    @staticmethod
    def _model_out(m: Model, p: ModelProvider, enabled: list[uuid.UUID]) -> ModelOut:
        storage = None
        if m.model_type == "embedding" and m.embedding_dims:
            storage = "halfvec" if m.embedding_dims >= HALFVEC_FROM else "vector"
        return ModelOut(
            id=m.id,
            project_id=m.project_id,
            scope="platform" if m.project_id is None else "project",
            provider_id=p.id,
            provider_name=p.name,
            provider_type=p.provider_type,
            name=m.name,
            display_name=m.display_name,
            model_type=m.model_type,
            model_id=m.model_id,
            params=m.params,
            context_window=m.context_window,
            max_output_tokens=m.max_output_tokens,
            embedding_dims=m.embedding_dims,
            vector_storage=storage,
            cost_input=Decimal(m.cost_input),
            cost_output=Decimal(m.cost_output),
            capabilities=sorted(m.capabilities),
            timeout_s=m.timeout_s,
            max_retries=m.max_retries,
            rpm_limit=m.rpm_limit,
            all_projects=m.all_projects,
            enabled_project_ids=sorted(enabled),
            status=m.status,
            created_at=m.created_at,
            updated_at=m.updated_at,
        )

    async def _check_model_name(
        self, s: AsyncSession, scope: Scope, name: str, exclude: uuid.UUID | None
    ) -> None:
        """Names are unique per scope and never shadow a platform model or an alias."""
        lowered = name.lower()
        stmt = select(Model.id, Model.project_id).where(func.lower(Model.name) == lowered)
        if exclude:
            stmt = stmt.where(Model.id != exclude)
        for _id, project_id in (await s.execute(stmt)).all():
            # Platform scope sees every project's models (forge_admin); project scope sees its
            # own plus platform ones (dual RLS policy).
            if scope is None or project_id is None or project_id == scope:
                raise ProblemError(409, "model_exists", f"A model named '{name}' already exists")
        aliases = select(ModelAlias.name).where(func.lower(ModelAlias.name) == lowered)
        if lowered in STANDARD_ALIASES or (await s.execute(aliases)).first():
            raise ProblemError(409, "name_is_alias", f"'{name}' is an alias name")

    async def _validate_model(
        self, s: AsyncSession, scope: Scope, body: ModelIn, exclude: uuid.UUID | None = None
    ) -> ModelProvider:
        provider = await s.get(ModelProvider, body.provider_id)
        if provider is None or not _same_scope(provider.project_id, scope):
            raise ProblemError(
                422,
                "unknown_provider",
                f"Choose a {_scope_label(scope)} provider for a {_scope_label(scope)} model",
            )
        if body.model_type == "reranker" and provider.provider_type not in RERANK_PROVIDERS:
            raise ProblemError(
                422,
                "capability_not_supported",
                f"Rerank is not supported for provider type {provider.provider_type}",
            )
        if scope is not None and (body.all_projects or body.enabled_project_ids):
            raise ProblemError(
                422, "invalid_enablement", "Project models are only available in their project"
            )
        await self._check_model_name(s, scope, body.name, exclude)
        return provider

    def _apply(self, row: Model, body: ModelIn, actor: Actor) -> None:
        for name in (
            "provider_id",
            "name",
            "display_name",
            "model_type",
            "model_id",
            "params",
            "context_window",
            "max_output_tokens",
            "embedding_dims",
            "cost_input",
            "cost_output",
            "timeout_s",
            "max_retries",
            "rpm_limit",
            "all_projects",
            "status",
        ):
            setattr(row, name, getattr(body, name))
        row.capabilities = sorted(set(body.capabilities))
        row.updated_by = actor.id

    async def _set_enablement(
        self, s: AsyncSession, actor: Actor, model_id: uuid.UUID, project_ids: list[uuid.UUID]
    ) -> None:
        wanted = set(project_ids)
        if wanted:
            found = set(
                (
                    await s.execute(
                        text("SELECT id FROM project WHERE id = ANY(:ids) AND status <> 'deleted'"),
                        {"ids": list(wanted)},
                    )
                ).scalars()
            )
            if missing := wanted - found:
                raise ProblemError(
                    422,
                    "unknown_project",
                    "Unknown projects",
                    {"projects": sorted(map(str, missing))},
                )
        await s.execute(delete(ModelEnablement).where(ModelEnablement.model_id == model_id))
        for pid in sorted(wanted):
            s.add(ModelEnablement(model_id=model_id, project_id=pid, created_by=actor.id))

    async def create_model(self, actor: Actor, scope: Scope, body: ModelIn) -> ModelOut:
        async with self._session(actor, scope) as s:
            provider = await self._validate_model(s, scope, body)
            row = Model(project_id=scope, created_by=actor.id)
            self._apply(row, body, actor)
            s.add(row)
            await s.flush()
            if scope is None:
                await self._set_enablement(s, actor, row.id, body.enabled_project_ids)
            await audit.record(
                s,
                actor,
                "model.create",
                "model",
                row.id,
                body.model_dump(mode="json"),
                project_id=scope,
            )
            await self._changed(s)
            await s.flush()
            await s.refresh(row)
            out = self._model_out(row, provider, body.enabled_project_ids)
        await self._registry.load()
        return out

    async def _get_model(self, s: AsyncSession, scope: Scope, model_id: uuid.UUID) -> Model:
        row = await s.get(Model, model_id)
        if row is None or not _same_scope(row.project_id, scope):
            raise ProblemError(404, "not_found", "Model not found")
        return row

    async def update_model(
        self, actor: Actor, scope: Scope, model_id: uuid.UUID, body: ModelIn
    ) -> ModelOut:
        async with self._session(actor, scope) as s:
            row = await self._get_model(s, scope, model_id)
            before = self._model_out(
                row, await self._get_provider(s, scope, row.provider_id), []
            ).model_dump(mode="json", exclude={"created_at", "updated_at"})
            provider = await self._validate_model(s, scope, body, exclude=row.id)
            if body.model_type != row.model_type:
                await self._ensure_unreferenced(s, row, "change the type of")
            self._apply(row, body, actor)
            if scope is None:
                await self._set_enablement(s, actor, row.id, body.enabled_project_ids)
            await s.flush()
            await audit.record(
                s,
                actor,
                "model.update",
                "model",
                row.id,
                {"before": before, "after": body.model_dump(mode="json")},
                project_id=scope,
            )
            await self._changed(s)
            await s.refresh(row)
            out = self._model_out(row, provider, body.enabled_project_ids if scope is None else [])
        await self._registry.load()
        return out

    async def _references(self, s: AsyncSession, model_id: uuid.UUID) -> list[ModelReference]:
        rows = (
            await s.execute(
                select(ModelAlias.name, ModelAlias.project_id).where(
                    ModelAlias.model_ids.any(model_id)  # type: ignore[arg-type]
                )
            )
        ).all()
        return [ModelReference(kind="alias", name=n, project_id=p) for n, p in rows]

    async def _ensure_unreferenced(self, s: AsyncSession, row: Model, verb: str) -> None:
        refs = await self._references(s, row.id)
        if refs:
            raise ProblemError(
                409,
                "model_in_use",
                f"Cannot {verb} a model used by aliases",
                {"references": [r.model_dump(mode="json") for r in refs]},
            )

    async def delete_model(self, actor: Actor, scope: Scope, model_id: uuid.UUID) -> None:
        """Blocked while an alias references the model (US-1.9.1); agents join in E1.18."""
        async with self._session(actor, scope) as s:
            row = await self._get_model(s, scope, model_id)
            await self._ensure_unreferenced(s, row, "delete")
            await s.delete(row)
            await audit.record(
                s, actor, "model.delete", "model", row.id, {"name": row.name}, project_id=scope
            )
            await self._changed(s)
        await self._registry.load()

    def draft_model(
        self,
        scope: Scope,
        draft: ModelDraft,
        provider: ProviderIn | None,
        provider_row: Provider | None,
        api_key: str | None,
    ) -> ModelConfig:
        """An unsaved model for Test Connection (US-1.5.2)."""
        if provider_row is not None:
            prov = Provider(
                provider_row.id,
                provider_row.project_id,
                provider_row.name,
                provider_row.provider_type,
                provider_row.base_url,
                api_key if api_key else provider_row.api_key,
                provider_row.config,
                "active",
            )
        else:
            assert provider is not None
            prov = Provider(
                uuid.UUID(int=0),
                scope,
                provider.name,
                provider.provider_type,
                provider.base_url,
                provider.api_key.get_secret_value() if provider.api_key else None,
                provider.config.model_dump(exclude_defaults=True),
                "active",
            )
        return ModelConfig(
            id=uuid.UUID(int=0),
            project_id=scope,
            name="test",
            display_name="test",
            model_type=draft.model_type,
            model_id=draft.model_id,
            params=draft.params,
            context_window=None,
            max_output_tokens=None,
            embedding_dims=draft.embedding_dims,
            cost_input=Decimal(0),
            cost_output=Decimal(0),
            capabilities=frozenset(),
            timeout_s=draft.timeout_s,
            max_retries=0,
            rpm_limit=None,
            all_projects=False,
            enabled_projects=frozenset(),
            status="active",
            provider=prov,
        )

    def saved_provider(self, scope: Scope, provider_id: uuid.UUID) -> Provider:
        provider = self._registry.snapshot.providers.get(provider_id)
        if provider is None or provider.project_id != scope:
            raise ProblemError(404, "not_found", "Provider not found")
        return provider

    def saved_model(self, scope: Scope, model_id: uuid.UUID) -> ModelConfig:
        model = self._registry.snapshot.models.get(model_id)
        if model is None or model.project_id != scope:
            raise ProblemError(404, "not_found", "Model not found")
        return model

    # -------------------------------------------------------------- aliases
    async def list_aliases(self, actor: Actor, scope: Scope) -> list[AliasOut]:
        async with self._session(actor, scope) as s:
            rows = list(
                (
                    await s.execute(
                        select(ModelAlias).where(
                            ModelAlias.project_id.is_(None)
                            if scope is None
                            else (
                                ModelAlias.project_id.is_(None) | (ModelAlias.project_id == scope)
                            )
                        )
                    )
                ).scalars()
            )
            names = {m.id: m.name for m in (await s.execute(select(Model))).scalars()}
        platform = {a.name.lower(): a for a in rows if a.project_id is None}
        project = {a.name.lower(): a for a in rows if a.project_id is not None}
        out: list[AliasOut] = []
        keys = sorted(set(STANDARD_ALIASES) | set(platform) | set(project))
        for key in keys:
            row = project.get(key) or platform.get(key)
            if row is None:
                out.append(
                    AliasOut(name=key, scope=_scope_label(scope), model_ids=[], model_names=[])
                )
                continue
            out.append(
                AliasOut(
                    name=row.name,
                    scope="platform" if row.project_id is None else "project",
                    model_ids=list(row.model_ids),
                    model_names=[names.get(i, "?") for i in row.model_ids],
                    overrides_platform=row.project_id is not None and key in platform,
                    updated_at=row.updated_at,
                )
            )
        return out

    async def set_alias(
        self, actor: Actor, scope: Scope, name: str, model_ids: list[uuid.UUID]
    ) -> AliasOut:
        if not _NAME_RE.match(name):
            raise ProblemError(422, "invalid_name", "Alias names use letters, digits and ._:/-")
        if len(set(model_ids)) != len(model_ids):
            raise ProblemError(422, "duplicate_model", "A model appears twice in the chain")
        async with self._session(actor, scope) as s:
            models = {
                m.id: m
                for m in (await s.execute(select(Model).where(Model.id.in_(model_ids)))).scalars()
            }
            enabled = set()
            if scope is not None:
                enabled = set(
                    (
                        await s.execute(
                            select(ModelEnablement.model_id).where(
                                ModelEnablement.project_id == scope
                            )
                        )
                    ).scalars()
                )
            chain = []
            for mid in model_ids:
                m = models.get(mid)
                visible = m is not None and (
                    (scope is None and m.project_id is None)
                    or (
                        scope is not None
                        and (
                            m.project_id == scope
                            or (m.project_id is None and (m.all_projects or m.id in enabled))
                        )
                    )
                )
                if not visible:
                    raise ProblemError(
                        422, "unknown_model", f"Model {mid} is not available in this scope"
                    )
                chain.append(m)
            assert chain
            if len({m.model_type for m in chain}) > 1:  # type: ignore[union-attr]
                raise ProblemError(422, "mixed_types", "All models in a chain must have one type")
            if chain[0].model_type == "embedding" and len({m.embedding_dims for m in chain}) > 1:  # type: ignore[union-attr]
                raise ProblemError(
                    422,
                    "mixed_dimensions",
                    "Embedding fallbacks must have the same dimensions (stored vectors)",
                )
            clash = select(Model.id).where(
                func.lower(Model.name) == name.lower(),
                Model.project_id.is_(None) | (Model.project_id == scope)
                if scope is not None
                else Model.project_id.is_(None),
            )
            if (await s.execute(clash)).first():
                raise ProblemError(409, "name_is_model", f"'{name}' is already a model name")
            row = (
                await s.execute(
                    select(ModelAlias).where(
                        func.lower(ModelAlias.name) == name.lower(),
                        ModelAlias.project_id.is_(None)
                        if scope is None
                        else ModelAlias.project_id == scope,
                    )
                )
            ).scalar_one_or_none()
            before = list(map(str, row.model_ids)) if row else None
            if row is None:
                row = ModelAlias(
                    project_id=scope, name=name, model_ids=model_ids, updated_by=actor.id
                )
                s.add(row)
            else:
                row.model_ids, row.updated_by = model_ids, actor.id
            await s.flush()
            await audit.record(
                s,
                actor,
                "alias.set",
                "model_alias",
                name,
                {"before": before, "after": list(map(str, model_ids))},
                project_id=scope,
            )
            await self._changed(s)
            out = AliasOut(
                name=row.name,
                scope=_scope_label(scope),
                model_ids=list(model_ids),
                model_names=[m.name for m in chain],  # type: ignore[union-attr]
                updated_at=row.updated_at,
            )
        await self._registry.load()
        return out

    async def delete_alias(self, actor: Actor, scope: Scope, name: str) -> None:
        async with self._session(actor, scope) as s:
            result = await s.execute(
                delete(ModelAlias).where(
                    func.lower(ModelAlias.name) == name.lower(),
                    ModelAlias.project_id.is_(None)
                    if scope is None
                    else ModelAlias.project_id == scope,
                )
            )
            if result.rowcount == 0:  # type: ignore[attr-defined]
                raise ProblemError(404, "not_found", "Alias not found")
            await audit.record(s, actor, "alias.delete", "model_alias", name, project_id=scope)
            await self._changed(s)
        await self._registry.load()

    # ------------------------------------------------------- project limits
    async def get_project_config(
        self, actor: Actor, project_id: uuid.UUID
    ) -> ProjectGatewayConfigOut:
        async with self._session(actor, project_id) as s:
            row = await s.get(GatewayProjectConfig, project_id)
            config = (
                ProjectGatewayConfigIn.model_validate(row, from_attributes=True)
                if row
                else ProjectGatewayConfigIn()
            )
        return self._config_out(config, project_id)

    def _config_out(
        self, config: ProjectGatewayConfigIn, project_id: uuid.UUID
    ) -> ProjectGatewayConfigOut:
        platform = self._store.get(GatewayLimits)
        return ProjectGatewayConfigOut(
            config=config,
            platform=effective_limits(platform, None),
            effective=self._registry.limits(project_id),
        )

    async def set_project_config(
        self, actor: Actor, project_id: uuid.UUID, body: ProjectGatewayConfigIn
    ) -> ProjectGatewayConfigOut:
        platform = self._store.get(GatewayLimits)
        for name in (
            "tokens_per_day",
            "requests_per_minute",
            "user_tokens_per_day",
            "user_requests_per_minute",
            "max_tokens_per_call",
        ):
            ceiling, value = getattr(platform, name), getattr(body, name)
            if ceiling and value is not None and (value == 0 or value > ceiling):
                raise ProblemError(
                    422,
                    "above_platform_limit",
                    "Value above the platform limit",
                    f"{name} must be between 1 and the platform limit {ceiling}",
                )
        if body.cache_ttl_s > platform.cache_ttl_max_s:
            raise ProblemError(
                422,
                "above_platform_limit",
                "Value above the platform limit",
                f"cache_ttl_s must not exceed {platform.cache_ttl_max_s}",
            )
        async with self._session(actor, project_id) as s:
            row = await s.get(GatewayProjectConfig, project_id)
            before = (
                ProjectGatewayConfigIn.model_validate(row, from_attributes=True).model_dump()
                if row
                else None
            )
            if row is None:
                row = GatewayProjectConfig(project_id=project_id)
                s.add(row)
            for name, value in body.model_dump().items():
                setattr(row, name, value)
            row.updated_by = actor.id
            await s.flush()
            await audit.record(
                s,
                actor,
                "gateway.config_update",
                "project",
                project_id,
                {"before": before, "after": body.model_dump()},
                project_id=project_id,
            )
            await self._changed(s)
        await self._registry.load()
        return self._config_out(body, project_id)
