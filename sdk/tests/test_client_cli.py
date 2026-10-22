from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from forge_sdk import ForgeClient, ForgeError, cli

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
        return httpx.Response(200, json={"items": []})

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


def make_zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for path, text in files.items():
            zf.writestr(path, text)
    return buffer.getvalue()


class FakeServer:
    """Canned answers for the sync endpoints; records what the CLI sent."""

    def __init__(
        self,
        *,
        zipped: bytes = b"",
        bundle: dict[str, Any] | None = None,
        plan: dict[str, Any] | None = None,
        report: dict[str, Any] | None = None,
    ) -> None:
        self.zipped = zipped
        self.bundle = bundle or {"format": "forge.bundle", "items": []}
        self.plan = plan or {"items": [], "summary": {}}
        self.report = report
        self.calls: list[tuple[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = request.content
        if request.url.path.endswith("/export/files"):
            self.calls.append(("export", json.loads(body)))
            return httpx.Response(200, content=self.zipped)
        if request.url.path.endswith("/sync/read"):
            self.calls.append(("read", len(body)))
            return httpx.Response(200, json=self.bundle)
        if request.url.path.endswith("/sync/plan"):
            self.calls.append(("plan", json.loads(body)))
            return httpx.Response(200, json=self.plan)
        self.calls.append(("apply", json.loads(body)))
        return httpx.Response(200, json={"plan": self.plan, "report": self.report})


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


def export_dir(tmp_path: Path) -> Path:
    (tmp_path / "prompts").mkdir()
    (tmp_path / "forge.json").write_text('{"format": "forge.bundle"}')
    (tmp_path / "prompts" / "A.json").write_text('{"kind": "prompt", "name": "A"}')
    return tmp_path


def test_export_writes_the_files_and_removes_stale_ones(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "Old.json").write_text("{}")
    (tmp_path / "README.md").write_text("mine")
    zipped = make_zip({"forge.json": "{}", "prompts/A.json": '{"name": "A"}'})
    server = FakeServer(zipped=zipped)
    code, text = run_cli(monkeypatch, server, "export", str(tmp_path), "--versions", "published")
    assert code == 0
    assert "1 items written" in text
    assert server.calls[0] == ("export", {"versions": "published", "config": False})
    assert (tmp_path / "prompts" / "A.json").exists()
    assert not (tmp_path / "prompts" / "Old.json").exists()
    assert (tmp_path / "README.md").read_text() == "mine"


def test_diff_prints_the_plan_and_can_fail_a_pipeline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = {
        "items": [
            {"kind": "prompt", "name": "A", "status": "unchanged", "versions_to_send": 0},
            {"kind": "prompt", "name": "B", "status": "changed", "versions_to_send": 2},
            {"kind": "prompt", "name": "C", "status": "new", "versions_to_send": 1},
            {"kind": "prompt", "name": "D", "status": "removed", "versions_to_send": 0},
        ],
        "summary": {"unchanged": 1, "changed": 1, "new": 1, "removed": 1},
    }
    server = FakeServer(plan=plan)
    code, text = run_cli(monkeypatch, server, "diff", str(export_dir(tmp_path)), "--exit-code")
    assert code == 1
    assert "[changed] +2 version(s)" in text
    assert "[new]" in text
    assert "[removed]" in text
    assert "[unchanged]" not in text
    assert "2 to apply, 1 only in the project" in text
    quiet = FakeServer(plan={"items": [], "summary": {"unchanged": 3}})
    assert run_cli(monkeypatch, quiet, "diff", str(tmp_path), "--exit-code")[0] == 0


def test_apply_sends_the_bundle_and_reports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report = {"items": [{"kind": "prompt", "name": "B", "action": "new_versions"}]}
    server = FakeServer(report=report, bundle={"format": "forge.bundle", "items": [{"name": "B"}]})
    code, text = run_cli(monkeypatch, server, "apply", str(export_dir(tmp_path)), "--dry-run")
    assert code == 0
    assert "Dry run" in text
    assert "new_versions" in text
    kind, body = server.calls[-1]
    assert kind == "apply"
    assert body["dry_run"] is True
    assert body["policy"] == "new_version"
    assert body["bundle"]["items"] == [{"name": "B"}]
    code, _ = run_cli(monkeypatch, server, "apply", str(tmp_path))
    assert code == 0
    assert server.calls[-1][1]["dry_run"] is False


def test_apply_with_nothing_to_do_and_with_a_failed_item(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    code, text = run_cli(monkeypatch, FakeServer(report=None), "apply", str(export_dir(tmp_path)))
    assert code == 0
    assert "Nothing to apply" in text
    failing = {"items": [{"kind": "prompt", "name": "A", "action": "error", "error": "no model"}]}
    code, text = run_cli(monkeypatch, FakeServer(report=failing), "apply", str(tmp_path))
    assert code == 1
    assert "ERROR: no model" in text


def test_a_directory_that_is_not_an_export_exits_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _ = run_cli(monkeypatch, FakeServer(), "diff", str(tmp_path))
    assert code == 2
    assert "not a Forge export" in capsys.readouterr().err


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
