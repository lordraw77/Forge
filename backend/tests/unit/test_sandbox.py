"""E3.4 pieces that need no container: code checks, runner protocol, log parsing, graph rules."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys

from forge.sandbox import runner
from forge.sandbox.server import MARKER, parse_result, split_logs
from forge.sandbox.service import check_code
from forge.usecases import graph as g


def test_check_code_wants_one_plain_function_named_run() -> None:
    assert check_code("def run(args):\n    return 1") is None
    assert "SyntaxError" in (check_code("def run(args) return 1") or "")
    assert "Define a function run" in (check_code("x = 1") or "")
    assert "exactly one" in (check_code("def run(a, b):\n    return 1") or "")
    assert "not async" in (check_code("async def run(args):\n    return 1") or "")
    assert "longer" in (check_code("#" * 50_000) or "")


def test_runner_executes_and_reports_without_raising() -> None:
    ok = runner.execute(
        {"code": "def run(a):\n    print('hi')\n    return a['n'] + 1", "args": {"n": 1}}
    )
    assert ok["ok"]
    assert ok["result"] == 2
    assert ok["stdout"] == "hi\n"
    bad = runner.execute({"code": "def run(a):\n    raise KeyError('x')", "args": {}})
    assert not bad["ok"]
    assert "KeyError" in bad["error"]
    assert "not JSON" in runner.execute({"code": "def run(a):\n    return {1, 2}"})["error"]
    big = runner.execute({"code": "def run(a):\n    print('x' * 10**6)\n    return 1"})
    assert len(big["stdout"]) == runner.CAPTURE_LIMIT


def test_runner_process_prints_the_result_last_and_hides_its_payload() -> None:
    code = "import os\ndef run(a):\n    return sorted(k for k in os.environ if 'PAYLOAD' in k)"
    payload = base64.b64encode(json.dumps({"code": code}).encode()).decode()
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-I", runner.__file__],
        env={**os.environ, "FORGE_PAYLOAD": payload},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    result = parse_result(out)
    assert result is not None
    assert result["result"] == []  # the payload variable is gone before the code runs


def test_parse_result_takes_the_last_marker_and_survives_noise() -> None:
    fake = MARKER + json.dumps({"ok": True, "result": "fake"})
    real = MARKER + json.dumps({"ok": True, "result": "real"})
    assert parse_result(f"noise\n{fake}\n{real}\n")["result"] == "real"  # type: ignore[index]
    assert parse_result("nothing here") is None
    assert parse_result(MARKER + "{broken") is None
    assert parse_result(MARKER + "[1]") is None


def test_split_logs_demultiplexes_docker_streams() -> None:
    def frame(stream: int, data: bytes) -> bytes:
        return bytes([stream, 0, 0, 0]) + len(data).to_bytes(4, "big") + data

    raw = frame(1, b"out1 ") + frame(2, b"err ") + frame(1, b"out2")
    assert split_logs(raw) == ("out1 out2", "err ")
    assert split_logs(b"") == ("", "")


def test_code_node_validation() -> None:
    nodes = [
        {"id": "start", "type": "start"},
        {"id": "c", "type": "code", "after": ["start"], "code": "def run(a):\n    return 1"},
        {"id": "end", "type": "end", "after": ["c"], "output": "{c}"},
    ]
    issues = g.analyze({"nodes": nodes}, ["topic"])
    assert not [i for i in issues if i.code in {"bad_code", "bad_node_type"}]
    nodes[1]["code"] = "def run(a) return"
    bad = g.analyze({"nodes": nodes}, ["topic"])
    assert "bad_code" in [i.code for i in bad]
    nodes[1]["code"], nodes[1]["memory_mb"] = "def run(a):\n    return 1", 10
    assert "bad_code" in [i.code for i in g.analyze({"nodes": nodes}, ["topic"])]
    assert "code" in g.EXECUTABLE
