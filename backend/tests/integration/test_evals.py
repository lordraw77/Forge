"""E3.2 evaluations of prompts against PostgreSQL, Valkey and the mock provider (US-3.2.x).
The RAG half lives in test_rag.py (it needs the full stack)."""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.integration.test_gateway import model, ok, provider
from tests.integration.test_platform_core import csrf
from tests.integration.test_prompts import (  # noqa: F401 - module fixtures
    admin,
    alias,
    calls,
    clean,
    client,
    mock,
    project,
    settings,
    studio,
    uid,
    version,
)


@pytest.fixture(scope="module")
def judge(admin: TestClient, mock: str) -> str:  # noqa: F811
    prov = provider(admin, mock)
    return model(admin, prov["id"], "p-judge", all_projects=True)["name"]  # type: ignore[no-any-return]


def evals(s: dict[str, Any]) -> str:
    return s["base"].replace("/prompts", "/evals")


def dataset(s: dict[str, Any], cases: list[dict[str, Any]], kind: str = "prompt") -> Any:
    c = s["owner"]
    body = {"name": f"Set {uid()}", "kind": kind, "cases": cases}
    return ok(c.post(f"{evals(s)}/datasets", headers=csrf(c), json=body), 201)


def start(s: dict[str, Any], **body: Any) -> Any:
    c = s["owner"]
    return ok(c.post(f"{evals(s)}/runs", headers=csrf(c), json=body), 202)


def finished(s: dict[str, Any], run_id: str) -> Any:
    deadline = time.time() + 30
    while time.time() < deadline:
        run = ok(s["owner"].get(f"{evals(s)}/runs/{run_id}"))
        if run["status"] not in ("queued", "running"):
            return run
        time.sleep(0.2)
    raise AssertionError("evaluation did not finish")


def pong_prompt(s: dict[str, Any]) -> Any:
    c = s["owner"]
    v = version(
        s["models"]["pong"],
        system_template="You greet.",
        user_template="Say hi to {customer}",
        variables=[],
    )
    body = {"name": f"Prompt {uid()}", "description": "eval target", "version": v}
    return ok(c.post(s["base"], headers=csrf(c), json=body), 201)


@pytest.mark.ac("US-3.2.1/1")
def test_dataset_crud_import_and_validation(studio: dict[str, Any]) -> None:  # noqa: F811
    c, base = studio["owner"], evals(studio)
    d = dataset(studio, [{"input": {"customer": "Anna"}, "expected": "pong"}])
    assert d["cases"] == 1
    assert d["items"][0]["position"] == 1
    csv_text = "customer,expected\nLuca,pong\nMia,pong\n"
    body = {"format": "csv", "text": csv_text}
    d = ok(c.post(f"{base}/datasets/{d['id']}/import", headers=csrf(c), json=body))
    assert [x["input"]["customer"] for x in d["items"]] == ["Anna", "Luca", "Mia"]
    d = ok(
        c.post(f"{base}/datasets/{d['id']}/import", headers=csrf(c), json=body | {"replace": True})
    )
    assert d["cases"] == 2
    assert (
        c.post(
            f"{base}/datasets", headers=csrf(c), json={"name": d["name"], "kind": "prompt"}
        ).status_code
        == 409
    )
    rag_bad = {"name": f"x{uid()}", "kind": "rag", "cases": [{"input": {"customer": "no query"}}]}
    assert c.post(f"{base}/datasets", headers=csrf(c), json=rag_bad).status_code == 422
    assert c.delete(f"{base}/datasets/{d['id']}", headers=csrf(c)).status_code == 204
    assert c.get(f"{base}/datasets/{d['id']}").status_code == 404


@pytest.mark.ac("US-3.2.2/1")
@pytest.mark.ac("US-3.2.2/2")
def test_run_scores_each_case_and_reports_errors(studio: dict[str, Any]) -> None:  # noqa: F811
    p = pong_prompt(studio)
    d = dataset(
        studio,
        [
            {"input": {"customer": "Anna"}, "expected": "pong from"},  # passes
            {"input": {"customer": "Luca"}, "expected": "something else"},  # fails
            {"input": {}, "expected": "pong"},  # missing variable: error
            {"input": {"customer": "Mia"}},  # no expected text: only the regex scores it
        ],
    )
    run = start(
        studio,
        dataset_id=d["id"],
        target_id=p["id"],
        evaluators=[{"type": "contains"}, {"type": "regex", "pattern": "^pong", "name": "starts"}],
    )
    assert run["status"] in ("queued", "running")
    assert run["total"] == 4
    assert run["target_version"] == 1
    assert run["target_name"] == p["name"]
    done = finished(studio, run["id"])
    assert done["status"] == "done"
    assert done["completed"] == 4
    s = done["summary"]
    assert (s["cases"], s["passed"], s["failed"], s["errors"]) == (4, 2, 1, 1)
    assert s["pass_rate"] == 0.5
    assert s["evaluators"]["contains"]["scored"] == 2
    assert s["evaluators"]["starts"]["scored"] == 3
    by_pos = {r["position"]: r for r in done["results"]}
    assert by_pos[1]["passed"]
    assert by_pos[1]["output"] == "pong from p-ok"
    assert by_pos[1]["source_run"]
    assert not by_pos[2]["passed"]
    assert by_pos[2]["scores"]["contains"]["score"] == 0.0
    assert "customer" in by_pos[3]["error"]
    assert by_pos[4]["scores"]["contains"]["score"] is None
    assert by_pos[4]["passed"]
    only_failed = ok(studio["owner"].get(f"{evals(studio)}/runs/{run['id']}?failed_only=true"))
    assert {r["position"] for r in only_failed["results"]} == {2, 3}


@pytest.mark.ac("US-3.2.2/3")
def test_judge_evaluator_uses_a_gateway_model(studio: dict[str, Any], judge: str) -> None:  # noqa: F811
    p = pong_prompt(studio)
    d = dataset(studio, [{"input": {"customer": "Anna"}}])
    run = start(
        studio,
        dataset_id=d["id"],
        target_id=p["id"],
        evaluators=[{"type": "judge", "model": judge, "criteria": "polite"}],
    )
    done = finished(studio, run["id"])
    r = done["results"][0]
    assert r["passed"]
    assert r["scores"]["judge"]["score"] == 0.9
    assert r["cost"] is not None
    assert r["tokens"] > 0


@pytest.mark.ac("US-3.2.3/1")
def test_compare_runs_shows_regressions(studio: dict[str, Any]) -> None:  # noqa: F811
    p = pong_prompt(studio)
    d = dataset(
        studio,
        [
            {"input": {"customer": "A"}, "expected": "pong"},
            {"input": {"customer": "B"}, "expected": "pong"},
        ],
    )
    base = finished(
        studio,
        start(studio, dataset_id=d["id"], target_id=p["id"], evaluators=[{"type": "contains"}])[
            "id"
        ],
    )
    # The second run overrides the model with one that fails every call.
    bad = finished(
        studio,
        start(
            studio,
            dataset_id=d["id"],
            target_id=p["id"],
            model=studio["models"]["fail"],
            evaluators=[{"type": "contains"}],
        )["id"],
    )
    assert base["summary"]["pass_rate"] == 1.0
    assert bad["summary"]["errors"] == 2
    c = ok(studio["owner"].get(f"{evals(studio)}/runs/{base['id']}/compare/{bad['id']}"))
    assert c["regressions"] == 2
    assert c["improvements"] == 0
    assert c["pass_rate_delta"] == -1.0
    assert {x["change"] for x in c["cases"]} == {"regressed"}


@pytest.mark.ac("US-3.2.2/4")
def test_invalid_runs_are_refused_and_runs_can_be_deleted(studio: dict[str, Any]) -> None:  # noqa: F811
    c = studio["owner"]
    p = pong_prompt(studio)
    d = dataset(studio, [{"input": {"customer": "A"}, "expected": "x"}])
    empty = dataset(studio, [])
    ids = {"dataset_id": d["id"], "target_id": p["id"]}
    for evaluators in (
        [{"type": "recall_at_k"}],  # RAG only
        [{"type": "judge", "model": "m"}],  # no criteria
        [{"type": "regex", "pattern": "("}],
        [{"type": "contains"}, {"type": "contains"}],  # same name twice
    ):
        r = c.post(f"{evals(studio)}/runs", headers=csrf(c), json=ids | {"evaluators": evaluators})
        assert r.status_code == 422, evaluators
    r = c.post(
        f"{evals(studio)}/runs",
        headers=csrf(c),
        json={"dataset_id": empty["id"], "target_id": p["id"], "evaluators": [{"type": "exact"}]},
    )
    assert r.status_code == 422
    run = finished(studio, start(studio, **ids, evaluators=[{"type": "exact"}])["id"])
    listed = ok(c.get(f"{evals(studio)}/runs?dataset_id={d['id']}"))
    assert [x["id"] for x in listed] == [run["id"]]
    assert c.delete(f"{evals(studio)}/runs/{run['id']}", headers=csrf(c)).status_code == 204
    assert c.post(f"{evals(studio)}/runs/{run['id']}/cancel", headers=csrf(c)).status_code == 404
