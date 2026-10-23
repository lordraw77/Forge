from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Kind = Literal["prompt", "rag"]
MAX_CASES = 1000


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class EvalCaseIn(In):
    """Prompt datasets: `input` holds the prompt's variables. RAG datasets: `{"query": "…"}`."""

    input: dict[str, Any] = Field(max_length=50)
    expected: str | None = Field(default=None, max_length=20000)
    expected_sources: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        default_factory=list, max_length=50
    )


class EvalCaseOut(Out):
    id: uuid.UUID
    position: int
    input: dict[str, Any]
    expected: str | None
    expected_sources: list[str]


class EvalDatasetIn(In):
    name: str = Field(min_length=1, max_length=120)
    kind: Kind
    description: str = Field(default="", max_length=2000)
    cases: list[EvalCaseIn] = Field(default_factory=list, max_length=MAX_CASES)


class EvalDatasetUpdateIn(In):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    cases: list[EvalCaseIn] | None = Field(default=None, max_length=MAX_CASES)


class EvalImportIn(In):
    format: Literal["jsonl", "csv"]
    text: str = Field(min_length=1, max_length=5_000_000)
    replace: bool = False


class EvalDatasetOut(Out):
    id: uuid.UUID
    name: str
    kind: Kind
    description: str
    cases: int
    created_at: datetime
    updated_at: datetime


class EvalDatasetDetail(EvalDatasetOut):
    items: list[EvalCaseOut]


class EvalRunIn(In):
    dataset_id: uuid.UUID
    target_id: uuid.UUID = Field(description="The prompt or RAG pipeline to evaluate")
    target_version: int | None = Field(default=None, ge=1, description="Default: the latest")
    model: str | None = Field(default=None, max_length=100, description="Prompt only: override")
    name: str = Field(default="", max_length=120)
    evaluators: list[dict[str, Any]] = Field(min_length=1, max_length=8)
    concurrency: int = Field(default=2, ge=1, le=8)


class EvalRunOut(Out):
    id: uuid.UUID
    dataset_id: uuid.UUID | None
    dataset_name: str
    kind: Kind
    name: str
    target_id: uuid.UUID
    target_name: str
    target_version: int | None
    model: str | None
    evaluators: list[dict[str, Any]]
    status: Literal["queued", "running", "done", "cancelled", "interrupted"]
    total: int
    completed: int
    summary: dict[str, Any]
    created_at: datetime
    created_by: uuid.UUID | None
    finished_at: datetime | None


class EvalResultOut(Out):
    id: uuid.UUID
    case_id: uuid.UUID | None
    position: int
    input: dict[str, Any]
    expected: str | None
    output: str | None
    scores: dict[str, Any]
    passed: bool
    error: str | None
    tokens: int
    cost: Decimal
    latency_ms: int
    source_run: uuid.UUID | None


class EvalRunDetail(EvalRunOut):
    results: list[EvalResultOut]


class EvalCaseDiff(Out):
    position: int
    input: dict[str, Any]
    a_passed: bool | None
    b_passed: bool | None
    change: Literal["regressed", "improved", "same", "only_a", "only_b"]
    a_output: str | None
    b_output: str | None


class EvalComparison(Out):
    a: EvalRunOut
    b: EvalRunOut
    pass_rate_delta: float | None
    evaluator_deltas: dict[str, float]
    regressions: int
    improvements: int
    cases: list[EvalCaseDiff]
