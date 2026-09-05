"""Employee login, restoration, and logout routes; no workflow routes live here."""

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request, Response

from app.api.auth_contracts import (
    EmployeeIdentityResponse,
    EmployeeLoginRequest,
    EmployeeLogoutResponse,
    EmployeeSessionEnvelope,
    EmployeeSessionResponse,
)
from app.api.contracts import ErrorResponse
from app.auth.contracts import AuthenticatedUser, UserRole
from app.auth.service import AuthService, get_authenticated_user
from app.config import ApplicationSettings
from app.workflow.contracts import UtcTimestamp


def _require_request_origin(request: Request) -> None:
    """Reject cross-origin cookie calls even if a browser CORS check is bypassed."""

    allowed_origins = cast(tuple[str, ...], request.app.state.allowed_origins)
    if request.headers.get("origin") not in allowed_origins:
        from app.auth.service import AuthError

        raise AuthError(
            status_code=403,
            code="invalid_origin",
            message="The request origin is not allowed.",
            clear_cookie=False,
        )


AllowedOrigin = Annotated[None, Depends(_require_request_origin)]
CurrentEmployee = Annotated[AuthenticatedUser, Depends(get_authenticated_user)]

_LOGIN_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Invalid employee credentials"},
    403: {"model": ErrorResponse, "description": "Request origin or employee role is not allowed"},
    422: {"model": ErrorResponse, "description": "Request validation failed"},
    503: {"model": ErrorResponse, "description": "Local authentication dependency unavailable"},
}
_SESSION_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Invalid employee session"},
    403: {"model": ErrorResponse, "description": "Request origin is not allowed"},
    503: {"model": ErrorResponse, "description": "Local authentication dependency unavailable"},
}
_LOGOUT_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse, "description": "Request origin is not allowed"},
    503: {"model": ErrorResponse, "description": "Local authentication dependency unavailable"},
}


def _session_envelope(user: AuthenticatedUser, expires_at: UtcTimestamp) -> EmployeeSessionEnvelope:
    """Map only safe server identity facts into the exact desktop envelope."""

    return EmployeeSessionEnvelope(
        session=EmployeeSessionResponse(
            session_id=user.auth_session_id,
            user=EmployeeIdentityResponse(
                employee_id=user.user_id,
                username=user.username,
                display_name=user.display_name,
                role=UserRole.EMPLOYEE,
            ),
            expires_at=expires_at,
        )
    )


def _set_session_cookie(
    response: Response, service: AuthService, token: str, expires_at: UtcTimestamp
) -> None:
    """Set the sole HttpOnly, host-only session cookie without exposing its value elsewhere."""

    response.set_cookie(
        key=service.cookie_name,
        value=token,
        max_age=service.cookie_max_age(),
        expires=expires_at,
        path="/",
        secure=service.cookie_secure,
        httponly=True,
        samesite="strict",
    )


def clear_session_cookie(response: Response, service: AuthService) -> None:
    """Delete the cookie using exactly the same host-only path and security attributes."""

    response.delete_cookie(
        key=service.cookie_name,
        path="/",
        secure=service.cookie_secure,
        httponly=True,
        samesite="strict",
    )


def build_auth_router(settings: ApplicationSettings) -> APIRouter:
    """Build the complete Phase 2 employee authentication surface."""

    del settings
    router = APIRouter(prefix="/auth", tags=["authentication"])

    @router.post(
        "/login", response_model=EmployeeSessionEnvelope, responses=_LOGIN_ERROR_RESPONSES
    )
    async def login(
        payload: EmployeeLoginRequest,
        response: Response,
        _: AllowedOrigin,
        request: Request,
    ) -> EmployeeSessionEnvelope:
        service = cast(AuthService, request.app.state.auth_service)
        issued = await service.login(username=payload.username, password=payload.password)
        user = AuthenticatedUser(
            user_id=issued.identity.user_id,
            username=issued.identity.username,
            display_name=issued.identity.display_name,
            role=issued.identity.role,
            auth_session_id=issued.auth_session_id,
        )
        _set_session_cookie(response, service, issued.token, issued.expires_at)
        return _session_envelope(user, issued.expires_at)

    @router.get(
        "/session", response_model=EmployeeSessionEnvelope, responses=_SESSION_ERROR_RESPONSES
    )
    async def restore_session(
        _: AllowedOrigin,
        user: CurrentEmployee,
        request: Request,
    ) -> EmployeeSessionEnvelope:
        service = cast(AuthService, request.app.state.auth_service)
        token = request.cookies.get(service.cookie_name)
        # The dependency already proved this token and server record agree; only its expiry is read.
        return _session_envelope(user, service.decode(token or "").expires_at)

    @router.post(
        "/logout", response_model=EmployeeLogoutResponse, responses=_LOGOUT_ERROR_RESPONSES
    )
    async def logout(
        response: Response,
        _: AllowedOrigin,
        request: Request,
    ) -> EmployeeLogoutResponse:
        service = cast(AuthService, request.app.state.auth_service)
        try:
            revoked = await service.logout(request.cookies.get(service.cookie_name))
        finally:
            clear_session_cookie(response, service)
        return EmployeeLogoutResponse(revoked=revoked)

    return router
