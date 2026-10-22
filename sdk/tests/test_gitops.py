from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from forge_sdk import gitops
from tests_support import bundle, prompt


def test_files_round_trip_and_are_stable(tmp_path: Path) -> None:
    b = bundle(
        {"kind": "data_collection", "name": "Docs", "description": ""},
        prompt("Support"),
        prompt("Sales / EU"),
    )
    written = gitops.write_tree(b, tmp_path)
    assert written == [
        "collections/Docs.json",
        "forge.json",
        "prompts/Sales-EU.json",
        "prompts/Support.json",
    ]
    first = {p: (tmp_path / p).read_text() for p in written}
    # Exporting again with a later export time changes no file.
    gitops.write_tree(b | {"exported_at": "2027-01-01T00:00:00+00:00"}, tmp_path)
    assert {p: (tmp_path / p).read_text() for p in written} == first
    assert "created_at" not in first["prompts/Support.json"]
    back = gitops.read_tree(tmp_path)
    assert [(i["kind"], i["name"]) for i in back["items"]] == [
        ("data_collection", "Docs"),
        ("prompt", "Sales / EU"),
        ("prompt", "Support"),
    ]


def test_removed_items_lose_their_files(tmp_path: Path) -> None:
    gitops.write_tree(bundle(prompt("A"), prompt("B")), tmp_path)
    (tmp_path / "notes.json").write_text("{}")  # not ours: left alone
    gitops.write_tree(bundle(prompt("A")), tmp_path)
    assert not (tmp_path / "prompts/B.json").exists()
    assert (tmp_path / "notes.json").exists()


def test_names_that_slug_alike_get_distinct_files(tmp_path: Path) -> None:
    written = gitops.write_tree(bundle(prompt("A b"), prompt("A-b")), tmp_path)
    assert len([w for w in written if w.startswith("prompts/")]) == 2


def test_compare_ignores_versions_notes_timestamps_and_tags() -> None:
    remote = bundle(prompt("Support", version=7), prompt("Gone"))
    local = bundle(prompt("Support", version=1), prompt("Fresh"))
    local["items"][0]["tags"] = {}
    local["items"][0]["versions"][0]["note"] = "other"
    status = {c.name: c.status for c in gitops.compare(local, remote)}
    assert status == {"Support": "unchanged", "Fresh": "new", "Gone": "removed"}


def test_compare_sees_a_changed_template_and_sends_only_that() -> None:
    remote = bundle(prompt("A"), prompt("B"))
    local = bundle(prompt("A"), prompt("B", text="Different {name}"))
    changes = gitops.compare(local, remote)
    assert {c.name: c.status for c in changes} == {"A": "unchanged", "B": "changed"}
    assert [i["name"] for i in gitops.only_changes(local, changes)["items"]] == ["B"]


def test_not_an_export_is_reported(tmp_path: Path) -> None:
    with pytest.raises(gitops.TreeError, match="not a Forge export"):
        gitops.read_tree(tmp_path)
    (tmp_path / "forge.json").write_text(json.dumps({"format": "other"}))
    with pytest.raises(gitops.TreeError, match="not a Forge bundle"):
        gitops.read_tree(tmp_path)
    (tmp_path / "forge.json").write_text(json.dumps({"format": "forge.bundle"}))
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "bad.json").write_text("{nope")
    with pytest.raises(gitops.TreeError, match=r"bad\.json"):
        gitops.read_tree(tmp_path)


def history(name: str, *texts: str) -> dict[str, Any]:
    """A prompt with one version per text (numbers 1..n)."""
    item = prompt(name, texts[0], 1)
    item["versions"] = [
        {**item["versions"][0], "version": n, "user_template": t} for n, t in enumerate(texts, 1)
    ]
    return item


def test_only_the_missing_versions_are_sent() -> None:
    remote = bundle(history("A", "v1", "v2"))
    local = bundle(history("A", "v1", "v2", "v3"))
    (change,) = gitops.compare(local, remote)
    assert (change.status, change.send) == ("changed", [2])
    sent = gitops.only_changes(local, [change])["items"][0]["versions"]
    assert [v["user_template"] for v in sent] == ["v3"]


def test_version_numbers_do_not_matter_only_content() -> None:
    remote = bundle(history("A", "x", "v1", "v2"))  # the project has an extra early version
    local = bundle(history("A", "v1", "v2"))
    (change,) = gitops.compare(local, remote)
    assert change.status == "unchanged"


def test_a_revert_in_git_becomes_a_new_version() -> None:
    remote = bundle(history("A", "v1", "v2"))
    local = bundle(history("A", "v1"))  # Git went back to v1
    (change,) = gitops.compare(local, remote)
    assert (change.status, change.send) == ("changed", [0])


def test_a_new_item_goes_with_its_whole_history() -> None:
    local = bundle(history("N", "a", "b", "c"))
    changes = gitops.compare(local, bundle())
    assert changes[0].status == "new"
    assert len(gitops.only_changes(local, changes)["items"][0]["versions"]) == 3


def test_a_description_change_alone_is_reported_not_sent() -> None:
    remote = bundle(history("A", "v1"))
    local = bundle(history("A", "v1"))
    local["items"][0]["description"] = "A new description"
    (change,) = gitops.compare(local, remote)
    assert change.status == "differs"
    assert gitops.only_changes(local, [change])["items"] == []
