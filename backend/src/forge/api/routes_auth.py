"""Authentication, first-login steps and the current user (US-1.2.x)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from forge.api.deps import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    REFRESH_COOKIE,
    Principal,
    principal,
    request_context,
    services,
)
from forge.api.schemas import (
    CodeIn,
    EulaAcceptIn,
    EulaOut,
    FlagsOut,
    LoginIn,
    LoginOut,
    LoginTotpIn,
    MeOut,
    MyProjectOut,
    PasswordChangeIn,
    RecoveryCodesOut,
    TotpEnrollOut,
    UserOut,
)
from forge.core.errors import ProblemError
from forge.iam.service import IssuedSession
from forge.iam.tokens import ACCESS_TTL_S

router = APIRouter(prefix="/api/v1", tags=["auth"])
AnyPrincipal = Annotated[Principal, Depends(principal)]
REFRESH_PATH = "/api/v1/auth"


def set_cookies(request: Request, response: Response, issued: IssuedSession) -> None:
    secure = services(request).settings.secure_cookies
    response.set_cookie(
        ACCESS_COOKIE,
        issued.access_token,
        max_age=ACCESS_TTL_S,
        path="/",
        httponly=True,
        secure=secure,
        samesite="lax",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        issued.refresh_token,
        max_age=issued.idle_seconds,
        path=REFRESH_PATH,
        httponly=True,
        secure=secure,
        samesite="lax",
    )
    response.set_cookie(
        CSRF_COOKIE,
        issued.csrf_token,
        max_age=issued.idle_seconds,
        path="/",
        httponly=False,
        secure=secure,
        samesite="lax",
    )


def _clear_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_PATH)
    response.delete_cookie(CSRF_COOKIE, path="/")


def _logged_in(request: Request, issued: IssuedSession) -> LoginOut:
    flags = services(request).auth.flags(issued.user)
    return LoginOut(
        status="ok", user=UserOut.model_validate(issued.user), flags=FlagsOut.model_validate(flags)
    )


@router.post("/auth/login", response_model=LoginOut)
async def login(body: LoginIn, request: Request, response: Response) -> LoginOut:
    outcome = await services(request).auth.login(
        body.login, body.password, request_context(request)
    )
    if outcome.challenge:
        return LoginOut(status="totp_required", challenge=outcome.challenge)
    assert outcome.session
    set_cookies(request, response, outcome.session)
    return _logged_in(request, outcome.session)


@router.post("/auth/login/totp", response_model=LoginOut)
async def login_totp(body: LoginTotpIn, request: Request, response: Response) -> LoginOut:
    if not body.code and not body.recovery_code:
        raise ProblemError(422, "code_required", "Provide a TOTP code or a recovery code")
    issued = await services(request).auth.login_totp(
        body.challenge, body.code, body.recovery_code, request_context(request)
    )
    set_cookies(request, response, issued)
    return _logged_in(request, issued)


@router.post("/auth/refresh", status_code=status.HTTP_204_NO_CONTENT)
async def refresh(request: Request, response: Response) -> Response:
    token = request.cookies.get(REFRESH_COOKIE)
    csrf = request.cookies.get(CSRF_COOKIE)
    if not token:
        raise ProblemError(401, "session_expired", "Session expired")
    if not csrf or request.headers.get("x-csrf-token") != csrf:
        raise ProblemError(403, "csrf_failed", "Missing or invalid CSRF token")
    issued = await services(request).auth.refresh(token, request_context(request))
    response.status_code = status.HTTP_204_NO_CONTENT
    set_cookies(request, response, issued)
    return response


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(p: AnyPrincipal, request: Request, response: Response) -> Response:
    if p.session_id:
        await services(request).auth.logout(p.session_id, p.actor)
    response.status_code = status.HTTP_204_NO_CONTENT
    _clear_cookies(response)
    return response


@router.post("/auth/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(body: PasswordChangeIn, p: AnyPrincipal, request: Request) -> None:
    if p.user is None:
        raise ProblemError(403, "forbidden", "Only users can change passwords")
    await services(request).auth.change_password(
        p.user.id, body.current_password, body.new_password, request_context(request)
    )


@router.get("/auth/eula", response_model=EulaOut)
async def get_eula(_p: AnyPrincipal, request: Request) -> EulaOut:
    return EulaOut.model_validate(services(request).auth.eula())


@router.post("/auth/eula", status_code=status.HTTP_204_NO_CONTENT)
async def accept_eula(body: EulaAcceptIn, p: AnyPrincipal, request: Request) -> None:
    assert p.user
    await services(request).auth.accept_eula(p.user.id, body.version, request_context(request))


@router.post("/auth/totp/enroll", response_model=TotpEnrollOut)
async def totp_enroll(p: AnyPrincipal, request: Request) -> TotpEnrollOut:
    assert p.user
    secret, uri = await services(request).auth.totp_enroll(p.user.id, request_context(request))
    return TotpEnrollOut(secret=secret, otpauth_uri=uri)


@router.post("/auth/totp/confirm", response_model=RecoveryCodesOut)
async def totp_confirm(body: CodeIn, p: AnyPrincipal, request: Request) -> RecoveryCodesOut:
    assert p.user
    codes = await services(request).auth.totp_confirm(
        p.user.id, body.code, request_context(request)
    )
    return RecoveryCodesOut(recovery_codes=codes)


@router.post("/auth/totp/disable", status_code=status.HTTP_204_NO_CONTENT)
async def totp_disable(body: CodeIn, p: AnyPrincipal, request: Request) -> None:
    assert p.user
    await services(request).auth.totp_disable(p.user.id, body.code, request_context(request))


@router.get("/me", response_model=MeOut, tags=["me"])
async def me(p: AnyPrincipal, request: Request) -> MeOut:
    if p.user is None:
        raise ProblemError(403, "forbidden", "API keys have no user profile")
    projects = await services(request).tenancy.my_projects(p.user.id)
    return MeOut(
        user=UserOut.model_validate(p.user),
        flags=FlagsOut.model_validate(p.flags),
        projects=[
            MyProjectOut(
                **{
                    k: getattr(s.project, k)
                    for k in ("id", "name", "description", "status", "created_at")
                },
                role_name=s.role_name,
                role_type=s.role_type,
                permissions=s.permissions,
            )
            for s in projects
        ],
    )
