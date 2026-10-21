from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from forge_sdk import ForgeClient, ForgeError, cli, gitops
from tests_support import bundle, prompt

API = "https://forge.test"


def client_for(handler: Any) -> ForgeClient:
    return ForgeClient(API, "fk_k", "p1", http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_errors_carry_the_problem_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer fk_k"
        return httpx.Response(403, json={"code": "forbidden", "title": "Permission required"})

    with pytest.raises(ForgeError) as err:
        client_for(handler).export_bundle()
    assert err.value.status == 403
    assert err.value.code == "forbidden"


def test_export_asks_for_everything_and_project_id_comes_from_whoami() -> None:
    seen: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        if request.url.path == "/api/v1/whoami":
            return httpx.Response(200, json={"project_id": "p9"})
        return httpx.Response(200, json=bundle())

    c = ForgeClient(API, "fk_k", http=httpx.Client(transport=httpx.MockTransport(handler)))
    c.export_bundle(versions="published", config=True)
    path, body = seen[-1][1], seen[-1][2]
    assert path == "/api/v1/projects/p9/export"
    assert body["everything"] is True
    assert body["everything_versions"] == "published"
    assert body["guardrail_policy"] is True


def test_run_waits_until_the_run_ends() -> None:
    states = iter(["queued", "running", "succeeded"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/catalog/use-cases"):
            return httpx.Response(200, json=[{"id": "u1", "name": "Review"}])
        if request.method == "POST":
            return httpx.Response(202, json={"id": "r1", "status": "queued"})
        return httpx.Response(200, json={"id": "r1", "status": next(states), "outputs": {"a": 1}})

    run = client_for(handler).run_use_case("Review", {"x": "1"}, poll=0)
    assert run["status"] == "succeeded"
    assert run["outputs"] == {"a": 1}
    with pytest.raises(ForgeError, match="No runnable use case"):
        client_for(handler).run_use_case("Nope")


class FakeServer:
    """Answers export from `remote` and records imports."""

    def __init__(self, remote: dict[str, Any]) -> None:
        self.remote, self.imports = remote, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path.endswith("/export"):
            return httpx.Response(200, json=self.remote)
        self.imports.append(body)
        items = [
            {"kind": i["kind"], "name": i["name"], "action": "new_versions"}
            for i in body["bundle"]["items"]
        ]
        return httpx.Response(200, json={"dry_run": body["dry_run"], "items": items})


def run_cli(monkeypatch: pytest.MonkeyPatch, server: FakeServer, *argv: str) -> tuple[int, str]:
    monkeypatch.setattr(
        cli,
        "_client",
        lambda args: ForgeClient(
            API, "fk_k", "p1", http=httpx.Client(transport=httpx.MockTransport(server))
        ),
    )
    out = io.StringIO()
    code = cli.main(list(argv), out)
    return code, out.getvalue()


def test_export_diff_apply_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    server = FakeServer(bundle(prompt("A"), prompt("B")))
    code, text = run_cli(monkeypatch, server, "export", str(tmp_path))
    assert code == 0
    assert "2 items written" in text

    code, text = run_cli(monkeypatch, server, "diff", str(tmp_path), "--exit-code")
    assert code == 0
    assert "0 to apply" in text

    # A reviewed change in Git: B's template, and a new prompt C.
    changed = json.loads((tmp_path / "prompts/B.json").read_text())
    changed["versions"][0]["user_template"] = "New text for {name}"
    (tmp_path / "prompts/B.json").write_text(json.dumps(changed))
    (tmp_path / "prompts/C.json").write_text(json.dumps(prompt("C")))

    code, text = run_cli(monkeypatch, server, "diff", str(tmp_path), "--exit-code")
    assert code == 1
    assert "[changed]" in text
    assert "[new]" in text
    assert "[unchanged]" not in text

    code, text = run_cli(monkeypatch, server, "apply", str(tmp_path), "--dry-run")
    assert code == 0
    assert "Dry run" in text
    assert server.imports[-1]["dry_run"] is True
    assert [i["name"] for i in server.imports[-1]["bundle"]["items"]] == ["B", "C"]

    code, _ = run_cli(monkeypatch, server, "apply", str(tmp_path))
    assert code == 0
    assert server.imports[-1]["dry_run"] is False


def test_apply_with_nothing_to_do_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    server = FakeServer(bundle(prompt("A")))
    gitops.write_tree(server.remote, tmp_path)
    code, text = run_cli(monkeypatch, server, "apply", str(tmp_path))
    assert code == 0
    assert "Nothing to apply" in text
    assert server.imports == []


def test_apply_fails_when_an_item_cannot_be_imported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    server = FakeServer(bundle())
    gitops.write_tree(bundle(prompt("A")), tmp_path)

    def failing(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/export"):
            return httpx.Response(200, json=server.remote)
        return httpx.Response(
            200,
            json={
                "items": [{"kind": "prompt", "name": "A", "action": "error", "error": "no model"}]
            },
        )

    code, _text = run_cli(monkeypatch, FakeServer(bundle()), "diff", str(tmp_path))
    assert code == 0
    monkeypatch.setattr(
        cli,
        "_client",
        lambda args: ForgeClient(
            API, "k", "p1", http=httpx.Client(transport=httpx.MockTransport(failing))
        ),
    )
    out = io.StringIO()
    assert cli.main(["apply", str(tmp_path)], out) == 1
    assert "ERROR: no model" in out.getvalue()


def test_missing_settings_and_bad_inputs_exit_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("FORGE_URL", raising=False)
    monkeypatch.delenv("FORGE_API_KEY", raising=False)
    assert cli.main(["whoami"], io.StringIO()) == 2
    assert "FORGE_URL" in capsys.readouterr().err
    with pytest.raises(ForgeError):
        cli._parse_inputs(["novalue"], None)
    assert cli._parse_inputs(["a=1", "b=x=y"], None) == {"a": "1", "b": "x=y"}
