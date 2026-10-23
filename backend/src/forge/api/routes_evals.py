"""Evaluations (E3.2): datasets, runs, comparison. Needs `asset.write` like the studios."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status

from forge.api.deps import ProjectContext, project_permission, services
from forge.evals.schemas import (
    EvalComparison,
    EvalDatasetDetail,
    EvalDatasetIn,
    EvalDatasetOut,
    EvalDatasetUpdateIn,
    EvalImportIn,
    EvalRunDetail,
    EvalRunIn,
    EvalRunOut,
    Kind,
)
from forge.evals.service import Evaluations

router = APIRouter(prefix="/api/v1/projects/{project_id}/evals", tags=["evaluations"])

Reader = Annotated[ProjectContext, Depends(project_permission("asset.write"))]
Writer = Annotated[ProjectContext, Depends(project_permission("asset.write", writable=True))]


def _svc(request: Request) -> Evaluations:
    return services(request).evals


@router.get("/datasets", response_model=list[EvalDatasetOut])
async def eval_list_datasets(
    ctx: Reader, request: Request, kind: Kind | None = None
) -> list[EvalDatasetOut]:
    return await _svc(request).list_datasets(ctx.actor, ctx.project_id, kind)


@router.post("/datasets", response_model=EvalDatasetDetail, status_code=status.HTTP_201_CREATED)
async def eval_create_dataset(
    body: EvalDatasetIn, ctx: Writer, request: Request
) -> EvalDatasetDetail:
    return await _svc(request).create_dataset(ctx.actor, ctx.project_id, body)


@router.get("/datasets/{dataset_id}", response_model=EvalDatasetDetail)
async def eval_get_dataset(
    dataset_id: uuid.UUID, ctx: Reader, request: Request
) -> EvalDatasetDetail:
    return await _svc(request).get_dataset(ctx.actor, ctx.project_id, dataset_id)


@router.put("/datasets/{dataset_id}", response_model=EvalDatasetDetail)
async def eval_update_dataset(
    dataset_id: uuid.UUID, body: EvalDatasetUpdateIn, ctx: Writer, request: Request
) -> EvalDatasetDetail:
    return await _svc(request).update_dataset(ctx.actor, ctx.project_id, dataset_id, body)


@router.post("/datasets/{dataset_id}/import", response_model=EvalDatasetDetail)
async def eval_import_cases(
    dataset_id: uuid.UUID, body: EvalImportIn, ctx: Writer, request: Request
) -> EvalDatasetDetail:
    return await _svc(request).import_cases(ctx.actor, ctx.project_id, dataset_id, body)


@router.delete("/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eval_delete_dataset(dataset_id: uuid.UUID, ctx: Writer, request: Request) -> Response:
    await _svc(request).delete_dataset(ctx.actor, ctx.project_id, dataset_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/runs", response_model=EvalRunOut, status_code=status.HTTP_202_ACCEPTED)
async def eval_start_run(body: EvalRunIn, ctx: Writer, request: Request) -> EvalRunOut:
    return await _svc(request).create_run(ctx.actor, ctx.project_id, body)


@router.get("/runs", response_model=list[EvalRunOut])
async def eval_list_runs(
    ctx: Reader,
    request: Request,
    dataset_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[EvalRunOut]:
    return await _svc(request).list_runs(ctx.actor, ctx.project_id, dataset_id, limit)


@router.get("/runs/{run_id}", response_model=EvalRunDetail)
async def eval_get_run(
    run_id: uuid.UUID, ctx: Reader, request: Request, failed_only: bool = False
) -> EvalRunDetail:
    return await _svc(request).get_run(ctx.actor, ctx.project_id, run_id, failed_only=failed_only)


@router.post("/runs/{run_id}/cancel", response_model=EvalRunOut)
async def eval_cancel_run(run_id: uuid.UUID, ctx: Writer, request: Request) -> EvalRunOut:
    return await _svc(request).cancel_run(ctx.actor, ctx.project_id, run_id)


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eval_delete_run(run_id: uuid.UUID, ctx: Writer, request: Request) -> Response:
    await _svc(request).delete_run(ctx.actor, ctx.project_id, run_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/runs/{run_id}/compare/{other_id}", response_model=EvalComparison)
async def eval_compare_runs(
    run_id: uuid.UUID, other_id: uuid.UUID, ctx: Reader, request: Request
) -> EvalComparison:
    """`run_id` is the baseline (a), `other_id` the candidate (b)."""
    return await _svc(request).compare(ctx.actor, ctx.project_id, run_id, other_id)
