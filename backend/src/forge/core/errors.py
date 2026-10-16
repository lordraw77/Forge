"""RFC 9457 problem details (ADR-011)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

PROBLEM_JSON = "application/problem+json"


class ProblemError(Exception):
    """Raise from any layer to return a problem+json response."""

    def __init__(self, status: int, code: str, title: str, detail: str | None = None) -> None:
        super().__init__(title)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail


def _problem(
    request: Request, status: int, code: str, title: str, detail: Any = None
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"https://forge.dev/problems/{code}",
        "title": title,
        "status": status,
        "code": code,
        "instance": request.url.path,
    }
    if detail is not None:
        body["detail"] = detail
    trace_id = request.headers.get("x-request-id")
    if trace_id:
        body["trace_id"] = trace_id
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


def install_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _forge_error(request: Request, exc: ProblemError) -> JSONResponse:
        return _problem(request, exc.status, exc.code, exc.title, exc.detail)

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return _problem(request, exc.status_code, f"http_{exc.status_code}", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()
        ]
        return _problem(request, 422, "validation_error", "Request validation failed", errors)
