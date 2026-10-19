#!/usr/bin/env python3
"""Licence gate for shipped dependencies (backend runtime + frontend production), run in CI.

Policy: scripts/license-policy.json. A dependency passes when its licence (SPDX expression,
licence field or trove classifier) is allowed, or when it is listed as an exception with a
reason. Unknown or denied licences fail the build. LiteLLM must not ship its proprietary
`enterprise` package (spike E0.5d).

Usage: scripts/check-licenses.py [--backend] [--frontend] [--ingest-image IMAGE]
  (default: backend and frontend). `--ingest-image` checks the ingestion worker's extra
  dependencies (Docling, CPU PyTorch, ...) inside that built image, where they are installed.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / "scripts" / "license-policy.json").read_text())
ALLOWED = set(POLICY["allowed"])
ALIASES = {k.lower(): v for k, v in POLICY["aliases"].items()}
EXCEPTIONS = POLICY["exceptions"]

# Collected inside the backend virtualenv: name, version, licence hints.
_PY_PROBE = r"""
import json, sys
from importlib import metadata
from packaging.requirements import Requirement
out = {}
for line in json.load(sys.stdin):
    req = Requirement(line)
    name = req.name.lower()
    if req.marker is not None and not req.marker.evaluate():
        continue  # platform-specific (e.g. Windows only): not installed here, not shipped
    try:
        md = metadata.metadata(name)
    except metadata.PackageNotFoundError:
        out[name] = None
        continue
    out[name] = {
        "version": md.get("Version"),
        "expression": md.get("License-Expression"),
        "license": md.get("License"),
        "classifiers": [c for c in (md.get_all("Classifier") or []) if c.startswith("License ::")],
    }
import importlib.util
out["__litellm_enterprise__"] = importlib.util.find_spec("enterprise") is not None or (
    importlib.util.find_spec("litellm_enterprise") is not None
)
print(json.dumps(out))
"""

_CLASSIFIERS = {
    "MIT License": "MIT",
    "BSD License": "BSD-3-Clause",
    "Apache Software License": "Apache-2.0",
    "ISC License (ISCL)": "ISC",
    "Python Software Foundation License": "PSF-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
    "GNU Lesser General Public License v3 or later (LGPLv3+)": "LGPL-3.0-or-later",
    "GNU Library or Lesser General Public License (LGPL)": "LGPL-2.1-or-later",
    "The Unlicense (Unlicense)": "Unlicense",
    "GNU General Public License v3 (GPLv3)": "GPL-3.0-only",
    "GNU General Public License v2 (GPLv2)": "GPL-2.0-only",
    "GNU Affero General Public License v3": "AGPL-3.0-only",
}


def normalise(raw: str) -> str:
    value = raw.strip()
    return ALIASES.get(value.lower(), value)


def expression_ok(expr: str) -> bool:
    """`A OR B` passes if any branch passes; `A AND B` needs all; parentheses are flattened."""
    expr = normalise(expr)
    if expr in ALLOWED:
        return True
    expr = expr.replace("(", " ").replace(")", " ")
    for branch in re.split(r"\s+OR\s+", expr):
        terms = [normalise(t) for t in re.split(r"\s+AND\s+", branch.strip()) if t.strip()]
        terms = [re.sub(r"\s+WITH\s+.*$", "", t) for t in terms]
        if terms and all(t in ALLOWED for t in terms):
            return True
    return False


def python_licence(info: dict) -> str | None:
    """First candidate that the policy accepts, else the most specific one for the report:
    SPDX expression, short licence field, trove classifiers, first line of a licence text."""
    lic = (info.get("license") or "").strip()
    names = [c.split(" :: ")[-1] for c in info.get("classifiers", [])]
    mapped = sorted({_CLASSIFIERS[n] for n in names if n in _CLASSIFIERS})
    candidates = [
        info.get("expression"),
        lic if lic and len(lic) < 80 and "\n" not in lic else None,
        " OR ".join(mapped) if mapped else None,
        lic.splitlines()[0].strip() if lic else None,
    ]
    present = [str(c) for c in candidates if c and c.upper() != "UNKNOWN"]
    for c in present:
        if expression_ok(c):
            return c
    return present[0][:120] if present else None


def check(kind: str, name: str, version: str, licence: str | None, problems: list[str]) -> str:
    exception = EXCEPTIONS.get(f"{kind}:{name}")
    if exception and str(version).startswith(exception.get("version_prefix", "")):
        return f"exception ({exception['license']}: {exception['reason']})"
    if licence and expression_ok(licence):
        return licence
    problems.append(f"{kind} {name} {version}: licence {licence or 'unknown'} is not allowed")
    return licence or "unknown"


def backend(problems: list[str], image: str | None = None) -> int:
    extra = ["--extra", "ingest"] if image else []
    req = subprocess.run(
        ["uv", "export", "--frozen", "--no-dev", "--no-hashes", "--no-emit-project",
         "--no-header", "--no-annotate", *extra],
        cwd=ROOT / "backend", check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    lines = [ln.strip() for ln in req.splitlines() if ln.strip() and not ln.startswith(("#", "-"))]
    # Index URLs (PyTorch CPU wheels) are not requirements.
    lines = [ln.split(" @ ")[0] if " @ " in ln else ln for ln in lines]
    if image:
        cmd = ["docker", "run", "--rm", "-i", "--network", "none", "--entrypoint", "python",
               image, "-c", _PY_PROBE]
    else:
        cmd = ["uv", "run", "--frozen", "--no-dev", "python", "-c", _PY_PROBE]
    probe = subprocess.run(
        cmd, cwd=ROOT / "backend", input=json.dumps(lines), check=True, capture_output=True,
        text=True,
    )  # fmt: skip
    found = json.loads(probe.stdout)
    where = image or "the virtualenv (run uv sync)"
    if found.pop("__litellm_enterprise__"):
        problems.append("backend litellm: proprietary `enterprise` package is installed")
    for name, info in sorted(found.items()):
        if info is None:
            problems.append(f"backend {name}: not installed in {where}")
            continue
        check("python", name, info["version"], python_licence(info), problems)
    return len(found)


def frontend(problems: list[str]) -> int:
    lock = json.loads((ROOT / "frontend" / "package-lock.json").read_text())
    count = 0
    for path, pkg in sorted(lock["packages"].items()):
        if not path or pkg.get("dev") or pkg.get("devOptional") or pkg.get("link"):
            continue
        name = path.split("node_modules/")[-1]
        licence = pkg.get("license")
        if isinstance(licence, dict):
            licence = licence.get("type")
        check("npm", name, pkg.get("version", "?"), licence, problems)
        count += 1
    return count


def main() -> int:
    argv = sys.argv[1:]
    image = None
    if "--ingest-image" in argv:
        i = argv.index("--ingest-image")
        image = argv[i + 1]
        del argv[i : i + 2]
    args = set(argv) or ({"--backend", "--frontend"} if image is None else set())
    problems: list[str] = []
    if "--backend" in args:
        print(f"backend: {backend(problems)} runtime packages checked")
    if image:
        print(f"ingest image: {backend(problems, image)} runtime packages checked")
    if "--frontend" in args:
        print(f"frontend: {frontend(problems)} production packages checked")
    for p in problems:
        print(f"LICENCE: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
