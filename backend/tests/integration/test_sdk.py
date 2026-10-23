"""E3.3: the SDK and `forgectl` against the real API with a project API key.

The SDK's HTTP client is the test client of the app, so every call goes through authentication,
scopes, permissions and the bundle code exactly as it does from a laptop or a CI job.
"""
# ruff: noqa: F811  (module fixtures imported from test_rag are redefined as parameters)

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from forge_sdk import ForgeClient, ForgeError, cli

from tests.integration.test_bundles import _prompt
from tests.integration.test_gateway import ok, uid
from tests.integration.test_platform_core import csrf, new_client
from tests.integration.test_rag import (  # noqa: F401 - module fixtures
    admin,
    client,
    env,
    mock,
    settings,
    worker,
)


def key_client(client: TestClient, env: dict[str, Any], scopes: list[str]) -> ForgeClient:
    owner, base = env["owner"], env["base"]
    token = ok(
        owner.post(
            f"{base}/api-keys", headers=csrf(owner), json={"name": f"sdk {uid()}", "scopes": scopes}
        ),
        201,
    )["token"]
    http = new_client(client)
    return ForgeClient("http://testserver", token, http=http)


def run_cli(forge: ForgeClient, *argv: str) -> tuple[int, str]:
    out = io.StringIO()
    original = cli._client
    cli._client = lambda _args: forge  # type: ignore[assignment]
    try:
        return cli.main(list(argv), out), out.getvalue()
    finally:
        cli._client = original  # type: ignore[assignment]


def test_whoami_shows_the_project_and_what_the_key_really_holds(
    client: TestClient,
    env: dict[str, Any],
) -> None:
    forge = key_client(client, env, ["asset.write", "llm.invoke"])
    who = forge.whoami()
    assert who["kind"] == "api_key"
    assert who["project_id"] == env["project"]["id"]
    assert set(who["permissions"]) == {"asset.write", "llm.invoke"}


def test_git_flow_export_edit_diff_apply_is_idempotent(
    client: TestClient,
    env: dict[str, Any],
    tmp_path: Path,
) -> None:
    owner, base = env["owner"], env["base"]
    name = f"Style {uid()}"
    _prompt(owner, base, name)
    forge = key_client(client, env, ["asset.write"])

    # Export: the prompt is a file, with no timestamps.
    code, text = run_cli(forge, "export", str(tmp_path))
    assert code == 0, text
    files = list((tmp_path / "prompts").glob("*.json"))
    mine = next(f for f in files if name.replace(" ", "-") in f.name)
    item = json.loads(mine.read_text())
    assert item["name"] == name
    assert len(item["versions"]) == 2  # the whole history
    assert "created_at" not in mine.read_text()

    # Nothing changed yet.
    code, text = run_cli(forge, "diff", str(tmp_path), "--exit-code")
    assert code == 0
    assert "0 to apply" in text

    # A reviewed edit in Git, then diff, a dry run, and apply.
    third = {**item["versions"][-1], "system_template": "Answer in exactly two lines, cite [n]."}
    item["versions"].append(third)  # a new version, appended in Git
    mine.write_text(json.dumps(item))
    code, text = run_cli(forge, "diff", str(tmp_path), "--exit-code")
    assert code == 1
    assert f"prompt           {name}  [changed]" in text

    before = ok(owner.get(f"{base}/prompts"))["items"]
    prompt_id = next(p["id"] for p in before if p["name"] == name)
    versions_before = len(ok(owner.get(f"{base}/prompts/{prompt_id}/versions")))
    code, text = run_cli(forge, "apply", str(tmp_path), "--dry-run")
    assert code == 0
    assert "Dry run" in text
    assert len(ok(owner.get(f"{base}/prompts/{prompt_id}/versions"))) == versions_before

    code, text = run_cli(forge, "apply", str(tmp_path))
    assert code == 0
    assert "new_versions" in text
    versions = ok(owner.get(f"{base}/prompts/{prompt_id}/versions"))
    assert len(versions) == versions_before + 1

    # Applying again changes nothing: the project matches the files now.
    code, text = run_cli(forge, "diff", str(tmp_path), "--exit-code")
    assert code == 0, text
    code, text = run_cli(forge, "apply", str(tmp_path))
    assert "Nothing to apply" in text
    assert len(ok(owner.get(f"{base}/prompts/{prompt_id}/versions"))) == versions_before + 1

    # A new item in the files is created; removing a file never deletes anything.
    fresh = {**item, "name": f"Fresh {uid()}"}
    (tmp_path / "prompts" / "fresh.json").write_text(json.dumps(fresh))
    mine.unlink()
    code, text = run_cli(forge, "diff", str(tmp_path))
    assert "[new]" in text
    assert "[removed]" in text
    code, _ = run_cli(forge, "apply", str(tmp_path))
    assert code == 0
    names = {p["name"] for p in ok(owner.get(f"{base}/prompts"))["items"]}
    assert {name, fresh["name"]} <= names


def test_a_key_without_asset_write_cannot_export_or_import(
    client: TestClient,
    env: dict[str, Any],
) -> None:
    forge = key_client(client, env, ["llm.invoke"])
    with pytest.raises(ForgeError) as err:
        forge.export_bundle()
    assert err.value.status == 403
    with pytest.raises(ForgeError) as err:
        forge.import_bundle({"format": "forge.bundle", "schema_version": 1, "items": []})
    assert err.value.status == 403


def test_config_needs_project_manage_and_a_bad_key_is_401(
    client: TestClient,
    env: dict[str, Any],
) -> None:
    forge = key_client(client, env, ["asset.write"])
    with pytest.raises(ForgeError) as err:
        forge.export_bundle(config=True)
    assert err.value.status == 403
    bad = ForgeClient(
        "http://testserver", "fk_nope_nope", env["project"]["id"], http=new_client(client)
    )
    with pytest.raises(ForgeError) as err:
        bad.whoami()
    assert err.value.status == 401


def test_import_dry_run_reports_without_writing(
    client: TestClient,
    env: dict[str, Any],
) -> None:
    owner, base = env["owner"], env["base"]
    forge = key_client(client, env, ["asset.write"])
    bundle = {
        "format": "forge.bundle",
        "schema_version": 1,
        "items": [
            {
                "kind": "prompt",
                "name": f"Dry {uid()}",
                "description": "",
                "versions": [{"model": "default-chat", "system_template": "Be brief."}],
            }
        ],
    }
    report = forge.import_bundle(bundle)  # dry run by default
    assert report["items"][0]["action"] == "created"
    assert bundle["items"][0]["name"] not in {
        p["name"] for p in ok(owner.get(f"{base}/prompts"))["items"]
    }


def test_the_web_interface_endpoints_sync_a_picked_folder(
    client: TestClient,
    env: dict[str, Any],
) -> None:
    """What the Sync page does: a zip of files out, a picked folder or zip back in, a plan, and an
    apply that sends only what the project lacks."""
    import io
    import zipfile

    owner, base = env["owner"], env["base"]
    name = f"Sync {uid()}"
    _prompt(owner, base, name)
    r = owner.post(f"{base}/export/files", headers=csrf(owner), json={"versions": "all"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        files = {n: zf.read(n) for n in zf.namelist()}
    assert "forge.json" in files
    path = next(p for p in files if p.startswith("prompts/") and name.replace(" ", "-") in p)

    # A browser folder pick: each file keeps its path, below the folder's own name.
    picked = [("files", (f"repo/{p}", data)) for p, data in files.items()]
    bundle = ok(owner.post(f"{base}/sync/read", headers=csrf(owner), files=picked))
    assert any(i["name"] == name for i in bundle["items"])
    plan = ok(owner.post(f"{base}/sync/plan", headers=csrf(owner), json={"bundle": bundle}))
    mine = next(i for i in plan["items"] if i["name"] == name)
    assert mine["status"] == "unchanged"

    # A new version appended in the file: the plan says +1, the dry run writes nothing.
    item = json.loads(files[path])
    item["versions"].append({**item["versions"][-1], "system_template": "Changed in Git."})
    edited = {**bundle, "items": [item]}
    plan = ok(owner.post(f"{base}/sync/plan", headers=csrf(owner), json={"bundle": edited}))
    mine = next(i for i in plan["items"] if i["name"] == name)
    assert (mine["status"], mine["versions_to_send"]) == ("changed", 1)
    prompt_id = next(
        p["id"] for p in ok(owner.get(f"{base}/prompts"))["items"] if p["name"] == name
    )
    before = len(ok(owner.get(f"{base}/prompts/{prompt_id}/versions")))
    dry = ok(
        owner.post(
            f"{base}/sync/apply", headers=csrf(owner), json={"bundle": edited, "dry_run": True}
        )
    )
    assert dry["report"]["dry_run"] is True
    assert len(ok(owner.get(f"{base}/prompts/{prompt_id}/versions"))) == before
    done = ok(
        owner.post(
            f"{base}/sync/apply", headers=csrf(owner), json={"bundle": edited, "dry_run": False}
        )
    )
    assert done["report"]["summary"]
    assert len(ok(owner.get(f"{base}/prompts/{prompt_id}/versions"))) == before + 1
    again = ok(
        owner.post(
            f"{base}/sync/apply", headers=csrf(owner), json={"bundle": edited, "dry_run": False}
        )
    )
    assert again["report"] is None  # nothing left to send
    assert len(ok(owner.get(f"{base}/prompts/{prompt_id}/versions"))) == before + 1

    # The same through one zip, and a file that is not an export.
    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, "w") as zf:
        for p, data in files.items():
            zf.writestr(p, data)
    again = ok(
        owner.post(
            f"{base}/sync/read",
            headers=csrf(owner),
            files=[("files", ("export.zip", zipped.getvalue()))],
        )
    )
    assert any(i["name"] == name for i in again["items"])
    bad = owner.post(f"{base}/sync/read", headers=csrf(owner), files=[("files", ("x.json", b"{}"))])
    assert bad.status_code == 422
    assert bad.json()["code"] == "not_an_export"


def test_forgectl_eval_gates_a_pipeline_on_the_pass_rate(
    client: TestClient,
    env: dict[str, Any],
    tmp_path: Path,
) -> None:
    owner, base = env["owner"], env["base"]
    version = {
        "model": env["models"]["chat"]["name"],
        "system_template": "You greet.",
        "user_template": "Say hi to {customer}",
        "variables": [],
    }
    prompt = ok(
        owner.post(
            f"{base}/prompts",
            headers=csrf(owner),
            json={"name": f"Greet {uid()}", "description": "eval", "version": version},
        ),
        201,
    )
    # The echo model returns the messages as JSON, so the user text is in the output.
    cases = [
        {"input": {"customer": "Anna"}, "expected": "Say hi to Anna"},
        {"input": {"customer": "Luca"}, "expected": "Say hi to Mia"},  # fails
    ]
    dataset = ok(
        owner.post(
            f"{base}/evals/datasets",
            headers=csrf(owner),
            json={"name": f"Greetings {uid()}", "kind": "prompt", "cases": cases},
        ),
        201,
    )
    spec = tmp_path / "evaluators.json"
    spec.write_text('[{"type": "contains", "case_sensitive": true}]')
    forge = key_client(client, env, ["asset.write"])
    argv = ["eval", dataset["name"], prompt["name"], "--evaluators", str(spec)]

    code, text = run_cli(forge, *argv)
    assert code == 0, text  # no gate: reports only
    assert "passed 1/2" in text
    assert "FAIL case 2" in text

    code, text = run_cli(forge, *argv, "--fail-under", "1.0")
    assert code == 1
    code, _ = run_cli(forge, *argv, "--fail-under", "0.5")
    assert code == 0

    first = forge.eval_run(dataset["name"], prompt["name"], json.loads(spec.read_text()))
    code, text = run_cli(forge, *argv, "--baseline", first["id"], "--no-regressions")
    assert code == 0, text
    assert "0 regressed" in text

    code, _ = run_cli(forge, "eval", "nope", prompt["name"], "--evaluators", str(spec))
    assert code == 2
