"""Liveness, readiness and version endpoints."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from forge import __version__

router = APIRouter()

CheckState = Literal["ok", "fail"]


class Liveness(BaseModel):
    status: Literal["ok"] = "ok"


class Readiness(BaseModel):
    status: CheckState
    checks: dict[str, CheckState]


class VersionInfo(BaseModel):
    name: str = "forge"
    version: str


@router.get("/healthz", response_model=Liveness, tags=["health"])
async def healthz() -> Liveness:
    """The process is up. Never touches dependencies, so restarts are not triggered by them."""
    return Liveness()


@router.get("/readyz", response_model=Readiness, tags=["health"])
async def readyz(request: Request, response: Response) -> Readiness:
    """Dependencies needed to serve traffic are reachable."""
    checks: dict[str, CheckState] = {
        "database": await _check(_ping_db(request)),
        "redis": await _check(request.app.state.redis.ping()),
        "search": await _check(request.app.state.search.health()),
        "initialised": "ok" if request.app.state.initialised else "fail",
    }
    ready = all(v == "ok" for v in checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(status="ok" if ready else "fail", checks=checks)


@router.get("/api/v1/version", response_model=VersionInfo, tags=["system"])
async def version() -> VersionInfo:
    return VersionInfo(version=__version__)


async def _ping_db(request: Request) -> None:
    async with request.app.state.engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check(awaitable: Awaitable[object], timeout_s: float = 2.0) -> CheckState:
    try:
        await asyncio.wait_for(awaitable, timeout_s)
    except Exception:
        return "fail"
    return "ok"
