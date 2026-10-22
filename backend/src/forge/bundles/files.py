"""Bundles as files, and the comparison of files with a project (E3.3).

One implementation for the command line (`forgectl`) and the web interface, so both give the same
files and the same answers. Pure functions: no database, no I/O.

Layout (a zip, or a directory in Git)::

    forge.json                    format, schema version, Forge version, source project
    prompts/<name>.json           one file per item, grouped by kind
    rag_pipelines/<name>.json
    ...

Volatile fields (export time, version timestamps) are left out, so exporting twice gives
identical files. Items are matched by kind and name; versions by content.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from forge.core.errors import ProblemError

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
CONFIG_KINDS = frozenset({"model_alias", "gateway_config", "user_group", "guardrail_policy"})
TIME_KEYS = frozenset({"created_at", "updated_at", "exported_at", "published_at"})
# Never compared: they differ with every import and between environments.
VOLATILE = TIME_KEYS | {"published_by", "version", "note"}
MAX_FILES = 5000
MAX_UNZIPPED = 50 * 1024 * 1024


def _rank(kind: str) -> int:
    return KIND_ORDER.index(kind) if kind in KIND_ORDER else len(KIND_ORDER)


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")[:80] or "item"


def dump(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _without_times(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _without_times(v) for k, v in value.items() if k not in TIME_KEYS}
    if isinstance(value, list):
        return [_without_times(v) for v in value]
    return value


# ---- bundle <-> files
def to_files(bundle: dict[str, Any]) -> dict[str, str]:
    """Relative path -> file text."""
    items = sorted(
        bundle["items"], key=lambda i: (_rank(i["kind"]), i["kind"], str(i["name"]).lower())
    )
    files = {
        MANIFEST: dump(
            {
                "format": bundle["format"],
                "schema_version": bundle["schema_version"],
                "forge_version": bundle.get("forge_version"),
                "source": bundle.get("source", {}),
            }
        )
    }
    taken: set[str] = set()
    for item in items:
        folder = FOLDERS.get(item["kind"], _slug(item["kind"]))
        stem = _slug(str(item["name"]))
        path, n = f"{folder}/{stem}.json", 2
        while path.lower() in taken:
            path, n = f"{folder}/{stem}-{n}.json", n + 1
        taken.add(path.lower())
        files[path] = dump(_without_times(item))
    return files


def is_item_path(path: str) -> bool:
    return path != MANIFEST and path.split("/")[0] in FOLDERS.values() and path.endswith(".json")


def from_files(files: Mapping[str, bytes | str]) -> dict[str, Any]:
    """The bundle described by the files of a directory or zip (paths relative to its root)."""

    def load(path: str) -> Any:
        raw = files[path]
        try:
            return json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        except ValueError as exc:
            raise ProblemError(422, "bad_file", f"{path} is not valid JSON") from exc

    if MANIFEST not in files:
        raise ProblemError(422, "not_an_export", f"No {MANIFEST}: this is not a Forge export")
    manifest = load(MANIFEST)
    if not isinstance(manifest, dict) or manifest.get("format") != "forge.bundle":
        raise ProblemError(422, "not_an_export", f"{MANIFEST} is not a Forge bundle manifest")
    items: list[dict[str, Any]] = []
    for path in sorted(p for p in files if is_item_path(p)):
        item = load(path)
        if not isinstance(item, dict) or "kind" not in item or "name" not in item:
            raise ProblemError(422, "bad_file", f"{path} is not a bundle item (kind and name)")
        items.append(item)
    items.sort(key=lambda i: (_rank(i["kind"]), i["kind"], str(i["name"]).lower()))
    return {**manifest, "items": items}


def zip_files(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(files):
            info = zipfile.ZipInfo(path, date_time=(2020, 1, 1, 0, 0, 0))  # same bytes every time
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, files[path])
    return buffer.getvalue()


def unzip_files(data: bytes) -> dict[str, bytes]:
    """Files of a zip, safely: no absolute or `..` paths, bounded count and size."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ProblemError(422, "bad_zip", "The file is not a valid zip") from exc
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_UNZIPPED:
        raise ProblemError(413, "zip_too_large", "The zip holds too many or too large files")
    out: dict[str, bytes] = {}
    for info in infos:
        path = normalise_path(info.filename)
        if path is not None:
            out[path] = zf.read(info)
    return out


def normalise_path(name: str) -> str | None:
    """A relative POSIX path without `..`; None for junk such as `__MACOSX/` or dotfiles."""
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        raise ProblemError(422, "bad_path", f"Unsafe path in the upload: {name}")
    if parts[0] == "__MACOSX" or any(p.startswith(".") for p in parts):
        return None
    return "/".join(parts)


def strip_root(files: Mapping[str, bytes]) -> dict[str, bytes]:
    """A folder picked in a browser (or zipped by hand) carries its own name as first path
    segment: when `forge.json` sits one level down, that level is the root."""
    if MANIFEST in files:
        return dict(files)
    roots = {p.split("/")[0] for p in files if "/" in p}
    if len(roots) == 1:
        root = next(iter(roots))
        if f"{root}/{MANIFEST}" in files:
            return {p[len(root) + 1 :]: v for p, v in files.items() if p.startswith(root + "/")}
    return dict(files)


# ---- comparison
def _walk(v: Any) -> Any:
    if isinstance(v, dict):
        return {
            k: _walk(x)
            for k, x in v.items()
            if k not in VOLATILE and not k.endswith("_version") and k not in ("tags", "warnings")
        }
    return [_walk(x) for x in v] if isinstance(v, list) else v


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
    """Files against the project, version by version.

    A version of a file is *missing* when no version of the project item has the same content
    (numbers, notes and timestamps do not count). Missing versions are sent in order. If the
    last version of the file differs from the project's last one it is sent even when an older
    project version has the same content, so a revert in Git becomes a new version.
    `removed` (in the project, not in the files) is reported and never deleted."""
    there = {(i["kind"], str(i["name"]).lower()): i for i in remote["items"]}
    here = {(i["kind"], str(i["name"]).lower()): i for i in local["items"]}
    changes: list[Change] = []
    for key, item in here.items():
        other = there.get(key)
        name = str(item["name"])
        mine = item.get("versions")
        if other is None:
            changes.append(Change(item["kind"], name, "new"))
            continue
        body_same = _walk({k: v for k, v in item.items() if k != "versions"}) == _walk(
            {k: v for k, v in other.items() if k != "versions"}
        )
        if not isinstance(mine, list) or not mine:  # configuration items
            changes.append(Change(item["kind"], name, "unchanged" if body_same else "changed"))
            continue
        theirs = other.get("versions") or []
        known = {_hash(v) for v in theirs}
        send = [i for i, v in enumerate(mine) if _hash(v) not in known]
        if theirs and _hash(mine[-1]) != _hash(theirs[-1]) and len(mine) - 1 not in send:
            send.append(len(mine) - 1)
        status = "changed" if send else ("unchanged" if body_same else "differs")
        changes.append(Change(item["kind"], name, status, sorted(send)))
    for key, item in there.items():
        if key not in here:
            changes.append(Change(item["kind"], str(item["name"]), "removed"))
    changes.sort(key=lambda c: (_rank(c.kind), c.kind, c.name.lower()))
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
