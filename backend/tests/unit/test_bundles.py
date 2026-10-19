"""Bundle envelope and format migrations (no database)."""

from __future__ import annotations

import pytest

from forge.bundles import service
from forge.core.errors import ProblemError


def test_current_bundle_needs_no_migration() -> None:
    bundle, migrated = service.migrate(
        {"format": "forge.bundle", "schema_version": service.SCHEMA_VERSION, "items": []}
    )
    assert migrated is None
    assert bundle["items"] == []


def test_older_bundles_are_migrated_step_by_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulates a future format 3: bundles of formats 1 and 2 keep importing."""
    monkeypatch.setattr(service, "SCHEMA_VERSION", 3)

    def v1_to_v2(b: dict[str, object]) -> dict[str, object]:
        for item in b["items"]:  # type: ignore[attr-defined]
            if item["kind"] == "prompt":
                item["description"] = item.pop("about", "")
        return b

    def v2_to_v3(b: dict[str, object]) -> dict[str, object]:
        b["items"] = [i for i in b["items"] if i["kind"] != "obsolete"]  # type: ignore[attr-defined]
        return b

    monkeypatch.setattr(service, "MIGRATIONS", {1: v1_to_v2, 2: v2_to_v3})
    old = {
        "format": "forge.bundle",
        "schema_version": 1,
        "items": [{"kind": "prompt", "name": "p", "about": "text"}, {"kind": "obsolete"}],
    }
    bundle, migrated = service.migrate(old)
    assert migrated == 1
    assert bundle["schema_version"] == 3
    assert bundle["items"] == [{"kind": "prompt", "name": "p", "description": "text"}]


@pytest.mark.parametrize("version", [0, -1, "1", None])
def test_invalid_schema_versions(version: object) -> None:
    with pytest.raises(ProblemError) as err:
        service.migrate({"format": "forge.bundle", "schema_version": version, "items": []})
    assert err.value.code == "bundle_invalid"


def test_every_supported_version_has_a_migration() -> None:
    assert set(service.MIGRATIONS) == set(range(1, service.SCHEMA_VERSION))
