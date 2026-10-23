"""E3.2 evaluators and dataset parsing (no database)."""

from __future__ import annotations

import pytest

from forge.core.errors import ProblemError
from forge.evals import evaluators as ev
from forge.evals.service import parse_cases, summarise


def test_exact_and_contains_ignore_case_and_spaces_by_default() -> None:
    assert ev.deterministic({"type": "exact"}, "  Paris \n", "paris").passed  # type: ignore[union-attr]
    assert not ev.deterministic({"type": "exact"}, "Paris, France", "paris").passed  # type: ignore[union-attr]
    assert ev.deterministic({"type": "contains"}, "It is Paris.", "paris").passed  # type: ignore[union-attr]
    strict = {"type": "exact", "case_sensitive": True}
    assert not ev.deterministic(strict, "paris", "Paris").passed  # type: ignore[union-attr]


def test_no_expected_answer_skips() -> None:
    assert ev.deterministic({"type": "exact"}, "x", None) is None
    assert ev.deterministic({"type": "regex"}, "x", None) is None


def test_regex_takes_pattern_or_expected() -> None:
    assert ev.deterministic({"type": "regex", "pattern": r"^\d{3}$"}, "123", None).passed  # type: ignore[union-attr]
    assert ev.deterministic({"type": "regex"}, "order 42", r"\d+").passed  # type: ignore[union-attr]
    with pytest.raises(ev.EvaluatorError):
        ev.validate("prompt", {"type": "regex", "pattern": "("})


def test_json_schema_reads_fenced_json_and_names_the_failure() -> None:
    spec = {"type": "json_schema", "schema": {"type": "object", "required": ["answer"]}}
    assert ev.deterministic(spec, '```json\n{"answer": 1}\n```', None).passed  # type: ignore[union-attr]
    bad = ev.deterministic(spec, "{}", None)
    assert bad is not None
    assert not bad.passed
    assert "answer" in bad.detail
    assert "not JSON" in ev.deterministic(spec, "plain", None).detail  # type: ignore[union-attr]


def test_recall_and_mrr_match_file_names() -> None:
    retrieved = ["docs/a.md", "B.pdf", "c.txt"]
    r = ev.retrieval({"type": "recall_at_k", "k": 2}, retrieved, ["a.md", "c.txt"])
    assert r is not None
    assert r.score == 0.5
    assert not r.passed
    r = ev.retrieval({"type": "recall_at_k", "k": 3}, retrieved, ["A.md", "c.txt"])
    assert r is not None
    assert r.score == 1.0
    assert r.passed
    m = ev.retrieval({"type": "mrr"}, retrieved, ["b.pdf"])
    assert m is not None
    assert m.score == 0.5
    assert ev.retrieval({"type": "mrr"}, retrieved, []) is None
    assert ev.retrieval({"type": "mrr"}, [], ["a"]).score == 0.0  # type: ignore[union-attr]


def test_judge_reply_parsing() -> None:
    spec = {"type": "judge", "model": "m", "criteria": "polite", "threshold": 0.8}
    assert ev.parse_judge('noise {"score": 0.9, "reason": "ok"}', spec).passed
    s = ev.parse_judge('{"score": 7}', spec)
    assert s.score == 1.0  # clamped
    assert not ev.parse_judge("no idea", spec).passed
    assert not ev.parse_judge('{"score": 0.5}', spec).passed


def test_validate_rejects_wrong_kind_and_missing_settings() -> None:
    with pytest.raises(ev.EvaluatorError):
        ev.validate("prompt", {"type": "recall_at_k"})
    with pytest.raises(ev.EvaluatorError):
        ev.validate("rag", {"type": "faithfulness"})  # no judge model
    with pytest.raises(ev.EvaluatorError):
        ev.validate("prompt", {"type": "judge", "model": "m"})  # no criteria
    with pytest.raises(ev.EvaluatorError):
        ev.validate("prompt", {"type": "exact", "threshold": 2})
    ev.validate("rag", {"type": "faithfulness", "model": "m"})


def test_parse_cases_csv_and_jsonl() -> None:
    csv_text = "customer,topic,expected\nAnna,billing,refund\nLuca,tech,\n"
    cases = parse_cases("prompt", "csv", csv_text)
    assert cases[0].input == {"customer": "Anna", "topic": "billing"}
    assert cases[0].expected == "refund"
    assert cases[1].expected is None
    rag = parse_cases("rag", "csv", "query,expected_sources\nhow?,a.md;b.md\n")
    assert rag[0].input == {"query": "how?"}
    assert rag[0].expected_sources == ["a.md", "b.md"]
    lines = '{"input": {"query": "q"}, "expected": "e"}\n\n{"input": {"query": "z"}}\n'
    assert len(parse_cases("rag", "jsonl", lines)) == 2
    with pytest.raises(ProblemError):
        parse_cases("rag", "jsonl", "not json")
    with pytest.raises(ProblemError):
        parse_cases("rag", "csv", "query\n\n,\n")


def test_summary_means_and_percentiles() -> None:
    from decimal import Decimal

    from forge.evals.models import EvalResult

    def row(passed: bool, score: float | None, ms: int, error: str | None = None) -> EvalResult:
        return EvalResult(
            passed=passed,
            error=error,
            scores={"exact": {"score": score, "passed": passed}} if not error else {},
            tokens=10,
            cost=Decimal("0.5"),
            latency_ms=ms,
        )

    rows = [row(True, 1.0, 100), row(False, 0.0, 300), row(False, 0.0, 0, "boom")]
    s = summarise(rows, [{"type": "exact"}])
    assert (s["cases"], s["passed"], s["failed"], s["errors"]) == (3, 1, 1, 1)
    assert s["pass_rate"] == 0.3333
    assert s["evaluators"]["exact"]["mean"] == 0.5
    assert s["tokens"] == 30
    assert s["cost"] == "1.5"
    assert s["latency_p95_ms"] == 300
