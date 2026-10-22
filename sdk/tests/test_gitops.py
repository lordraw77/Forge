from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from forge_sdk import gitops


def make_zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for path, text in files.items():
            zf.writestr(path, text)
    return buffer.getvalue()


def test_extract_writes_only_changed_files(tmp_path: Path) -> None:
    data = make_zip({"forge.json": "{}", "prompts/A.json": "a"})
    assert gitops.extract(data, tmp_path) == ["forge.json", "prompts/A.json"]
    mtime = (tmp_path / "prompts" / "A.json").stat().st_mtime_ns
    gitops.extract(data, tmp_path)
    assert (tmp_path / "prompts" / "A.json").stat().st_mtime_ns == mtime  # untouched


def test_extract_refuses_unsafe_paths(tmp_path: Path) -> None:
    with pytest.raises(gitops.TreeError, match="Unsafe path"):
        gitops.extract(make_zip({"../evil.json": "x"}), tmp_path)
    with pytest.raises(gitops.TreeError, match="valid zip"):
        gitops.extract(b"nope", tmp_path)


def test_zip_directory_takes_only_export_files(tmp_path: Path) -> None:
    with pytest.raises(gitops.TreeError, match="not a Forge export"):
        gitops.zip_directory(tmp_path)
    (tmp_path / "forge.json").write_text("{}")
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "A.json").write_text("a")
    (tmp_path / "prompts" / "notes.txt").write_text("x")
    (tmp_path / "package.json").write_text("{}")
    with zipfile.ZipFile(io.BytesIO(gitops.zip_directory(tmp_path))) as zf:
        assert sorted(zf.namelist()) == ["forge.json", "prompts/A.json"]
