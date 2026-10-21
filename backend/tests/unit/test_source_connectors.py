"""Source connectors that need no services: git (local repository) and the safety rules."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from forge.rag.sources.connectors import (
    FilesystemConfig,
    GitConfig,
    GitConnector,
    SourceContext,
    SourceError,
    build,
    validate_config,
)


async def collect(connector) -> list:  # type: ignore[no-untyped-def]
    return [i async for i in connector.items()]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
async def test_git_clones_and_skips_dot_git(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "guide.md").write_text("# Guide\n\ntext")
    (repo / "main.py").write_text("print(1)")
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
    }
    for cmd in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "x"]):
        subprocess.run(["git", *cmd], cwd=repo, env=env, check=True)  # noqa: S603, S607, ASYNC221

    class Local(GitConnector):
        async def _check_repo(self, url: str) -> None:  # the https rule is tested below
            return None

    conn = Local(
        GitConfig(repo_url=str(repo), include=["*.md"], subpath="docs"), SourceContext([], False)
    )
    try:
        items = await collect(conn)
    finally:
        await conn.close()
    assert [(i.external_id, i.name, i.folder) for i in items] == [
        ("docs/guide.md", "guide.md", "docs")
    ]


async def test_git_refuses_non_https() -> None:
    conn = build(
        "git", {"repo_url": "http://example.com/r.git"}, SourceContext(["example.com"], False)
    )
    with pytest.raises(SourceError, match="https"):
        await collect(conn)


async def test_network_connectors_obey_the_host_allowlist() -> None:
    conn = build(
        "rest", {"url": "https://api.example.com/x", "id_field": "id"}, SourceContext([], False)
    )
    with pytest.raises(SourceError, match="host_not_allowed"):
        await collect(conn)


async def test_filesystem_needs_an_enabled_root_and_ignores_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "share"
    root.mkdir()
    (root / "a.txt").write_text("a")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    (root / "link.txt").symlink_to(outside)
    no_root = build("filesystem", {"path": str(root)}, SourceContext([], False))
    with pytest.raises(SourceError, match="FORGE_SOURCE_ROOTS"):
        await collect(no_root)
    elsewhere = build("filesystem", {"path": str(tmp_path)}, SourceContext([], False, roots=[root]))
    with pytest.raises(SourceError, match="outside"):
        await collect(elsewhere)
    ok = build("filesystem", {"path": str(root)}, SourceContext([], False, roots=[root]))
    assert [i.external_id for i in await collect(ok)] == ["a.txt"]


def test_config_validation() -> None:
    assert FilesystemConfig.model_validate({"path": "/x"}).recursive is True
    with pytest.raises(ValueError, match="regular expression"):
        validate_config("url", {"start_urls": ["https://a.example"], "include_regex": "("})
    with pytest.raises(ValueError, match="extra"):
        validate_config("rest", {"url": "https://a.example", "id_field": "id", "extra": 1})


def test_source_roots_are_bound_to_projects() -> None:
    import uuid

    from forge.core.settings import Settings
    from forge.rag.service import roots_for, source_roots

    a, b = uuid.uuid4(), uuid.uuid4()
    settings = Settings(
        source_roots=f"/mnt/hr={a};/mnt/pub=*;/mnt/open;/mnt/bad=not-a-uuid;/mnt/two={a},{b}",
        master_key="a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2V5a2U=",
    )
    roots = source_roots(settings)
    assert [str(r.path) for r in roots] == ["/mnt/hr", "/mnt/pub", "/mnt/two"]  # no `=`: ignored
    assert [str(p) for p in roots_for(roots, a)] == ["/mnt/hr", "/mnt/pub", "/mnt/two"]
    assert [str(p) for p in roots_for(roots, b)] == ["/mnt/pub", "/mnt/two"]
    assert [str(p) for p in roots_for(roots, uuid.uuid4())] == ["/mnt/pub"]
