"""FastAPI composition root for the local WorkBench service."""

import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from hashlib import sha256

import uvicorn
from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import RequestResponseEndpoint

from app.api.auth import build_auth_router, clear_session_cookie
from app.api.chat import build_chat_router
from app.api.contracts import ErrorResponse
from app.api.health_contracts import HealthResponse, HealthStatus
from app.auth.service import AuthError, AuthService
from app.config import ApplicationSettings
from app.health import ApplicationDependencies, build_health_response
from app.storage import (
    LocalSQLiteDatabase,
    SQLiteAuditStore,
    SQLiteAuthSessionStore,
    SQLiteIdentityStore,
    SQLiteWorkflowStore,
)


def _health_router(
    settings: ApplicationSettings, dependencies: ApplicationDependencies
) -> APIRouter:
    """Build the sole Phase 1 route with its injected readiness dependencies."""

    router = APIRouter(tags=["service"])

    @router.get("/internal/ready", response_model=None, include_in_schema=False)
    async def get_ready(request: Request) -> Response:
        """Prove possession of the launch capability to Electron main."""

        nonce = request.headers.get("x-workbench-readiness-nonce", "")
        if not nonce or len(nonce) > 512:
            return JSONResponse(status_code=400, content={"detail": "invalid readiness request"})
        capability = settings.local_service_capability
        if capability is None:
            return JSONResponse(
                status_code=503, content={"detail": "managed capability unavailable"}
            )
        proof = hmac.new(capability.encode(), nonce.encode(), sha256).hexdigest()
        return JSONResponse(content={"proof": proof})

    @router.get(
        "/health",
        response_model=HealthResponse,
        responses={503: {"model": HealthResponse, "description": "Local dependencies unavailable"}},
        summary="Report local service readiness",
    )
    async def get_health() -> HealthResponse | JSONResponse:
        response = await build_health_response(
            dependencies, timeout_seconds=settings.health_check_timeout_seconds
        )
        if response.status is HealthStatus.READY:
            return response
        return JSONResponse(
            status_code=503,
            content=response.model_dump(mode="json", by_alias=True),
        )

    return router


@asynccontextmanager
async def _lifespan(dependencies: ApplicationDependencies) -> AsyncIterator[None]:
    """Initialize and close composition-owned local resources."""

    if dependencies.startup is not None:
        await dependencies.startup()
    try:
        yield
    finally:
        if dependencies.shutdown is not None:
            await dependencies.shutdown()


def compose_runtime_dependencies(settings: ApplicationSettings) -> ApplicationDependencies:
    """Compose the local SQLite auth stores for a normal service process."""

    database = LocalSQLiteDatabase(settings.database_path)
    return ApplicationDependencies(
        identity_store=SQLiteIdentityStore(database),
        auth_session_store=SQLiteAuthSessionStore(database),
        audit_store=SQLiteAuditStore(database),
        chat_store=SQLiteWorkflowStore(database),
        startup=database.initialize,
    )


async def _validation_error_handler(request: Request, error: Exception) -> JSONResponse:
    """Keep validation failures stable without reflecting request contents."""

    del request, error
    body = ErrorResponse(code="validation_error", message="Request validation failed.")
    return JSONResponse(status_code=422, content=body.model_dump(mode="json", by_alias=True))


async def _unhandled_error_handler(request: Request, error: Exception) -> JSONResponse:
    """Avoid returning exception text, filesystem paths, or credentials to clients."""

    del request, error
    body = ErrorResponse(code="internal_error", message="Internal service error.")
    return JSONResponse(status_code=500, content=body.model_dump(mode="json", by_alias=True))


async def _auth_error_handler(request: Request, error: Exception) -> JSONResponse:
    """Return stable auth failures and clear only invalid client session cookies."""

    auth_error = error if isinstance(error, AuthError) else None
    if auth_error is None:
        return await _unhandled_error_handler(request, error)
    body = ErrorResponse(code=auth_error.code, message=auth_error.message)
    response = JSONResponse(
        status_code=auth_error.status_code,
        content=body.model_dump(mode="json", by_alias=True),
    )
    if auth_error.clear_cookie:
        clear_session_cookie(response, request.app.state.auth_service)
    return response


def create_app(
    *,
    settings: ApplicationSettings | None = None,
    dependencies: ApplicationDependencies | None = None,
) -> FastAPI:
    """Create a local-only FastAPI app without probing or constructing dependencies."""

    resolved_settings = settings or ApplicationSettings()
    _ = resolved_settings.signing_secret
    resolved_dependencies = dependencies or compose_runtime_dependencies(resolved_settings)
    application = FastAPI(
        title="WorkBench Local AI Service",
        version="v1",
        lifespan=lambda _: _lifespan(resolved_dependencies),
    )
    @application.middleware("http")
    async def require_managed_capability(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Readiness proves the child received Electron's launch environment. Sending that
        # capability to an unverified listener would defeat the proof, so this one route
        # accepts only Electron's fresh nonce.
        if request.url.path == "/internal/ready":
            if request.headers.get("origin") == "null":
                return JSONResponse(
                    status_code=403, content={"detail": "request origin is not allowed"}
                )
            return await call_next(request)
        capability = resolved_settings.local_service_capability
        if capability is not None and not hmac.compare_digest(
            request.headers.get("x-workbench-capability", ""), capability
        ):
            return JSONResponse(status_code=403, content={"detail": "managed capability required"})
        return await call_next(request)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Accept", "Content-Type"],
    )
    application.state.allowed_origins = resolved_settings.cors_origins
    application.state.audit_store = resolved_dependencies.audit_store
    application.state.auth_service = AuthService(
        settings=resolved_settings,
        identity_store=resolved_dependencies.identity_store,
        auth_session_store=resolved_dependencies.auth_session_store,
        audit_store=resolved_dependencies.audit_store,
    )
    application.state.chat_store = resolved_dependencies.chat_store
    application.add_exception_handler(RequestValidationError, _validation_error_handler)
    application.add_exception_handler(AuthError, _auth_error_handler)
    application.add_exception_handler(Exception, _unhandled_error_handler)
    application.include_router(_health_router(resolved_settings, resolved_dependencies))
    application.include_router(build_auth_router(resolved_settings))
    application.include_router(build_chat_router())
    return application


app = create_app()


def run() -> None:
    """Start Uvicorn only on the validated local bind address."""

    settings = ApplicationSettings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()
