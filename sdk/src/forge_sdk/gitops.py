"""Bundles as files: one JSON file per item, stable enough to review in a Git diff, and a
comparison with what the project holds now so that `apply` sends only what changed.

Layout of a directory (`forgectl export DIR`)::

    forge.json                    format, schema version, Forge version, source project
    prompts/<name>.json           one file per item, grouped by kind
    rag_pipelines/<name>.json
    ...

Volatile fields (export time, version timestamps) are left out so that exporting twice gives
identical files.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MANIFEST = "forge.json"
# Dependencies first: an import resolves names, so what is referenced comes before who references.
KIND_ORDER = (
    "data_collection",
    "prompt",
    "rag_pipeline",
    "agent",
    "use_case",
    "model_alias",
    "gateway_config",
    "guardrail_policy",
    "user_group",
)
# Never written to files and never compared.
VOLATILE = frozenset(
    {"created_at", "updated_at", "exported_at", "published_at", "published_by", "version", "note"}
)
FOLDERS = {
    "data_collection": "collections",
    "prompt": "prompts",
    "rag_pipeline": "rag_pipelines",
    "agent": "agents",
    "use_case": "use_cases",
    "model_alias": "model_aliases",
    "gateway_config": "gateway",
    "guardrail_policy": "guardrails",
    "user_group": "user_groups",
}


class TreeError(Exception):
    """A directory that is not a Forge export, or a file that cannot be read."""


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")[:80] or "item"


def _clean(value: Any) -> Any:
    """Drop volatile keys at every level (version numbers inside versions are positional)."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def dump(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def item_paths(items: list[dict[str, Any]]) -> dict[int, str]:
    """A relative path per item (by position), unique even when two names slug alike."""
    taken: set[str] = set()
    paths: dict[int, str] = {}
    for i, item in enumerate(items):
        folder = FOLDERS.get(item["kind"], _slug(item["kind"]))
        path = f"{folder}/{_slug(item['name'])}.json"
        n = 2
        while path.lower() in taken:
            path = f"{folder}/{_slug(item['name'])}-{n}.json"
            n += 1
        taken.add(path.lower())
        paths[i] = path
    return paths


def write_tree(bundle: dict[str, Any], directory: Path) -> list[str]:
    """Write the bundle as files; files of items that no longer exist are removed.
    Returns the relative paths written."""
    items = sorted(
        bundle["items"], key=lambda i: (_kind_rank(i["kind"]), i["kind"], i["name"].lower())
    )
    manifest = {
        "format": bundle["format"],
        "schema_version": bundle["schema_version"],
        "forge_version": bundle.get("forge_version"),
        "source": bundle.get("source", {}),
    }
    directory.mkdir(parents=True, exist_ok=True)
    wanted = {MANIFEST: dump(manifest)}
    for i, rel in item_paths(items).items():
        wanted[rel] = dump(_strip_times(items[i]))
    for rel, text in wanted.items():
        target = directory / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_text(encoding="utf-8") != text:
            target.write_text(text, encoding="utf-8")
    for old in directory.rglob("*.json"):
        rel = old.relative_to(directory).as_posix()
        if rel not in wanted and (rel.split("/")[0] in FOLDERS.values()):
            old.unlink()
    return sorted(wanted)


def _strip_times(item: dict[str, Any]) -> dict[str, Any]:
    """Keep version numbers and notes (they explain the history) but not timestamps."""

    def walk(v: Any) -> Any:
        if isinstance(v, dict):
            return {
                k: walk(x)
                for k, x in v.items()
                if k not in ("created_at", "updated_at", "exported_at", "published_at")
            }
        return [walk(x) for x in v] if isinstance(v, list) else v

    out = walk(item)
    assert isinstance(out, dict)
    return out


def _kind_rank(kind: str) -> int:
    return KIND_ORDER.index(kind) if kind in KIND_ORDER else len(KIND_ORDER)


def read_tree(directory: Path) -> dict[str, Any]:
    """The bundle described by a directory written by `write_tree` (or by hand)."""
    manifest_file = directory / MANIFEST
    if not manifest_file.is_file():
        raise TreeError(f"{directory} has no {MANIFEST}: not a Forge export")
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise TreeError(f"{MANIFEST} is not valid JSON: {exc}") from exc
    if manifest.get("format") != "forge.bundle":
        raise TreeError(f"{MANIFEST} is not a Forge bundle manifest")
    items: list[dict[str, Any]] = []
    for path in sorted(directory.rglob("*.json")):
        rel = path.relative_to(directory).as_posix()
        if rel == MANIFEST or rel.split("/")[0] not in FOLDERS.values():
            continue
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise TreeError(f"{rel} is not valid JSON: {exc}") from exc
        if not isinstance(item, dict) or "kind" not in item or "name" not in item:
            raise TreeError(f"{rel} is not a bundle item (needs `kind` and `name`)")
        items.append(item)
    items.sort(key=lambda i: (_kind_rank(i["kind"]), i["kind"], str(i["name"]).lower()))
    return {**manifest, "items": items}


# ---- comparison
def _walk(v: Any) -> Any:
    if isinstance(v, dict):
        return {
            k: _walk(x)
            for k, x in v.items()
            if k not in VOLATILE and not k.endswith("_version") and k not in ("tags", "warnings")
        }
    return [_walk(x) for x in v] if isinstance(v, list) else v


def comparable(item: dict[str, Any]) -> Any:
    """What makes two items "the same": everything but version numbers, notes, timestamps, tags
    or fields ending in `_version` (pins differ in time and between environments)."""
    return _walk(item)


def _hash(version: dict[str, Any]) -> str:
    return json.dumps(_walk(version), sort_keys=True, ensure_ascii=False)


@dataclass
class Change:
    kind: str
    name: str
    # new | changed | unchanged | differs | removed. `differs`: only fields outside the versions
    # (description, draft) differ; an import cannot update them, so it is reported, not sent.
    status: str
    send: list[int] | None = None  # positions of the versions to send; None = the whole item


def compare(local: dict[str, Any], remote: dict[str, Any]) -> list[Change]:
    """Local files against the project, version by version.

    A version of a file is *missing* when no version of the project item has the same content
    (numbers, notes and timestamps do not count). Missing versions are sent in order. If the
    last version of the file differs from the project's last one, it is sent even when an older
    project version has the same content, so a revert in Git becomes a new version.
    `removed` (in the project, not in the files) is reported and never deleted."""
    there = {(i["kind"], str(i["name"]).lower()): i for i in remote["items"]}
    here = {(i["kind"], str(i["name"]).lower()): i for i in local["items"]}
    changes: list[Change] = []
    for key, item in here.items():
        other = there.get(key)
        mine = item.get("versions")
        if other is None:
            changes.append(Change(item["kind"], str(item["name"]), "new"))
            continue
        body_same = _walk({k: v for k, v in item.items() if k != "versions"}) == _walk(
            {k: v for k, v in other.items() if k != "versions"}
        )
        if not isinstance(mine, list) or not mine:  # configuration items
            status = "unchanged" if body_same else "changed"
            changes.append(Change(item["kind"], str(item["name"]), status))
            continue
        theirs = other.get("versions") or []
        known = {_hash(v) for v in theirs}
        send = [i for i, v in enumerate(mine) if _hash(v) not in known]
        if theirs and _hash(mine[-1]) != _hash(theirs[-1]) and len(mine) - 1 not in send:
            send.append(len(mine) - 1)
        status = "changed" if send else ("unchanged" if body_same else "differs")
        changes.append(Change(item["kind"], str(item["name"]), status, sorted(send)))
    for key, item in there.items():
        if key not in here:
            changes.append(Change(item["kind"], str(item["name"]), "removed"))
    changes.sort(key=lambda c: (_kind_rank(c.kind), c.kind, c.name.lower()))
    return changes


def only_changes(local: dict[str, Any], changes: list[Change]) -> dict[str, Any]:
    """The bundle with just the new and changed items; of a changed item only the versions to
    send (a new item goes with its whole history)."""
    send = {(c.kind, c.name.lower()): c for c in changes if c.status in ("new", "changed")}
    items: list[dict[str, Any]] = []
    for item in local["items"]:
        change = send.get((item["kind"], str(item["name"]).lower()))
        if change is None:
            continue
        if change.status == "changed" and change.send is not None and item.get("versions"):
            item = {**item, "versions": [item["versions"][i] for i in change.send]}
        items.append(item)
    return {**local, "items": items}
