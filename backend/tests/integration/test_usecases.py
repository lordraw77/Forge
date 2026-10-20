"""M2.3a catalogs, access control, use case library, versions and bundles (US-2.4.x, 2.5.x)."""

from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.integration.test_agents import (
    admin,
    client,
    env,
    make_agent,
    make_prompt,
    mock,
    publish,
    reset_mock,
    settings,
    workers,
)
from tests.integration.test_gateway import ok, project, uid
from tests.integration.test_platform_core import create_user, csrf, login

pytestmark = pytest.mark.skipif(
    not os.environ.get("FORGE_TEST_S3_ENDPOINT"), reason="needs S3, OpenSearch and Temporal"
)
__all__ = ["admin", "client", "env", "mock", "reset_mock", "settings", "workers"]


@pytest.fixture(scope="module")
def uc_env(env: dict[str, Any], client: TestClient) -> dict[str, Any]:
    owner, base = env["owner"], env["base"]
    roles = {r["role_type"]: r for r in ok(owner.get(f"{base}/roles"))}
    builder = create_user(client)
    ok(
        owner.post(
            f"{base}/members",
            headers=csrf(owner),
            json={"identifiers": [builder["username"]], "role_name_id": roles["builder"]["id"]},
        )
    )
    out = dict(env)
    out["builder"] = login(client, builder)
    out["roles"] = {k: v["id"] for k, v in roles.items()}
    return out


def catalogs(env: dict[str, Any], who: str = "owner") -> list[dict[str, Any]]:
    return ok(env[who].get(f"{env['base']}/catalogs"))


def builtin(env: dict[str, Any], name: str = "Forge.ITOps") -> dict[str, Any]:
    return next(c for c in catalogs(env) if c["name"] == name)


def new_category(env: dict[str, Any], catalog_id: str, name: str | None = None) -> dict[str, Any]:
    c = env["owner"]
    name = name or f"Cat {uid()}"
    cat = ok(
        c.post(
            f"{env['base']}/catalogs/{catalog_id}/categories", headers=csrf(c), json={"name": name}
        ),
        201,
    )
    return next(k for k in cat["categories"] if k["name"] == name)


def team(*agents: str) -> dict[str, Any]:
    return {"agents": [{"agent_ref": ref, "task": "Do it"} for ref in agents]}


def create_uc(env: dict[str, Any], who: str = "builder", **extra: Any) -> dict[str, Any]:
    c = env[who]
    body = {"name": f"UC {uid()}", "pattern": "supervisor", "definition": team()} | extra
    return ok(c.post(f"{env['base']}/use-cases", headers=csrf(c), json=body), 201)


def published_agent(env: dict[str, Any]) -> dict[str, Any]:
    agent = make_agent(env, make_prompt(env)["id"])
    publish(env, agent["id"])
    return agent


def error_codes(r: Any) -> list[str]:
    return [i["code"] for i in r.json()["detail"]]


def test_builtin_catalogs_exist_and_are_protected(uc_env: dict[str, Any]) -> None:
    c, base = uc_env["owner"], uc_env["base"]
    names = {x["name"] for x in catalogs(uc_env)}
    assert {"Forge.Software", "Forge.ITOps", "Forge.BizOps"} <= names
    itops = builtin(uc_env)
    assert itops["builtin"]
    assert itops["status"] == "active"
    r = c.delete(f"{base}/catalogs/{itops['id']}", headers=csrf(c))
    assert r.status_code == 409
    assert r.json()["code"] == "builtin_catalog"
    renamed = c.put(
        f"{base}/catalogs/{itops['id']}", headers=csrf(c), json={"name": "Other", "description": ""}
    )
    assert renamed.status_code == 409
    off = ok(
        c.put(
            f"{base}/catalogs/{itops['id']}",
            headers=csrf(c),
            json={"name": itops["name"], "description": itops["description"], "status": "disabled"},
        )
    )
    assert off["status"] == "disabled"
    ok(
        c.put(
            f"{base}/catalogs/{itops['id']}",
            headers=csrf(c),
            json={"name": itops["name"], "description": itops["description"], "status": "active"},
        )
    )


def test_catalog_and_category_management(uc_env: dict[str, Any]) -> None:
    c, base = uc_env["owner"], uc_env["base"]
    name = f"Team {uid()}"
    cat = ok(c.post(f"{base}/catalogs", headers=csrf(c), json={"name": name}), 201)
    assert not cat["builtin"]
    assert cat["categories"] == []
    assert (
        c.post(f"{base}/catalogs", headers=csrf(c), json={"name": name.lower()}).status_code == 409
    )
    k = ok(
        c.post(
            f"{base}/catalogs/{cat['id']}/categories", headers=csrf(c), json={"name": "Docs & Co"}
        ),
        201,
    )["categories"][0]
    dup = c.post(
        f"{base}/catalogs/{cat['id']}/categories", headers=csrf(c), json={"name": "docs & co"}
    )
    assert dup.status_code == 409
    renamed = ok(c.put(f"{base}/categories/{k['id']}", headers=csrf(c), json={"name": "Docs"}))
    assert renamed["categories"][0]["name"] == "Docs"
    # A filed use case blocks both deletes with the references.
    uc = create_uc(uc_env, catalog_id=cat["id"], category_id=k["id"])
    blocked = c.delete(f"{base}/categories/{k['id']}", headers=csrf(c))
    assert blocked.status_code == 409
    assert blocked.json()["detail"][0]["name"] == uc["name"]
    assert c.delete(f"{base}/catalogs/{cat['id']}", headers=csrf(c)).status_code == 409
    ok(c.delete(f"{base}/use-cases/{uc['id']}", headers=csrf(c)), 204)
    ok(c.delete(f"{base}/categories/{k['id']}", headers=csrf(c)), 204)
    ok(c.delete(f"{base}/catalogs/{cat['id']}", headers=csrf(c)), 204)
    # Only Project Admins manage catalogs.
    b = uc_env["builder"]
    assert b.post(f"{base}/catalogs", headers=csrf(b), json={"name": "Nope"}).status_code == 403


def test_library_validation_and_filters(uc_env: dict[str, Any]) -> None:
    b, base = uc_env["builder"], uc_env["base"]
    uc = create_uc(
        uc_env,
        description="Weekly report",
        tags=["Report", "report", "weekly"],
        input_fields=[
            {"name": "topic", "type": "text", "required": True, "label": "Topic"},
            {"name": "doc", "type": "file"},
        ],
        output_type="docx",
    )
    assert uc["tags"] == ["report", "weekly"]
    assert uc["status"] == "draft"
    assert uc["dirty"]
    assert uc["input_schema"]["required"] == ["topic"]
    clash = b.post(
        f"{base}/use-cases", headers=csrf(b), json={"name": uc["name"].lower(), "pattern": "graph"}
    )
    assert clash.status_code == 409
    bad = b.post(
        f"{base}/use-cases",
        headers=csrf(b),
        json={"name": f"Bad {uid()}", "pattern": "graph", "definition": {"nodes": [{"id": "A"}]}},
    )
    assert bad.status_code == 422
    assert bad.json()["code"] == "invalid_definition"
    assert "bad_node_id" in error_codes(bad)
    wrong_cat = b.post(
        f"{base}/use-cases",
        headers=csrf(b),
        json={
            "name": f"W {uid()}",
            "pattern": "graph",
            "catalog_id": builtin(uc_env)["id"],
            "category_id": "00000000-0000-7000-8000-000000000001",
        },
    )
    assert wrong_cat.status_code == 422
    found = ok(b.get(f"{base}/use-cases", params={"q": "weekly"}))["items"]
    assert [x["id"] for x in found] == [uc["id"]]
    assert (
        ok(b.get(f"{base}/use-cases", params={"status": "published", "q": "weekly"}))["items"] == []
    )
    copy = ok(
        b.post(
            f"{base}/use-cases/{uc['id']}/duplicate",
            headers=csrf(b),
            json={"name": f"Copy {uid()}"},
        ),
        201,
    )
    assert copy["input_fields"] == uc["input_fields"]
    assert copy["output_type"] == "docx"
    # End users cannot use the studio endpoints.
    e = uc_env["end_user"]
    assert e.get(f"{base}/use-cases").status_code == 403


def test_publish_needs_a_ready_use_case_with_published_references(uc_env: dict[str, Any]) -> None:
    b, base = uc_env["builder"], uc_env["base"]
    cat = builtin(uc_env)
    kat = new_category(uc_env, cat["id"])
    uc = create_uc(uc_env)
    r = b.post(f"{base}/use-cases/{uc['id']}/publish", headers=csrf(b), json={})
    assert r.status_code == 422
    assert r.json()["code"] == "not_ready"
    assert {"empty_workflow", "no_category"} <= set(error_codes(r))
    assert (
        b.post(
            f"{base}/use-cases/{uc['id']}/status", headers=csrf(b), json={"status": "testing"}
        ).status_code
        == 422
    )
    draft_agent = make_agent(uc_env, make_prompt(uc_env)["id"])  # exists but is not published
    body = {
        "catalog_id": cat["id"],
        "category_id": kat["id"],
        "definition": team(f"{draft_agent['name']}@1"),
    }
    ok(b.put(f"{base}/use-cases/{uc['id']}", headers=csrf(b), json=body))
    r = b.post(f"{base}/use-cases/{uc['id']}/publish", headers=csrf(b), json={})
    assert error_codes(r) == ["reference_not_published"]
    publish(uc_env, draft_agent["id"])
    body["definition"] = team(f"{draft_agent['name']}@9")
    ok(b.put(f"{base}/use-cases/{uc['id']}", headers=csrf(b), json=body))
    assert error_codes(
        b.post(f"{base}/use-cases/{uc['id']}/publish", headers=csrf(b), json={})
    ) == ["reference_version_unknown"]
    body["definition"] = team(f"{draft_agent['name']}@1")
    ok(b.put(f"{base}/use-cases/{uc['id']}", headers=csrf(b), json=body))
    testing = ok(
        b.post(f"{base}/use-cases/{uc['id']}/status", headers=csrf(b), json={"status": "testing"})
    )
    assert testing["status"] == "testing"
    done = ok(
        b.post(f"{base}/use-cases/{uc['id']}/publish", headers=csrf(b), json={"note": "first"})
    )
    assert done["status"] == "published"
    assert done["published_version"] == 1
    assert not done["dirty"]
    again = b.post(f"{base}/use-cases/{uc['id']}/publish", headers=csrf(b), json={})
    assert again.status_code == 409
    assert again.json()["code"] == "nothing_to_publish"
    # The end user cannot publish.
    e = uc_env["end_user"]
    assert (
        e.post(f"{base}/use-cases/{uc['id']}/publish", headers=csrf(e), json={}).status_code == 403
    )


def test_versions_are_immutable_and_rollback_works(uc_env: dict[str, Any]) -> None:
    b, base, owner = uc_env["builder"], uc_env["base"], uc_env["owner"]
    agent = published_agent(uc_env)
    cat = builtin(uc_env, "Forge.Software")
    kat = new_category(uc_env, cat["id"])
    uc = create_uc(
        uc_env,
        description="v1 text",
        catalog_id=cat["id"],
        category_id=kat["id"],
        definition=team(f"{agent['name']}@1"),
    )
    url = f"{base}/use-cases/{uc['id']}"
    v1 = ok(b.post(f"{url}/publish", headers=csrf(b), json={"note": "one"}))
    assert v1["published_version"] == 1
    edited = ok(
        b.put(
            url,
            headers=csrf(b),
            json={
                "description": "v2 text",
                "catalog_id": cat["id"],
                "category_id": kat["id"],
                "definition": team(f"{agent['name']}@1"),
                "output_type": "pdf",
            },
        )
    )
    # Editing a published use case makes a draft; the published version keeps serving.
    assert edited["dirty"]
    assert edited["status"] == "published"
    assert edited["published_version"] == 1
    live = ok(b.get(f"{base}/catalog/use-cases/{uc['id']}"))
    assert live["version"] == 1
    assert live["description"] == "v1 text"
    assert live["output_type"] == "markdown"
    v2 = ok(b.post(f"{url}/publish", headers=csrf(b), json={"note": "two"}))
    assert v2["published_version"] == 2
    assert v2["versions"] == [2, 1]
    vs = ok(b.get(f"{url}/versions"))
    assert [(v["version"], v["live"]) for v in vs] == [(2, True), (1, False)]
    assert ok(b.get(f"{url}/versions/1"))["description"] == "v1 text"
    diff = ok(b.get(f"{url}/diff", params={"left": 1, "right": 2}))
    assert set(diff["changed"]) == {"description", "output_type"}
    assert b.get(f"{url}/versions/7").status_code == 404
    assert b.get(f"{url}/diff", params={"left": 1, "right": 7}).status_code == 404
    back = ok(b.post(f"{url}/publish", headers=csrf(b), json={"version": 1}))
    assert back["published_version"] == 1
    assert back["description"] == "v1 text"
    assert not back["dirty"]
    assert back["output_type"] == "markdown"
    assert ok(b.get(f"{base}/catalog/use-cases/{uc['id']}"))["version"] == 1
    # Deprecate: gone from the catalog, versions kept; publishing again brings it back.
    dep = ok(b.post(f"{url}/status", headers=csrf(b), json={"status": "deprecated"}))
    assert dep["status"] == "deprecated"
    assert b.get(f"{base}/catalog/use-cases/{uc['id']}").status_code == 404
    assert b.post(f"{url}/status", headers=csrf(b), json={"status": "testing"}).status_code == 409
    ok(b.post(f"{url}/publish", headers=csrf(b), json={"version": 2}))
    assert ok(b.get(f"{base}/catalog/use-cases/{uc['id']}"))["version"] == 2
    # Agents used by a use case cannot be deleted.
    blocked = owner.delete(f"{base}/agents/{agent['id']}", headers=csrf(owner))
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "agent_in_use"
    assert blocked.json()["detail"][0]["name"] == uc["name"]
    ok(b.delete(url, headers=csrf(b)), 204)
    ok(owner.delete(f"{base}/agents/{agent['id']}", headers=csrf(owner)), 204)


def test_access_control_by_role_name(uc_env: dict[str, Any]) -> None:
    c, b, e, v = (uc_env[k] for k in ("owner", "builder", "end_user", "viewer"))
    base, role = uc_env["base"], uc_env["roles"]["end_user"]
    agent = published_agent(uc_env)
    cat = builtin(uc_env, "Forge.BizOps")
    k1, k2 = new_category(uc_env, cat["id"]), new_category(uc_env, cat["id"])
    shown, hidden = (
        create_uc(
            uc_env,
            catalog_id=cat["id"],
            category_id=k["id"],
            definition=team(f"{agent['name']}@1"),
        )
        for k in (k1, k2)
    )
    for uc in (shown, hidden):
        ok(b.post(f"{base}/use-cases/{uc['id']}/publish", headers=csrf(b), json={}))
    # No grant yet: the end user sees an empty catalog and no use case.
    assert catalogs(uc_env, "end_user") == []
    assert ok(e.get(f"{base}/catalog/use-cases")) == []
    # The Builder sees everything published.
    assert {x["id"] for x in ok(b.get(f"{base}/catalog/use-cases"))} >= {shown["id"], hidden["id"]}
    matrix = ok(c.get(f"{base}/roles/{role}/catalog-access"))
    assert matrix["role_name"] == "User"
    assert not any(x["enabled"] for x in matrix["catalogs"])
    grant = {
        "catalogs": [{"catalog_id": cat["id"], "enabled": True, "excluded_categories": [k2["id"]]}]
    }
    after = ok(c.put(f"{base}/roles/{role}/catalog-access", headers=csrf(c), json=grant))
    mine = next(x for x in after["catalogs"] if x["catalog_id"] == cat["id"])
    assert mine["enabled"]
    assert mine["excluded_categories"] == [k2["id"]]
    seen = ok(e.get(f"{base}/catalog/use-cases"))
    assert [x["id"] for x in seen] == [shown["id"]]
    assert "definition" not in seen[0]
    assert "agents" not in str(seen[0])
    visible = catalogs(uc_env, "end_user")
    assert [x["name"] for x in visible] == ["Forge.BizOps"]
    assert [x["id"] for x in visible[0]["categories"]] == [k1["id"]]
    assert ok(e.get(f"{base}/catalog/use-cases/{shown['id']}"))["category"] == k1["name"]
    # Not 403: a use case the caller may not see does not exist for them.
    assert e.get(f"{base}/catalog/use-cases/{hidden['id']}").status_code == 404
    assert e.get(f"{base}/use-cases/{shown['id']}").status_code == 403
    # Enabling the catalog again (no exclusions) shows both; a disabled catalog hides all.
    ok(
        c.put(
            f"{base}/roles/{role}/catalog-access",
            headers=csrf(c),
            json={"catalogs": [{"catalog_id": cat["id"], "enabled": True}]},
        )
    )
    assert len(ok(e.get(f"{base}/catalog/use-cases"))) >= 2
    off = {"name": cat["name"], "description": cat["description"], "status": "disabled"}
    ok(c.put(f"{base}/catalogs/{cat['id']}", headers=csrf(c), json=off))
    assert ok(e.get(f"{base}/catalog/use-cases")) == []
    ok(c.put(f"{base}/catalogs/{cat['id']}", headers=csrf(c), json=off | {"status": "active"}))
    # Revoke everything; a Viewer has no run permission at all.
    ok(c.put(f"{base}/roles/{role}/catalog-access", headers=csrf(c), json={"catalogs": []}))
    assert ok(e.get(f"{base}/catalog/use-cases")) == []
    assert v.get(f"{base}/catalog/use-cases").status_code == 403
    # Bad payloads and foreign ids.
    bad = c.put(
        f"{base}/roles/{role}/catalog-access",
        headers=csrf(c),
        json={
            "catalogs": [
                {"catalog_id": cat["id"], "enabled": True, "excluded_categories": [cat["id"]]}
            ]
        },
    )
    assert bad.status_code == 422
    foreign = c.get(f"{base}/roles/00000000-0000-7000-8000-0000000000aa/catalog-access")
    assert foreign.status_code == 404
    assert (
        e.put(
            f"{base}/roles/{role}/catalog-access", headers=csrf(e), json={"catalogs": []}
        ).status_code
        == 403
    )
    for uc in (shown, hidden):
        ok(b.delete(f"{base}/use-cases/{uc['id']}", headers=csrf(b)), 204)
    ok(c.delete(f"{base}/agents/{agent['id']}", headers=csrf(c)), 204)


def test_bundle_round_trip_repins_references(uc_env: dict[str, Any]) -> None:
    c, base = uc_env["owner"], uc_env["base"]
    agent = published_agent(uc_env)
    cat = builtin(uc_env)
    kat = new_category(uc_env, cat["id"])
    uc = create_uc(
        uc_env,
        description="bundled",
        tags=["x"],
        catalog_id=cat["id"],
        category_id=kat["id"],
        input_fields=[{"name": "topic", "type": "text", "required": True}],
        definition=team(f"{agent['name']}@1"),
    )
    ok(c.post(f"{base}/use-cases/{uc['id']}/publish", headers=csrf(c), json={}))
    exported = c.post(f"{base}/export", headers=csrf(c), json={"use_cases": [{"id": uc["id"]}]})
    bundle = ok(exported)
    kinds = [(i["kind"], i["name"]) for i in bundle["items"]]
    assert ("use_case", uc["name"]) in kinds
    assert ("agent", agent["name"]) in kinds
    item = next(i for i in bundle["items"] if i["kind"] == "use_case")
    assert item["catalog"] == cat["name"]
    assert item["category"] == kat["name"]
    assert [h["version"] for h in item["history"]] == [1]
    dry = ok(c.post(f"{base}/import", headers=csrf(c), json={"bundle": bundle, "policy": "rename"}))
    row = next(i for i in dry["items"] if i["kind"] == "use_case")
    assert dry["dry_run"]
    assert row["action"] == "renamed"
    assert row["target"].endswith("(imported)")
    done = ok(
        c.post(
            f"{base}/import",
            headers=csrf(c),
            json={"bundle": bundle, "policy": "rename", "dry_run": False},
        )
    )
    new = next(i for i in done["items"] if i["kind"] == "use_case")
    assert new.get("id"), new["error"]
    copy = ok(c.get(f"{base}/use-cases/{new['id']}"))
    assert copy["name"] == new["target"]
    assert copy["status"] == "draft"
    assert copy["dirty"]
    assert copy["catalog"] == cat["name"]
    assert copy["category"] == kat["name"]
    assert copy["input_fields"][0]["name"] == "topic"
    assert copy["tags"] == ["x"]
    assert any("history" in w for w in new["warnings"])
    skip = ok(c.post(f"{base}/import", headers=csrf(c), json={"bundle": bundle, "policy": "skip"}))
    assert next(i for i in skip["items"] if i["kind"] == "use_case")["action"] == "skipped"
    extend = ok(
        c.post(
            f"{base}/import",
            headers=csrf(c),
            json={"bundle": bundle, "policy": "new_version", "dry_run": False},
        )
    )
    assert next(i for i in extend["items"] if i["kind"] == "use_case")["action"] == "new_versions"
    # A reference that does not resolve is reported, not silently kept.
    item["definition"] = team("ghost-agent@1")
    miss = ok(
        c.post(
            f"{base}/import",
            headers=csrf(c),
            json={"bundle": {**bundle, "items": [item]}, "policy": "rename"},
        )
    )
    assert any("ghost-agent" in w for w in miss["items"][0]["warnings"])
    for u in ok(c.get(f"{base}/use-cases", params={"q": uc["name"]}))["items"]:
        ok(c.delete(f"{base}/use-cases/{u['id']}", headers=csrf(c)), 204)
    ok(c.delete(f"{base}/agents/{agent['id']}", headers=csrf(c)), 204)


def test_other_projects_cannot_see_use_cases(
    uc_env: dict[str, Any], admin: TestClient, client: TestClient
) -> None:
    other = project(admin, client)
    stranger = login(client, other["owner"])
    uc = create_uc(uc_env)
    base = uc_env["base"]
    # Not a member of this project: 404, as for every other resource (ADR-003).
    assert stranger.get(f"{base}/use-cases/{uc['id']}").status_code == 404
    mine = f"/api/v1/projects/{other['id']}"
    assert stranger.get(f"{mine}/use-cases/{uc['id']}").status_code == 404
    assert ok(stranger.get(f"{mine}/use-cases"))["items"] == []
    names = {c["name"] for c in ok(stranger.get(f"{mine}/catalogs"))}
    assert names == {"Forge.Software", "Forge.ITOps", "Forge.BizOps"}
    stolen = stranger.put(
        f"{mine}/use-cases/{uc['id']}", headers=csrf(stranger), json={"description": "x"}
    )
    assert stolen.status_code == 404
