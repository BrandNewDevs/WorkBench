"""FastAPI composition root for the local WorkBench service."""

import hmac
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import uvicorn
from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import RequestResponseEndpoint

from app.ai.local_engine import create_local_ai_engine
from app.api.auth import build_auth_router, clear_session_cookie
from app.api.chat import build_chat_router
from app.api.contracts import ErrorResponse
from app.api.health_contracts import HealthResponse, HealthStatus
from app.api.sessions import build_session_router
from app.artifacts import LibreOfficePdfConverter, LocalDocumentArtifactExecutor
from app.auth.service import AuthError, AuthService
from app.config import ApplicationSettings
from app.health import ApplicationDependencies, build_health_response
from app.local_health import LocalSystemHealthProvider
from app.ports.local_backend import (
    LocalDeploymentProof,
    WorkflowAdmissionStatus,
    WorkflowMessage,
    WorkflowRunAdmission,
)
from app.sandbox import DockerSandboxExecutor
from app.storage import (
    LocalKnowledgeSourceStore,
    LocalSessionWorkspaceStore,
    LocalSQLiteDatabase,
    SQLiteActivityEventStore,
    SQLiteApprovalStore,
    SQLiteArtifactStore,
    SQLiteAuditStore,
    SQLiteAuthSessionStore,
    SQLiteDraftStore,
    SQLiteIdentityStore,
    SQLiteSessionFileStore,
    SQLiteWorkflowStore,
)
from app.tools.registry import ToolRegistry
from app.workflow.contracts import (
    ActivityEvent,
    ActivityEventType,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowStage,
)
from app.workflow.runner import CheckpointAwareWorkflowRunner, InspectionWorkflowInputPolicy


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


def compose_runtime_dependencies(
    settings: ApplicationSettings,
    *,
    workflow_input_policy: InspectionWorkflowInputPolicy | None = None,
) -> ApplicationDependencies:
    """Compose the local SQLite auth stores for a normal service process."""

    database = LocalSQLiteDatabase(settings.database_path)
    workflow_store = SQLiteWorkflowStore(database)
    workspaces = LocalSessionWorkspaceStore(settings.sessions_root)
    files = SQLiteSessionFileStore(database, workspaces)
    approvals = SQLiteApprovalStore(database)
    artifacts = SQLiteArtifactStore(database)
    drafts = SQLiteDraftStore(database)
    knowledge = LocalKnowledgeSourceStore(database, settings.knowledge_root)
    artifact_executor = LocalDocumentArtifactExecutor(
        drafts,
        artifacts,
        workspaces,
        LibreOfficePdfConverter(
            settings.pdf_converter_executable,
            timeout_seconds=settings.pdf_timeout_seconds,
        ),
    )
    sandbox_executor = DockerSandboxExecutor(database, files, settings)
    ai_engine = create_local_ai_engine(knowledge_root=knowledge.approved_knowledge_root())
    tool_registry = ToolRegistry(approvals, artifact_executor, sandbox_executor)
    workflow_runner = (
        CheckpointAwareWorkflowRunner(
            workflows=workflow_store,
            drafts=drafts,
            approvals=approvals,
            ai_engine=ai_engine,
            tool_registry=tool_registry,
            input_policy=workflow_input_policy,
            lease_seconds=settings.workflow_lease_seconds,
        )
        if workflow_input_policy is not None
        else None
    )

    async def fail_recovery_run(run: WorkflowRun) -> None:
        """Terminally fail one unrecoverable run through the typed store boundary."""

        current = await workflow_store.get_run(
            workflow_run_id=run.workflow_run_id,
            session_id=run.session_id,
            owner_user_id=run.owner_user_id,
        )
        if current is None or current.status in {
            WorkflowRunStatus.COMPLETED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.APPROVAL_REJECTED,
        }:
            return
        await workflow_store.compare_and_set_stage(
            session_id=current.session_id,
            workflow_run_id=current.workflow_run_id,
            owner_user_id=current.owner_user_id,
            expected_stage=current.stage,
            expected_stage_version=current.stage_version,
            next_stage=WorkflowStage.FAILED,
            next_status=WorkflowRunStatus.FAILED,
            sandbox_attempts=current.sandbox_attempts,
        )

    async def _startup_with_recovery() -> None:
        await database.initialize()
        now = datetime.now(UTC)
        interrupted = await workflow_store.mark_stale_runs_interrupted(
            stale_before=now - timedelta(seconds=settings.workflow_lease_seconds),
            interrupted_at=now,
        )
        for queued_run in await workflow_store.list_unfinished_runs():
            if queued_run.status is not WorkflowRunStatus.QUEUED or workflow_runner is None:
                continue
            try:
                admission = await workflow_store.get_admission(
                    workflow_run_id=queued_run.workflow_run_id
                )
                if admission is None:
                    await fail_recovery_run(queued_run)
                    continue
                await workflow_runner.run(admission)
            except Exception:
                await fail_recovery_run(queued_run)
        for stale_run in interrupted:
            claimed = await workflow_store.claim_retry(
                workflow_run_id=stale_run.workflow_run_id,
                expected_stage_version=stale_run.stage_version,
                lease_expires_at=now + timedelta(seconds=settings.workflow_lease_seconds),
            )
            if claimed is None:
                continue
            if workflow_runner is None:
                await fail_recovery_run(claimed)
                continue
            try:
                selected_uploads = await workflow_store.get_run_inputs(
                    workflow_run_id=claimed.workflow_run_id,
                )
                synthetic_message = WorkflowMessage(
                    message_id=uuid4(),
                    session_id=claimed.session_id,
                    author_user_id=claimed.owner_user_id,
                    role="user",
                    content="[startup recovery]",
                    created_at=now,
                )
                admission = WorkflowRunAdmission(
                    status=WorkflowAdmissionStatus.CREATED,
                    run=claimed,
                    message=synthetic_message,
                    selected_uploads=selected_uploads,
                    accepted_event=ActivityEvent(
                        event_id=0,
                        session_id=claimed.session_id,
                        workflow_run_id=claimed.workflow_run_id,
                        event_type=ActivityEventType.MESSAGE_ACCEPTED,
                        occurred_at=now,
                    ),
                )
                await workflow_runner.run(admission)
            except Exception:
                await fail_recovery_run(claimed)

    return ApplicationDependencies(
        ai_engine=ai_engine,
        system_health_provider=LocalSystemHealthProvider(database, settings),
        identity_store=SQLiteIdentityStore(database),
        auth_session_store=SQLiteAuthSessionStore(database),
        audit_store=SQLiteAuditStore(database),
        chat_store=workflow_store,
        workflow_store=workflow_store,
        session_file_store=files,
        activity_event_store=SQLiteActivityEventStore(database),
        approval_store=approvals,
        artifact_store=artifacts,
        artifact_executor=artifact_executor,
        knowledge_source_store=knowledge,
        draft_store=drafts,
        sandbox_executor=sandbox_executor,
        workflow_runner=workflow_runner,
        tool_registry=tool_registry,
        deployment_proof=LocalDeploymentProof(
            model_endpoint_classification="loopback",
            pdf_converter_available=shutil.which(settings.pdf_converter_executable) is not None,
            docker_available=shutil.which(settings.docker_executable) is not None,
        ),
        startup=_startup_with_recovery,
        shutdown=ai_engine.close,
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
    application.state.workflow_store = resolved_dependencies.workflow_store
    application.state.session_file_store = resolved_dependencies.session_file_store
    application.state.activity_event_store = resolved_dependencies.activity_event_store
    application.state.workflow_runner = resolved_dependencies.workflow_runner
    application.state.upload_max_bytes = resolved_settings.upload_max_bytes
    application.add_exception_handler(RequestValidationError, _validation_error_handler)
    application.add_exception_handler(AuthError, _auth_error_handler)
    application.add_exception_handler(Exception, _unhandled_error_handler)
    application.include_router(_health_router(resolved_settings, resolved_dependencies))
    application.include_router(build_auth_router(resolved_settings))
    application.include_router(build_session_router())
    application.include_router(build_chat_router())
    return application


app = create_app()


def run() -> None:
    """Start Uvicorn only on the validated local bind address."""

    settings = ApplicationSettings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()
