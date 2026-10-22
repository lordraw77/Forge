"""Export and import of project configuration and assets (PO request 2026-10-19).

A bundle is one JSON document:

    {"format": "forge.bundle", "schema_version": 1, "forge_version": "...", "exported_at": "...",
     "source": {"project": "..."}, "items": [...]}

Items: `prompt` and `rag_pipeline` (with the chosen versions and their tags), `data_collection`
(definition only, never files), and configuration: `model_alias` (project aliases, by model
name), `gateway_config` (project limits), `guardrail_policy` (rules, guard models by name),
`user_group` (names, never members). Secrets and document contents are never exported.

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
from forge.agents.service import AgentStudio
from forge.bundles import files as bundle_files
from forge.core.context import Actor
from forge.core.errors import ProblemError
from forge.gateway.service import Gateway, ProjectGatewayConfigIn
from forge.prompts.service import PromptStudio
from forge.rag.service import Rag
from forge.tenancy.service import TenancyService
from forge.usecases.service import UseCaseStudio, extract_refs

FORMAT = "forge.bundle"
SCHEMA_VERSION = 1
Scope = Literal["all", "published", "latest"]
Policy = Literal["new_version", "rename", "skip"]
CONFIG_KINDS = frozenset({"model_alias", "gateway_config", "user_group", "guardrail_policy"})
KINDS = frozenset(bundle_files.KIND_ORDER)

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
    agents: list[AssetRef] = Field(default_factory=list, max_length=500)
    use_cases: list[AssetRef] = Field(default_factory=list, max_length=500)
    collections: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    include_dependencies: bool = Field(
        default=True, description="Add the prompts and collections used by the pipelines"
    )
    model_aliases: bool = False
    gateway_config: bool = False
    user_groups: bool = False
    guardrail_policy: bool = False
    everything: bool = Field(
        default=False,
        description="Every prompt, RAG pipeline, agent, use case and collection of the project, "
        "with the versions chosen in `everything_versions` (for the CLI's Git export)",
    )
    everything_versions: Scope = "latest"


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


class ExportFilesIn(In):
    versions: Scope = "all"
    config: bool = Field(
        default=False, description="Also aliases, limits, groups and guardrails (project.manage)"
    )


class BundlePlanIn(In):
    bundle: dict[str, Any]


class BundleSyncIn(In):
    bundle: dict[str, Any]
    policy: Policy = "new_version"
    dry_run: bool = True


class PlanItem(BaseModel):
    kind: str
    name: str
    status: str  # new | changed | unchanged | differs | removed
    versions_to_send: int = 0


class BundlePlanOut(BaseModel):
    items: list[PlanItem]
    summary: dict[str, int]


class ImportReport(BaseModel):
    dry_run: bool
    policy: Policy
    schema_version: int
    migrated_from: int | None
    source: dict[str, Any]
    items: list[ImportItem]
    summary: dict[str, int]


class BundleSyncOut(BaseModel):
    plan: BundlePlanOut
    report: ImportReport | None  # None when there was nothing to send


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
    agents: AgentStudio
    usecases: UseCaseStudio

    # ------------------------------------------------------------ export
    async def _everything(self, actor: Actor, project_id: uuid.UUID, body: ExportIn) -> ExportIn:
        """The request with every asset of the project added to its lists."""
        scope = body.everything_versions

        async def pages(fetch: Any) -> list[uuid.UUID]:
            ids: list[uuid.UUID] = []
            cursor = None
            while True:
                page = await fetch(actor, project_id, cursor=cursor, limit=200)
                ids += [item.id for item in page.items]
                cursor = page.next_cursor
                if not cursor:
                    return ids

        prompts = await pages(self.prompts.list_prompts)
        pipelines = await pages(self.rag.studio.list_pipelines)
        agents = await pages(self.agents.list_agents)
        cases = [u.id for u in (await self.usecases.list_use_cases(actor, project_id)).items]
        collections = [c.id for c in await self.rag.collections.list_collections(actor, project_id)]
        return body.model_copy(
            update={
                "prompts": [AssetRef(id=i, versions=scope) for i in prompts],
                "rag_pipelines": [AssetRef(id=i, versions=scope) for i in pipelines],
                "agents": [AssetRef(id=i, versions=scope) for i in agents],
                "use_cases": [AssetRef(id=i, versions=scope) for i in cases],
                "collections": collections,
            }
        )

    async def export(
        self, actor: Actor, project_id: uuid.UUID, body: ExportIn, *, can_manage: bool
    ) -> dict[str, Any]:
        if (
            body.model_aliases or body.gateway_config or body.user_groups or body.guardrail_policy
        ) and not can_manage:
            raise ProblemError(403, "forbidden", "Exporting configuration needs project.manage")
        if body.everything:
            body = await self._everything(actor, project_id, body)
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
        agent_items: list[dict[str, Any]] = []
        exported_pipelines = {ref.id for ref in body.rag_pipelines}
        use_case_items: list[dict[str, Any]] = []
        agent_refs = list(body.agents)
        for ref in body.use_cases:
            item = await self.usecases.export_use_case(actor, project_id, ref.id, ref.versions)
            use_case_items.append(item)
            if body.include_dependencies:
                refs, _ = extract_refs(item["pattern"], item["definition"])
                for r in refs:
                    if r.kind != "agent":
                        continue
                    found = await self.agents.find_agent(actor, project_id, r.name)
                    if found and all(a.id != found for a in agent_refs):
                        agent_refs.append(AssetRef(id=found, versions="published"))
        for ref in agent_refs:
            item, agent_prompts, agent_pipelines = await self.agents.export_agent(
                actor, project_id, ref.id, ref.versions
            )
            agent_items.append(item)
            if body.include_dependencies:
                for pid in agent_prompts:
                    prompt_scope.setdefault(pid, ref.versions)
                for rid in agent_pipelines - exported_pipelines:
                    exported_pipelines.add(rid)
                    dep, cols, dep_prompts = await self.rag.studio.export_pipeline(
                        actor, project_id, rid, ref.versions
                    )
                    pipelines.append(dep)
                    collection_ids |= cols
                    for pid in dep_prompts:
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
        items += agent_items
        items += use_case_items
        if body.model_aliases:
            for a in await self.gateway.catalog.list_aliases(actor, project_id):
                if a.scope == "project" and a.model_names:
                    items.append({"kind": "model_alias", "name": a.name, "models": a.model_names})
        if body.gateway_config:
            cfg = await self.gateway.catalog.get_project_config(actor, project_id)
            items.append(
                {"kind": "gateway_config", "name": "gateway", "config": cfg.config.model_dump()}
            )
        if body.guardrail_policy:
            policy = await self.gateway.guardrails.get_policy(project_id)
            items.append(
                {
                    "kind": "guardrail_policy",
                    "name": "guardrails",
                    "enabled": policy.enabled,
                    "rules": [r.model_dump() for r in policy.rules],
                }
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
            "guardrail_policy",
            "data_collection",
            "prompt",
            "rag_pipeline",
            "agent",
            "use_case",
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
        pipeline_ids: dict[str, uuid.UUID] = {}
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
                    actor,
                    project_id,
                    item,
                    body,
                    source,
                    collections,
                    prompt_ids,
                    pipeline_ids,
                    assumed,
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
        pipeline_ids: dict[str, uuid.UUID],
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
            result = await self.rag.studio.import_pipeline(
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
            target = result.get("target") or name
            if result.get("id"):
                pipeline_ids[name.lower()] = uuid.UUID(result["id"])
            elif result["action"] == "skipped":
                existing = await self.rag.studio.find_pipeline(actor, project_id, target)
                if existing:
                    pipeline_ids[name.lower()] = existing
            elif dry and result["action"] != "error":
                pipeline_ids[name.lower()] = uuid.uuid4()
            return result
        if kind == "agent":
            known_prompts, known_pipelines = dict(prompt_ids), dict(pipeline_ids)
            for v in item.get("versions", []):
                cfg = v.get("config") or {}
                pname = str((cfg.get("prompt") or {}).get("name") or "")
                if pname and pname.lower() not in known_prompts:
                    found = await self.prompts.find_prompt(actor, project_id, pname)
                    if found:
                        known_prompts[pname.lower()] = found
                for t in cfg.get("tools") or []:
                    ref = str(t.get("pipeline") or "") if t.get("kind") == "rag" else ""
                    if ref and ref.lower() not in known_pipelines:
                        found_p = await self.rag.studio.find_pipeline(actor, project_id, ref)
                        if found_p:
                            known_pipelines[ref.lower()] = found_p
            return await self.agents.import_agent(
                actor,
                project_id,
                item,
                body.policy,
                prompts=known_prompts,
                pipelines=known_pipelines,
                dry_run=dry,
                source=source,
                assume_models=assumed,
            )
        if kind == "use_case":
            return await self.usecases.import_use_case(
                actor, project_id, item, body.policy, dry_run=dry, source=source
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
        if kind == "guardrail_policy":
            return await self.gateway.guardrails.import_policy(
                project_id, actor.id, item, dry_run=dry
            )
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

    # ---- bundles as files, and synchronisation with them (E3.3)
    async def export_files(
        self, actor: Actor, project_id: uuid.UUID, body: ExportFilesIn, *, can_manage: bool
    ) -> bytes:
        """The whole project as a zip of files in the Git layout."""
        bundle = await self.export(
            actor,
            project_id,
            ExportIn(
                everything=True,
                everything_versions=body.versions,
                model_aliases=body.config,
                gateway_config=body.config,
                user_groups=body.config,
                guardrail_policy=body.config,
            ),
            can_manage=can_manage,
        )
        return bundle_files.zip_files(bundle_files.to_files(bundle))

    @staticmethod
    def read_files(files: dict[str, bytes]) -> dict[str, Any]:
        """A bundle from the files of a folder or zip."""
        return bundle_files.from_files(bundle_files.strip_root(files))

    async def _plan(
        self, actor: Actor, project_id: uuid.UUID, bundle: dict[str, Any], *, can_manage: bool
    ) -> tuple[dict[str, Any], list[bundle_files.Change]]:
        local, _ = migrate(dict(bundle))
        with_config = can_manage and any(
            isinstance(i, dict) and i.get("kind") in bundle_files.CONFIG_KINDS
            for i in local["items"]
        )
        remote = await self.export(
            actor,
            project_id,
            ExportIn(
                everything=True,
                everything_versions="all",
                model_aliases=with_config,
                gateway_config=with_config,
                user_groups=with_config,
                guardrail_policy=with_config,
            ),
            can_manage=can_manage,
        )
        known = [i for i in local["items"] if isinstance(i, dict) and i.get("kind") in KINDS]
        return {**local, "items": known}, bundle_files.compare({**local, "items": known}, remote)

    @staticmethod
    def _plan_out(changes: list[bundle_files.Change]) -> BundlePlanOut:
        summary: dict[str, int] = {}
        for c in changes:
            summary[c.status] = summary.get(c.status, 0) + 1
        return BundlePlanOut(
            items=[
                PlanItem(
                    kind=c.kind,
                    name=c.name,
                    status=c.status,
                    versions_to_send=len(c.send or []),
                )
                for c in changes
            ],
            summary=summary,
        )

    async def plan(
        self, actor: Actor, project_id: uuid.UUID, body: BundlePlanIn, *, can_manage: bool
    ) -> BundlePlanOut:
        """What a sync with this bundle would send: new items, versions the project lacks."""
        _, changes = await self._plan(actor, project_id, body.bundle, can_manage=can_manage)
        return self._plan_out(changes)

    async def sync(
        self, actor: Actor, project_id: uuid.UUID, body: BundleSyncIn, *, can_manage: bool
    ) -> BundleSyncOut:
        """Plan, then import only the new items and the missing versions (idempotent)."""
        local, changes = await self._plan(actor, project_id, body.bundle, can_manage=can_manage)
        todo = bundle_files.only_changes(local, changes)
        report = None
        if todo["items"]:
            report = await self.import_(
                actor,
                project_id,
                ImportIn(bundle=todo, policy=body.policy, dry_run=body.dry_run),
                can_manage=can_manage,
            )
        return BundleSyncOut(plan=self._plan_out(changes), report=report)
