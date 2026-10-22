"""A Forge export as a directory: the zip the server builds, written to disk (and back).

The layout, the comparison with the project and the import are done by the server, so the
command line and the web interface behave the same. This module only moves files:

    forge.json                    format, schema version, Forge version, source project
    prompts/<name>.json           one file per item, grouped by kind
    rag_pipelines/<name>.json
    ...
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

MANIFEST = "forge.json"
FOLDERS = frozenset(
    {
        "collections",
        "prompts",
        "rag_pipelines",
        "agents",
        "use_cases",
        "model_aliases",
        "gateway",
        "guardrails",
        "user_groups",
    }
)


class TreeError(Exception):
    """A directory that is not a Forge export, or an unsafe zip."""


def extract(data: bytes, directory: Path) -> list[str]:
    """Write the files of an export zip into `directory`, touching only files whose content
    changed, and remove files of items that no longer exist (only inside the known folders, so
    a README or a CI file next to them is safe). Returns the relative paths written."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise TreeError("The server did not return a valid zip") from exc
    wanted: dict[str, bytes] = {}
    for info in zf.infolist():
        parts = info.filename.split("/")
        if info.is_dir() or info.filename.startswith("/") or ".." in parts:
            if not info.is_dir():
                raise TreeError(f"Unsafe path in the zip: {info.filename}")
            continue
        wanted[info.filename] = zf.read(info)
    directory.mkdir(parents=True, exist_ok=True)
    for rel, content in wanted.items():
        target = directory / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_bytes() != content:
            target.write_bytes(content)
    for old in directory.rglob("*.json"):
        rel = old.relative_to(directory).as_posix()
        if rel not in wanted and rel.split("/")[0] in FOLDERS:
            old.unlink()
    return sorted(wanted)


def zip_directory(directory: Path) -> bytes:
    """The export files of `directory` as one zip, for the server to read."""
    if not (directory / MANIFEST).is_file():
        raise TreeError(f"{directory} has no {MANIFEST}: not a Forge export")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(directory / MANIFEST, MANIFEST)
        for path in sorted(directory.rglob("*.json")):
            rel = path.relative_to(directory).as_posix()
            if rel != MANIFEST and rel.split("/")[0] in FOLDERS:
                zf.write(path, rel)
    return buffer.getvalue()
