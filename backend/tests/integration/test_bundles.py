"""Export and import of configuration and assets between projects (bundles)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.integration.test_gateway import model, ok, uid
from tests.integration.test_platform_core import csrf
from tests.integration.test_rag import (  # noqa: F401 - module fixtures
    MANUAL,
    admin,
    client,
    collection,
    env,
    mock,
    pipeline,
    settings,
    upload,
    worker,
)


def _prompt(c: TestClient, base: str, name: str) -> dict[str, Any]:
    body = {
        "name": name,
        "description": "answer style",
        "version": {"model": "default-chat", "system_template": "Answer briefly, cite [n]."},
    }
    p = ok(c.post(f"{base}/prompts", headers=csrf(c), json=body), 201)
    v2 = {"model": "default-chat", "system_template": "Answer in one line, cite [n]."}
    ok(c.post(f"{base}/prompts/{p['id']}/versions", headers=csrf(c), json=v2), 201)
    ok(c.post(f"{base}/prompts/{p['id']}/publish", headers=csrf(c), json={"version": 1}))
    return p


def test_export_import_with_versions_dependencies_and_policies(
    env: dict[str, Any],  # noqa: F811
    admin: TestClient,  # noqa: F811
) -> None:
    c, base = env["owner"], env["base"]
    prompt = _prompt(c, base, f"Style {uid()}")
    cid = collection(env)["id"]
    upload(env, cid, [("manual.md", MANUAL)])
    p = pipeline(env, cid)
    cfg = p["version"]["config"] | {"prompt_id": prompt["id"], "top_k": 4}
    ok(c.post(f"{base}/rag/{p['id']}/versions", headers=csrf(c), json={"config": cfg}), 201)
    group = ok(c.post(f"{base}/groups", headers=csrf(c), json={"name": f"Ops {uid()}"}), 201)

    exported = c.post(
        f"{base}/export",
        headers=csrf(c),
        json={
            "rag_pipelines": [{"id": p["id"], "versions": "all"}],
            "model_aliases": True,
            "gateway_config": True,
            "user_groups": True,
        },
    )
    assert exported.status_code == 200, exported.text
    assert "attachment" in exported.headers["content-disposition"]
    bundle = exported.json()
    assert (bundle["format"], bundle["schema_version"]) == ("forge.bundle", 1)
    aliases = [i for i in bundle["items"] if i["kind"] == "model_alias"]
    assert aliases
    assert all(a["models"] for a in aliases)
    kinds = [i["kind"] for i in bundle["items"]]
    assert set(kinds) >= {
        "prompt",
        "data_collection",
        "rag_pipeline",
        "model_alias",
        "gateway_config",
        "user_group",
    }
    exp_prompt = next(i for i in bundle["items"] if i["kind"] == "prompt")
    assert [v["version"] for v in exp_prompt["versions"]] == [1, 2]
    assert exp_prompt["tags"] == {"prod": 1}
    exp_rag = next(i for i in bundle["items"] if i["kind"] == "rag_pipeline")
    latest = exp_rag["versions"][-1]["config"]
    assert latest["prompt"] == prompt["name"]  # references by name, never ids
    assert "collection_id" not in latest
    assert "prompt_id" not in latest
    assert all("api_key" not in str(i) for i in bundle["items"])

    # ---- into another project owned by the same Project Admin
    target = ok(
        admin.post(
            "/api/v1/admin/projects",
            headers=csrf(admin),
            json={"name": f"Target {uid()}", "admin_user_id": env["project"]["owner"]["id"]},
        ),
        201,
    )["project"]["id"]
    tbase = f"/api/v1/projects/{target}"
    dry = ok(c.post(f"{tbase}/import", headers=csrf(c), json={"bundle": bundle}))
    assert dry["dry_run"] is True
    actions = {(i["kind"], i["action"]) for i in dry["items"]}
    assert [i["error"] for i in dry["items"] if i["action"] == "error"] == [], dry["items"]
    assert ("prompt", "created") in actions
    assert ("rag_pipeline", "created") in actions, dry
    assert ok(c.get(f"{tbase}/prompts"))["items"] == []  # nothing written by a dry run

    real = ok(c.post(f"{tbase}/import", headers=csrf(c), json={"bundle": bundle, "dry_run": False}))
    errors = [i for i in real["items"] if i["action"] == "error"]
    assert errors == [], errors
    imported = next(
        i for i in ok(c.get(f"{tbase}/prompts"))["items"] if i["name"] == prompt["name"]
    )
    assert imported["latest_version"] == 2
    assert imported["published_version"] == 1  # tags follow their versions
    rag = next(i for i in ok(c.get(f"{tbase}/rag"))["items"] if i["name"] == p["name"])
    detail = ok(c.get(f"{tbase}/rag/{rag['id']}"))
    assert detail["version"]["config"]["prompt_id"] == imported["id"]
    assert detail["version"]["config"]["top_k"] == 4
    assert detail["collection_name"] == next(
        i["name"] for i in bundle["items"] if i["kind"] == "data_collection"
    )
    groups = {g["name"] for g in ok(c.get(f"{tbase}/groups"))}
    assert group["name"] in groups

    # ---- conflicts on a second import
    again = ok(
        c.post(
            f"{tbase}/import",
            headers=csrf(c),
            json={"bundle": bundle, "dry_run": False, "policy": "new_version"},
        )
    )
    by_kind = {i["kind"]: i for i in again["items"] if i["kind"] in ("prompt", "rag_pipeline")}
    assert by_kind["prompt"]["action"] == "new_versions"
    assert by_kind["prompt"]["warnings"]  # tags are not moved on an existing prompt
    after = ok(c.get(f"{tbase}/prompts/{imported['id']}"))
    assert after["latest_version"] == 4
    assert after["published_version"] == 1
    renamed = ok(
        c.post(
            f"{tbase}/import",
            headers=csrf(c),
            json={"bundle": bundle, "dry_run": False, "policy": "rename"},
        )
    )
    assert next(i for i in renamed["items"] if i["kind"] == "prompt")["target"].endswith(
        "(imported)"
    )
    skipped = ok(
        c.post(
            f"{tbase}/import",
            headers=csrf(c),
            json={"bundle": bundle, "dry_run": False, "policy": "skip"},
        )
    )
    assert {i["action"] for i in skipped["items"] if i["kind"] == "prompt"} == {"skipped"}


@pytest.mark.parametrize(
    ("bundle", "code"),
    [
        ({"format": "forge.bundle", "schema_version": 99, "items": []}, "bundle_too_new"),
        ({"format": "something", "schema_version": 1, "items": []}, "not_a_bundle"),
        ({"format": "forge.bundle", "items": []}, "bundle_invalid"),
    ],
)
def test_invalid_bundles_are_refused(
    env: dict[str, Any],  # noqa: F811
    bundle: dict[str, Any],
    code: str,
) -> None:
    c = env["owner"]
    r = c.post(f"{env['base']}/import", headers=csrf(c), json={"bundle": bundle})
    assert r.status_code == 422
    assert r.json()["code"] == code


def test_unknown_items_and_fields_from_newer_versions_are_ignored(
    env: dict[str, Any],  # noqa: F811
) -> None:
    c = env["owner"]
    name = f"Tolerant {uid()}"
    bundle = {
        "format": "forge.bundle",
        "schema_version": 1,
        "items": [
            {"kind": "connector", "name": "from the future"},
            {
                "kind": "prompt",
                "name": name,
                "description": "d",
                "future_field": True,
                "versions": [
                    {"version": 1, "model": "default-chat", "system_template": "Hi", "future": 1}
                ],
            },
        ],
    }
    report = ok(
        c.post(f"{env['base']}/import", headers=csrf(c), json={"bundle": bundle, "dry_run": False})
    )
    assert {(i["kind"], i["action"]) for i in report["items"]} == {
        ("connector", "skipped"),
        ("prompt", "created"),
    }


def test_agents_travel_in_bundles(
    env: dict[str, Any],  # noqa: F811
    admin: TestClient,  # noqa: F811
) -> None:
    """An agent is exported with its prompt and pipelines by name, and re-pinned to versions
    of the target project on import (version numbers differ between projects)."""
    c, base = env["owner"], env["base"]
    prompt = _prompt(c, base, f"Agent style {uid()}")
    cid = collection(env)["id"]
    upload(env, cid, [("manual.md", MANUAL)])
    rag = pipeline(env, cid)
    ok(c.post(f"{base}/rag/{rag['id']}/publish", headers=csrf(c), json={}))
    tools_model = model(
        admin,
        env["models"]["chat"]["provider_id"],
        "r-echo",
        all_projects=True,
        capabilities=["streaming", "tools"],
    )
    config = {
        "prompt": {"prompt_id": prompt["id"], "version": 1},
        "model": tools_model["name"],
        "tools": [
            {"kind": "rag", "pipeline_id": rag["id"], "name": "search_manual"},
            {"kind": "builtin", "name": "http_get", "allowed_hosts": ["example.com"]},
        ],
        "limits": {"max_steps": 5},
    }
    agent = ok(
        c.post(
            f"{base}/agents",
            headers=csrf(c),
            json={"name": f"Bundled {uid()}", "description": "ships in a bundle", "config": config},
        ),
        201,
    )
    ok(
        c.post(
            f"{base}/agents/{agent['id']}/versions",
            headers=csrf(c),
            json={"config": config | {"limits": {"max_steps": 6}}, "note": "more steps"},
        ),
        201,
    )
    ok(c.post(f"{base}/agents/{agent['id']}/publish", headers=csrf(c), json={"version": 1}))

    bundle = ok(
        c.post(
            f"{base}/export",
            headers=csrf(c),
            json={"agents": [{"id": agent["id"], "versions": "all"}], "model_aliases": True},
        )
    )
    kinds = {i["kind"] for i in bundle["items"]}
    # The agent's dependencies follow it; aliases were asked for.
    assert kinds == {"agent", "prompt", "rag_pipeline", "data_collection", "model_alias"}
    item = next(i for i in bundle["items"] if i["kind"] == "agent")
    assert [v["version"] for v in item["versions"]] == [1, 2]
    assert item["tags"] == {"prod": 1}
    text = str(item)
    assert agent["id"] not in text
    assert prompt["id"] not in text
    assert rag["id"] not in text
    first = item["versions"][0]["config"]
    assert first["prompt"]["name"] == prompt["name"]
    assert first["tools"][0]["pipeline"] == rag["name"]
    assert "pipeline_id" not in first["tools"][0]
    assert first["tools"][1] == {
        "kind": "builtin",
        "name": "http_get",
        "allowed_hosts": ["example.com"],
    }

    target = ok(
        admin.post(
            "/api/v1/admin/projects",
            headers=csrf(admin),
            json={"name": f"Agents target {uid()}", "admin_user_id": env["project"]["owner"]["id"]},
        ),
        201,
    )["project"]["id"]
    tbase = f"/api/v1/projects/{target}"
    dry = ok(c.post(f"{tbase}/import", headers=csrf(c), json={"bundle": bundle}))
    assert [i["error"] for i in dry["items"] if i["action"] == "error"] == [], dry
    assert ("agent", "created") in {(i["kind"], i["action"]) for i in dry["items"]}
    assert ok(c.get(f"{tbase}/agents"))["items"] == []  # a dry run writes nothing

    real = ok(c.post(f"{tbase}/import", headers=csrf(c), json={"bundle": bundle, "dry_run": False}))
    assert [i for i in real["items"] if i["action"] == "error"] == [], real
    imported = next(i for i in ok(c.get(f"{tbase}/agents"))["items"] if i["name"] == agent["name"])
    assert imported["latest_version"] == 2
    assert imported["published_version"] == 1
    detail = ok(c.get(f"{tbase}/agents/{imported['id']}"))
    t_prompt = next(
        i for i in ok(c.get(f"{tbase}/prompts"))["items"] if i["name"] == prompt["name"]
    )
    t_rag = next(i for i in ok(c.get(f"{tbase}/rag"))["items"] if i["name"] == rag["name"])
    cfg = detail["version"]["config"]
    assert cfg["prompt"]["prompt_id"] == t_prompt["id"]  # this project's prompt, not the source's
    assert cfg["tools"][0]["pipeline_id"] == t_rag["id"]
    assert cfg["limits"]["max_steps"] == 6

    # An existing name: a new version, a copy, or nothing.
    again = ok(
        c.post(f"{tbase}/import", headers=csrf(c), json={"bundle": bundle, "dry_run": False})
    )
    assert ("agent", "new_versions") in {(i["kind"], i["action"]) for i in again["items"]}
    assert ok(c.get(f"{tbase}/agents/{imported['id']}"))["latest_version"] == 4
    copy = ok(
        c.post(
            f"{tbase}/import",
            headers=csrf(c),
            json={"bundle": bundle, "dry_run": False, "policy": "rename"},
        )
    )
    renamed = next(i for i in copy["items"] if i["kind"] == "agent")
    assert renamed["action"] == "renamed"
    assert renamed["target"].endswith("(imported)")
    skipped = ok(
        c.post(f"{tbase}/import", headers=csrf(c), json={"bundle": bundle, "policy": "skip"})
    )
    assert ("agent", "skipped") in {(i["kind"], i["action"]) for i in skipped["items"]}


def test_guardrail_policy_travels_in_bundles(
    env: dict[str, Any],  # noqa: F811
    admin: TestClient,  # noqa: F811
) -> None:
    c, base = env["owner"], env["base"]
    rules = [
        {"type": "pii", "stage": ["input"], "action": "mask", "params": {"entities": ["EMAIL"]}},
        {"type": "secrets", "stage": ["output"], "action": "block"},
    ]
    ok(c.put(f"{base}/guardrails/policy", headers=csrf(c), json={"rules": rules}))
    exported = c.post(f"{base}/export", headers=csrf(c), json={"guardrail_policy": True})
    assert exported.status_code == 200, exported.text
    bundle = exported.json()
    item = next(i for i in bundle["items"] if i["kind"] == "guardrail_policy")
    assert [r["type"] for r in item["rules"]] == ["pii", "secrets"]

    target = ok(
        admin.post(
            "/api/v1/admin/projects",
            headers=csrf(admin),
            json={"name": f"GR target {uid()}", "admin_user_id": env["project"]["owner"]["id"]},
        ),
        201,
    )["project"]["id"]
    tbase = f"/api/v1/projects/{target}"
    dry = ok(c.post(f"{tbase}/import", headers=csrf(c), json={"bundle": bundle}))
    assert [i["action"] for i in dry["items"]] == ["updated"]
    assert ok(c.get(f"{tbase}/guardrails/policy"))["rules"] == []  # a dry run changes nothing
    done = ok(c.post(f"{tbase}/import", headers=csrf(c), json={"bundle": bundle, "dry_run": False}))
    assert done["summary"] == {"updated": 1}
    assert [r["type"] for r in ok(c.get(f"{tbase}/guardrails/policy"))["rules"]] == [
        "pii",
        "secrets",
    ]

    # A rule naming a guard model this project does not have is an error, not a half policy.
    item["rules"] = [{"type": "toxicity", "action": "block", "params": {"model": "no-such-guard"}}]
    bad = ok(c.post(f"{tbase}/import", headers=csrf(c), json={"bundle": bundle, "dry_run": False}))
    assert bad["items"][0]["action"] == "error"
    assert [r["type"] for r in ok(c.get(f"{tbase}/guardrails/policy"))["rules"]] == [
        "pii",
        "secrets",
    ]
