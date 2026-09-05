"""FastAPI composition root for the local WorkBench service."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.contracts import ErrorResponse
from app.api.health_contracts import HealthResponse, HealthStatus
from app.config import ApplicationSettings
from app.health import ApplicationDependencies, build_health_response


def _health_router(
    settings: ApplicationSettings, dependencies: ApplicationDependencies
) -> APIRouter:
    """Build the sole Phase 1 route with its injected readiness dependencies."""

    router = APIRouter(tags=["service"])

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
    """Run only composition-owned cleanup during a controlled application shutdown."""

    yield
    if dependencies.shutdown is not None:
        await dependencies.shutdown()


async def _validation_error_handler(
    request: Request, error: Exception
) -> JSONResponse:
    """Keep validation failures stable without reflecting request contents."""

    del request, error
    body = ErrorResponse(code="validation_error", message="Request validation failed.")
    return JSONResponse(status_code=422, content=body.model_dump(mode="json", by_alias=True))


async def _unhandled_error_handler(request: Request, error: Exception) -> JSONResponse:
    """Avoid returning exception text, filesystem paths, or credentials to clients."""

    del request, error
    body = ErrorResponse(code="internal_error", message="Internal service error.")
    return JSONResponse(status_code=500, content=body.model_dump(mode="json", by_alias=True))


def create_app(
    *,
    settings: ApplicationSettings | None = None,
    dependencies: ApplicationDependencies | None = None,
) -> FastAPI:
    """Create a local-only FastAPI app without probing or constructing dependencies."""

    resolved_settings = settings or ApplicationSettings()
    resolved_dependencies = dependencies or ApplicationDependencies()
    application = FastAPI(
        title="WorkBench Local AI Service",
        version="v1",
        lifespan=lambda _: _lifespan(resolved_dependencies),
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=[],
    )
    application.add_exception_handler(RequestValidationError, _validation_error_handler)
    application.add_exception_handler(Exception, _unhandled_error_handler)
    application.include_router(_health_router(resolved_settings, resolved_dependencies))
    return application


app = create_app()


def run() -> None:
    """Start Uvicorn only on the validated local bind address."""

    settings = ApplicationSettings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()
