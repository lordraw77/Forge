"""Bundles as files and the comparison with a project (E3.3): the logic behind `forgectl` and the
Sync page."""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import pytest

from forge.bundles import files as bf
from forge.core.errors import ProblemError


def prompt(name: str = "Support", text: str = "Hello {name}", version: int = 3) -> dict[str, Any]:
    return {
        "kind": "prompt",
        "name": name,
        "description": "d",
        "versions": [
            {
                "version": version,
                "model": "default-chat",
                "system_template": "You help.",
                "user_template": text,
                "variables": [],
                "examples": [],
                "model_params": {},
                "note": "n",
                "created_at": "2026-10-21T10:00:00+00:00",
            }
        ],
        "tags": {"prod": version},
    }


def history(name: str, *texts: str) -> dict[str, Any]:
    item = prompt(name, texts[0], 1)
    item["versions"] = [
        {**item["versions"][0], "version": n, "user_template": t} for n, t in enumerate(texts, 1)
    ]
    return item


def bundle(*items: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": "forge.bundle",
        "schema_version": 1,
        "forge_version": "0.1.0",
        "exported_at": "2026-10-21T10:00:00+00:00",
        "source": {"project": "P"},
        "items": list(items),
    }


# ---- files
def test_files_round_trip_and_are_stable() -> None:
    b = bundle(
        {"kind": "data_collection", "name": "Docs", "description": ""},
        prompt("Support"),
        prompt("Sales / EU"),
    )
    files = bf.to_files(b)
    assert sorted(files) == [
        "collections/Docs.json",
        "forge.json",
        "prompts/Sales-EU.json",
        "prompts/Support.json",
    ]
    assert "created_at" not in files["prompts/Support.json"]
    assert bf.to_files(b | {"exported_at": "2027-01-01T00:00:00+00:00"}) == files
    back = bf.from_files(files)
    assert [(i["kind"], i["name"]) for i in back["items"]] == [
        ("data_collection", "Docs"),
        ("prompt", "Sales / EU"),
        ("prompt", "Support"),
    ]


def test_names_that_slug_alike_get_distinct_files() -> None:
    files = bf.to_files(bundle(prompt("A b"), prompt("A-b")))
    assert len([p for p in files if p.startswith("prompts/")]) == 2


def test_zip_is_deterministic_and_round_trips() -> None:
    files = bf.to_files(bundle(prompt("A")))
    data = bf.zip_files(files)
    assert bf.zip_files(files) == data
    assert {p: v.decode() for p, v in bf.unzip_files(data).items()} == files


def test_a_picked_folder_name_is_stripped_and_junk_ignored() -> None:
    files = {f"myrepo/{p}": v.encode() for p, v in bf.to_files(bundle(prompt("A"))).items()}
    files["myrepo/README.md"] = b"x"
    files["myrepo/prompts/notes.txt"] = b"x"
    assert [i["name"] for i in bf.from_files(bf.strip_root(files))["items"]] == ["A"]


def test_bad_uploads_are_refused() -> None:
    with pytest.raises(ProblemError) as err:
        bf.from_files({})
    assert err.value.code == "not_an_export"
    with pytest.raises(ProblemError) as err:
        bf.from_files({"forge.json": json.dumps({"format": "other"})})
    assert err.value.code == "not_an_export"
    ok = {"forge.json": json.dumps({"format": "forge.bundle"}), "prompts/bad.json": "{nope"}
    with pytest.raises(ProblemError) as err:
        bf.from_files(ok)
    assert err.value.code == "bad_file"
    with pytest.raises(ProblemError) as err:
        bf.unzip_files(b"not a zip")
    assert err.value.code == "bad_zip"


def test_zip_slip_is_refused() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("../evil.json", "{}")
    with pytest.raises(ProblemError) as err:
        bf.unzip_files(buffer.getvalue())
    assert err.value.code == "bad_path"
    assert bf.normalise_path("__MACOSX/x") is None
    assert bf.normalise_path(".git/config") is None


# ---- comparison
def test_compare_ignores_versions_notes_timestamps_and_tags() -> None:
    remote = bundle(prompt("Support", version=7), prompt("Gone"))
    local = bundle(prompt("Support", version=1), prompt("Fresh"))
    local["items"][0]["tags"] = {}
    local["items"][0]["versions"][0]["note"] = "other"
    status = {c.name: c.status for c in bf.compare(local, remote)}
    assert status == {"Support": "unchanged", "Fresh": "new", "Gone": "removed"}


def test_only_the_missing_versions_are_sent() -> None:
    remote = bundle(history("A", "v1", "v2"))
    local = bundle(history("A", "v1", "v2", "v3"))
    (change,) = bf.compare(local, remote)
    assert (change.status, change.send) == ("changed", [2])
    sent = bf.only_changes(local, [change])["items"][0]["versions"]
    assert [v["user_template"] for v in sent] == ["v3"]


def test_version_numbers_do_not_matter_only_content() -> None:
    remote = bundle(history("A", "x", "v1", "v2"))
    local = bundle(history("A", "v1", "v2"))
    (change,) = bf.compare(local, remote)
    assert change.status == "unchanged"


def test_a_revert_in_git_becomes_a_new_version() -> None:
    remote = bundle(history("A", "v1", "v2"))
    local = bundle(history("A", "v1"))
    (change,) = bf.compare(local, remote)
    assert (change.status, change.send) == ("changed", [0])


def test_a_new_item_goes_with_its_whole_history() -> None:
    local = bundle(history("N", "a", "b", "c"))
    changes = bf.compare(local, bundle())
    assert changes[0].status == "new"
    assert len(bf.only_changes(local, changes)["items"][0]["versions"]) == 3


def test_a_description_change_alone_is_reported_not_sent() -> None:
    remote = bundle(history("A", "v1"))
    local = bundle(history("A", "v1"))
    local["items"][0]["description"] = "A new description"
    (change,) = bf.compare(local, remote)
    assert change.status == "differs"
    assert bf.only_changes(local, [change])["items"] == []


def test_configuration_items_are_compared_whole() -> None:
    alias = {"kind": "model_alias", "name": "fast", "models": ["a", "b"]}
    same = bf.compare(bundle(alias), bundle(dict(alias)))
    assert same[0].status == "unchanged"
    changed = bf.compare(bundle(alias | {"models": ["a"]}), bundle(alias))
    assert changed[0].status == "changed"
