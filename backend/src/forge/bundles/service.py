"""Export and import of project configuration and assets (PO request 2026-10-19).

A bundle is one JSON document:

    {"format": "forge.bundle", "schema_version": 1, "forge_version": "...", "exported_at": "...",
     "source": {"project": "..."}, "items": [...]}

Items: `prompt` and `rag_pipeline` (with the chosen versions and their tags), `data_collection`
(definition only, never files), and configuration: `model_alias` (project aliases, by model
name), `gateway_config` (project limits), `user_group` (names, never members). Secrets and
document contents are never exported.

Multi-version support:

- object versions: all, the published one, or the latest, per item;
- Forge versions: `schema_version` is migrated forward on import (`MIGRATIONS`); a bundle from
  a newer Forge is refused; unknown fields are ignored;
- conflicts: an existing name is extended with new versions, imported under a new name, or
  skipped (`policy`), with a dry run that reports what would happen.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from forge import __version__
from forge.core.context import Actor
from forge.core.errors import ProblemError
from forge.gateway.service import Gateway, ProjectGatewayConfigIn
from forge.prompts.service import PromptStudio
from forge.rag.service import Rag
from forge.tenancy.service import TenancyService

FORMAT = "forge.bundle"
SCHEMA_VERSION = 1
Scope = Literal["all", "published", "latest"]
Policy = Literal["new_version", "rename", "skip"]
CONFIG_KINDS = frozenset({"model_alias", "gateway_config", "user_group"})

# schema_version N -> function upgrading a bundle from N to N + 1. Add one with every change
# of the bundle format; old bundles keep importing.
MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


class In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssetRef(In):
    id: uuid.UUID
    versions: Scope = "all"


class ExportIn(In):
    prompts: list[AssetRef] = Field(default_factory=list, max_length=500)
    rag_pipelines: list[AssetRef] = Field(default_factory=list, max_length=500)
    collections: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    include_dependencies: bool = Field(
        default=True, description="Add the prompts and collections used by the pipelines"
    )
    model_aliases: bool = False
    gateway_config: bool = False
    user_groups: bool = False


class ImportIn(In):
    bundle: dict[str, Any]
    policy: Policy = "new_version"
    dry_run: bool = True


class ImportItem(BaseModel):
    kind: str
    name: str
    action: str  # created, new_versions, renamed, reused, updated, skipped, forbidden, error
    target: str | None = None
    versions: int = 0
    id: str | None = None
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ImportReport(BaseModel):
    dry_run: bool
    policy: Policy
    schema_version: int
    migrated_from: int | None
    source: dict[str, Any]
    items: list[ImportItem]
    summary: dict[str, int]


def migrate(bundle: dict[str, Any]) -> tuple[dict[str, Any], int | None]:
    """Validate the envelope and bring an older bundle up to SCHEMA_VERSION."""
    if bundle.get("format") != FORMAT:
        raise ProblemError(422, "not_a_bundle", "This file is not a Forge bundle")
    version = bundle.get("schema_version")
    if not isinstance(version, int) or version < 1:
        raise ProblemError(422, "bundle_invalid", "The bundle has no valid schema_version")
    if version > SCHEMA_VERSION:
        raise ProblemError(
            422,
            "bundle_too_new",
            "The bundle comes from a newer Forge; upgrade this installation first",
            {"bundle": version, "supported": SCHEMA_VERSION},
        )
    original = version
    while version < SCHEMA_VERSION:
        bundle = MIGRATIONS[version](bundle)
        version += 1
        bundle["schema_version"] = version
    if not isinstance(bundle.get("items"), list):
        raise ProblemError(422, "bundle_invalid", "The bundle has no items")
    return bundle, (original if original != SCHEMA_VERSION else None)


@dataclass
class Bundles:
    prompts: PromptStudio
    rag: Rag
    gateway: Gateway
    tenancy: TenancyService

    # ------------------------------------------------------------ export
    async def export(
        self, actor: Actor, project_id: uuid.UUID, body: ExportIn, *, can_manage: bool
    ) -> dict[str, Any]:
        if (body.model_aliases or body.gateway_config or body.user_groups) and not can_manage:
            raise ProblemError(403, "forbidden", "Exporting configuration needs project.manage")
        items: list[dict[str, Any]] = []
        prompt_scope: dict[uuid.UUID, str] = {p.id: p.versions for p in body.prompts}
        collection_ids: set[uuid.UUID] = set(body.collections)
        pipelines: list[dict[str, Any]] = []
        for ref in body.rag_pipelines:
            item, cols, prompts = await self.rag.studio.export_pipeline(
                actor, project_id, ref.id, ref.versions
            )
            pipelines.append(item)
            if body.include_dependencies:
                collection_ids |= cols
                for pid in prompts:
                    prompt_scope.setdefault(pid, ref.versions)
        prompt_names: dict[str, str] = {}
        for pid, scope in prompt_scope.items():
            item = await self.prompts.export_prompt(actor, project_id, pid, scope)
            prompt_names[str(pid)] = item["name"]
            items.append(item)
        for item in pipelines:
            for v in item["versions"]:
                ref = v["config"].pop("prompt_ref", None)
                v["config"]["prompt"] = prompt_names.get(ref) if ref else None
                if ref and ref not in prompt_names:
                    v["config"]["prompt"] = None
                    item.setdefault("warnings", []).append(
                        f"version {v['version']}: its prompt was not exported"
                    )
        for cid in sorted(collection_ids):
            items.append(await self.rag.studio.export_collection(actor, project_id, cid))
        items += pipelines
        if body.model_aliases:
            for a in await self.gateway.catalog.list_aliases(actor, project_id):
                if a.scope == "project" and a.model_names:
                    items.append({"kind": "model_alias", "name": a.name, "models": a.model_names})
        if body.gateway_config:
            cfg = await self.gateway.catalog.get_project_config(actor, project_id)
            items.append(
                {"kind": "gateway_config", "name": "gateway", "config": cfg.config.model_dump()}
            )
        if body.user_groups:
            for g in await self.tenancy.list_groups(project_id, actor.id):
                items.append({"kind": "user_group", "name": g.name, "description": g.description})
        access = await self.tenancy.access(actor, project_id)
        return {
            "format": FORMAT,
            "schema_version": SCHEMA_VERSION,
            "forge_version": __version__,
            "exported_at": datetime.now(UTC).isoformat(),
            "source": {"project": access.project.name},
            "items": items,
        }

    # ------------------------------------------------------------ import
    async def import_(
        self, actor: Actor, project_id: uuid.UUID, body: ImportIn, *, can_manage: bool
    ) -> ImportReport:
        bundle, migrated_from = migrate(dict(body.bundle))
        source = str((bundle.get("source") or {}).get("project") or "bundle")[:80]
        order = [
            "user_group",
            "model_alias",
            "gateway_config",
            "data_collection",
            "prompt",
            "rag_pipeline",
        ]
        items = sorted(
            (i for i in bundle["items"] if isinstance(i, dict) and i.get("kind") in order),
            key=lambda i: order.index(i["kind"]),
        )
        unknown = [
            i for i in bundle["items"] if not (isinstance(i, dict) and i.get("kind") in order)
        ]
        report: list[ImportItem] = [
            ImportItem(
                kind=str(i.get("kind") if isinstance(i, dict) else "?"),
                name=str(i.get("name", "") if isinstance(i, dict) else ""),
                action="skipped",
                warnings=["unknown item kind"],
            )
            for i in unknown
        ]
        collections = await self.rag.studio.collection_ids(actor, project_id)
        # Dry run: aliases the bundle itself creates do not exist yet.
        assumed = frozenset(
            str(i.get("name", "")).lower()
            for i in items
            if i.get("kind") == "model_alias" and body.dry_run
        )
        prompt_ids: dict[str, uuid.UUID] = {}
        for item in items:
            kind, name = item["kind"], str(item.get("name") or "")
            if kind in CONFIG_KINDS and not can_manage:
                report.append(
                    ImportItem(
                        kind=kind, name=name, action="forbidden", error="needs project.manage"
                    )
                )
                continue
            try:
                result = await self._import_one(
                    actor, project_id, item, body, source, collections, prompt_ids, assumed
                )
            except ProblemError as exc:
                result = {
                    "kind": kind,
                    "name": name,
                    "action": "error",
                    "error": f"{exc.code}: {exc.title}",
                }
            except (KeyError, TypeError, ValueError) as exc:
                result = {
                    "kind": kind,
                    "name": name,
                    "action": "error",
                    "error": f"malformed item: {exc}"[:300],
                }
            report.append(ImportItem.model_validate(result))
        summary: dict[str, int] = {}
        for r in report:
            summary[r.action] = summary.get(r.action, 0) + 1
        return ImportReport(
            dry_run=body.dry_run,
            policy=body.policy,
            schema_version=SCHEMA_VERSION,
            migrated_from=migrated_from,
            source=bundle.get("source") or {},
            items=report,
            summary=summary,
        )

    async def _import_one(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        item: dict[str, Any],
        body: ImportIn,
        source: str,
        collections: dict[str, uuid.UUID],
        prompt_ids: dict[str, uuid.UUID],
        assumed: frozenset[str],
    ) -> dict[str, Any]:
        kind, name, dry = item["kind"], str(item.get("name") or ""), body.dry_run
        if kind == "data_collection":
            result = await self.rag.studio.import_collection(actor, project_id, item, dry_run=dry)
            if result.get("id"):
                collections[name.lower()] = uuid.UUID(result["id"])
            elif dry:
                collections[name.lower()] = uuid.uuid4()  # will exist after the real import
            return result
        if kind == "prompt":
            result = await self.prompts.import_prompt(
                actor,
                project_id,
                item,
                body.policy,
                dry_run=dry,
                source=source,
                assume_models=assumed,
            )
            target = result.get("target") or name
            if result.get("id"):
                prompt_ids[name.lower()] = uuid.UUID(result["id"])
            elif result["action"] == "skipped":
                existing = await self.prompts.find_prompt(actor, project_id, target)
                if existing:
                    prompt_ids[name.lower()] = existing
            elif dry and result["action"] != "error":
                prompt_ids[name.lower()] = uuid.uuid4()
            return result
        if kind == "rag_pipeline":
            known = dict(prompt_ids)
            for v in item.get("versions", []):
                pname = (v.get("config") or {}).get("prompt")
                if pname and pname.lower() not in known:
                    found = await self.prompts.find_prompt(actor, project_id, pname)
                    if found:
                        known[pname.lower()] = found
            return await self.rag.studio.import_pipeline(
                actor,
                project_id,
                item,
                body.policy,
                collections=collections,
                prompts=known,
                dry_run=dry,
                source=source,
                assume_models=assumed,
            )
        if kind == "model_alias":
            return await self._import_alias(actor, project_id, item, dry)
        if kind == "gateway_config":
            cfg = ProjectGatewayConfigIn.model_validate(
                {
                    k: v
                    for k, v in (item.get("config") or {}).items()
                    if k in ProjectGatewayConfigIn.model_fields
                }
            )
            if not dry:
                await self.gateway.catalog.set_project_config(actor, project_id, cfg)
            return {"kind": kind, "name": name or "gateway", "action": "updated"}
        if kind == "user_group":
            groups = {g.name.lower() for g in await self.tenancy.list_groups(project_id, actor.id)}
            if name.lower() in groups:
                return {"kind": kind, "name": name, "action": "reused"}
            if not dry:
                await self.tenancy.save_group(
                    actor, project_id, name[:80], str(item.get("description") or "")[:500]
                )
            return {
                "kind": kind,
                "name": name,
                "action": "created",
                "warnings": ["add members after the import"],
            }
        raise ValueError(f"unsupported kind {kind}")

    async def _import_alias(
        self, actor: Actor, project_id: uuid.UUID, item: dict[str, Any], dry: bool
    ) -> dict[str, Any]:
        name = str(item["name"])
        snap = self.gateway.registry.snapshot
        usable = {m.name.lower(): m.id for m in snap.models.values() if m.usable_by(project_id)}
        if not item.get("models"):
            return {
                "kind": "model_alias",
                "name": name,
                "action": "skipped",
                "warnings": ["the alias has no models"],
            }
        missing = [m for m in item.get("models", []) if str(m).lower() not in usable]
        if missing:
            return {
                "kind": "model_alias",
                "name": name,
                "action": "error",
                "error": "models not available in this project: " + ", ".join(missing),
            }
        if not dry:
            await self.gateway.catalog.set_alias(
                actor, project_id, name, [usable[str(m).lower()] for m in item["models"]]
            )
        return {"kind": "model_alias", "name": name, "action": "updated"}
