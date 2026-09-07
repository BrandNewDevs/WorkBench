"""Authenticated workflow-session, input collection, and activity-stream routes."""

import asyncio
import codecs
import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, cast
from uuid import UUID, uuid4

import filetype  # type: ignore[import-untyped]
from fastapi import APIRouter, File, Header, Request, UploadFile, status
from fastapi import Path as FastApiPath
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.auth import AllowedOrigin, CurrentEmployee
from app.api.contracts import ErrorResponse
from app.api.session_contracts import (
    WorkflowSessionCreateRequest,
    WorkflowSessionCreateResponse,
    WorkflowUploadResponse,
)
from app.auth.service import AuthError, AuthService
from app.ports.local_backend import (
    ActivityEventStore,
    AuditAction,
    AuditRecord,
    AuditStore,
    SessionFileStore,
    WorkflowStore,
)
from app.storage import (
    UploadSessionStateConflictError,
    WorkflowSessionNotFoundError,
)
from app.workflow.contracts import (
    ActivityEvent,
    ActivityEventType,
    WorkflowSession,
    WorkflowStage,
    WorkflowStatus,
    WorkflowType,
)

_WORKFLOW_STORE_UNAVAILABLE = (
    "workflow_store_unavailable",
    "The local workflow storage is unavailable.",
)
_STORAGE_UNAVAILABLE = ("storage_unavailable", "The local input storage is unavailable.")
_SESSION_NOT_FOUND = ("session_not_found", "The workflow session was not found for this employee.")
_INVALID_STAGE = ("invalid_workflow_stage", "This workflow session no longer accepts inputs.")
_UNSUPPORTED_MEDIA = (
    "unsupported_media_type",
    "This file type is not supported for this workflow.",
)
_INVALID_FILE = ("invalid_file", "The selected file is not valid.")
_UPLOAD_TOO_LARGE = ("upload_too_large", "The selected file exceeds the local upload limit.")
_INVALID_EVENT_ID = ("invalid_event_id", "Last-Event-ID must be a nonnegative event sequence.")
_HEARTBEAT_SECONDS = 15
_STREAM_CHUNK_BYTES = 64 * 1024
_SNIFF_BYTES = 8192

_CREATE_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse, "description": "Invalid employee session"},
    403: {"model": ErrorResponse, "description": "Request origin is not allowed"},
    422: {"model": ErrorResponse, "description": "Request validation failed"},
    503: {"model": ErrorResponse, "description": "Local workflow storage unavailable"},
}
_UPLOAD_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    **_CREATE_ERROR_RESPONSES,
    404: {"model": ErrorResponse, "description": "Workflow session not found"},
    409: {"model": ErrorResponse, "description": "Workflow session is not collecting inputs"},
    413: {"model": ErrorResponse, "description": "Upload exceeds the local limit"},
    415: {"model": ErrorResponse, "description": "Unsupported upload media type"},
}
_EVENT_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    **_CREATE_ERROR_RESPONSES,
    404: {"model": ErrorResponse, "description": "Workflow session not found"},
}

SessionId = Annotated[UUID, FastApiPath(description="Owned workflow session identifier")]
_UPLOAD_FILE = File(...)


class UploadValidationError(ValueError):
    """A safe, client-actionable upload validation failure."""


class UploadTooLargeError(UploadValidationError):
    """The streamed request exceeded the configured local ceiling."""


class UnsupportedMediaError(UploadValidationError):
    """The observed bytes or extension are not allowed for this workflow."""


def _error(code: str, message: str, status_code: int) -> JSONResponse:
    body = ErrorResponse(code=code, message=message)
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json", by_alias=True),
    )


def _default_title(workflow_type: WorkflowType) -> str:
    """Use a concise, workflow-specific title until the employee supplies one."""

    return {
        WorkflowType.INSPECTION_ANALYSIS: "Inspection analysis",
        WorkflowType.CODE_REPAIR: "Code repair",
    }[workflow_type]


def _safe_display_name(name: str | None) -> str:
    """Reduce browser path-like names to one safe, non-sensitive display component."""

    candidate = (name or "").replace("\\", "/").rsplit("/", maxsplit=1)[-1].strip()
    windows_stem = candidate.split(".", maxsplit=1)[0].upper()
    reserved_names = {
        "AUX",
        "CON",
        "NUL",
        "PRN",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
    if (
        not candidate
        or candidate in {".", ".."}
        or any(character in '<>:"/\\|?*' for character in candidate)
        or any(ord(character) < 32 for character in candidate)
        or candidate.endswith((" ", "."))
        or windows_stem in reserved_names
    ):
        raise UploadValidationError("unsafe filename")
    return candidate


def _upload_type(workflow_type: WorkflowType, file_name: str, prefix: bytes) -> tuple[str, bool]:
    """Bind extension and observed bytes to one workflow-specific supported type."""

    extension = Path(file_name).suffix.lower()
    detected = filetype.guess_mime(prefix)
    inspection_types = {
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }
    text_types = {
        ".py": "text/x-python",
        ".csv": "text/csv",
        ".json": "application/json",
        ".txt": "text/plain",
    }
    supported = (
        inspection_types
        if workflow_type is WorkflowType.INSPECTION_ANALYSIS
        else text_types
    )
    expected = supported.get(extension)
    if expected is None:
        raise UnsupportedMediaError("unsupported extension")
    if workflow_type is WorkflowType.INSPECTION_ANALYSIS:
        if detected != expected:
            raise UnsupportedMediaError("detected media does not match extension")
        return expected, False
    if detected is not None:
        raise UnsupportedMediaError("binary data does not match text extension")
    try:
        prefix.decode("utf-8")
    except UnicodeDecodeError as error:
        raise UploadValidationError("text upload is not UTF-8") from error
    return expected, True


def _event_wire(event: ActivityEvent) -> str:
    """Serialize one persisted event using the standard SSE record shape."""

    payload = json.dumps(
        event.model_dump(mode="json", by_alias=True), separators=(",", ":"), ensure_ascii=False
    )
    return f"id: {event.event_id}\nevent: {event.event_type.value}\ndata: {payload}\n\n"


def build_session_router() -> APIRouter:
    """Build the complete Phase 3 workflow-session API surface."""

    router = APIRouter(tags=["workflow sessions"])

    def _workflow_store(request: Request) -> WorkflowStore | None:
        return cast(WorkflowStore | None, getattr(request.app.state, "workflow_store", None))

    def _file_store(request: Request) -> SessionFileStore | None:
        return cast(SessionFileStore | None, getattr(request.app.state, "session_file_store", None))

    def _event_store(request: Request) -> ActivityEventStore | None:
        return cast(
            ActivityEventStore | None, getattr(request.app.state, "activity_event_store", None)
        )

    async def _owned_session(
        session_id: SessionId, user_id: UUID, request: Request
    ) -> WorkflowSession | JSONResponse:
        store = _workflow_store(request)
        if store is None:
            return _error(*_WORKFLOW_STORE_UNAVAILABLE, status.HTTP_503_SERVICE_UNAVAILABLE)
        try:
            return await store.get_session(session_id, user_id)
        except WorkflowSessionNotFoundError:
            return _error(*_SESSION_NOT_FOUND, status.HTTP_404_NOT_FOUND)
        except Exception:
            return _error(*_WORKFLOW_STORE_UNAVAILABLE, status.HTTP_503_SERVICE_UNAVAILABLE)

    @router.post(
        "/sessions",
        response_model=WorkflowSessionCreateResponse,
        status_code=status.HTTP_201_CREATED,
        responses=_CREATE_ERROR_RESPONSES,
    )
    async def create_workflow_session(
        payload: WorkflowSessionCreateRequest,
        _: AllowedOrigin,
        user: CurrentEmployee,
        request: Request,
    ) -> WorkflowSessionCreateResponse | JSONResponse:
        """Persist an employee-owned workflow boundary in its initial input stage."""

        store = _workflow_store(request)
        events = _event_store(request)
        if store is None or events is None:
            return _error(*_WORKFLOW_STORE_UNAVAILABLE, status.HTTP_503_SERVICE_UNAVAILABLE)
        now = datetime.now(UTC)
        session = WorkflowSession(
            session_id=uuid4(),
            owner_user_id=user.user_id,
            workflow_type=payload.workflow_type,
            title=payload.title or _default_title(payload.workflow_type),
            stage=WorkflowStage.COLLECTING_INPUTS,
            status=WorkflowStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        try:
            created = await store.create_session(session)
            await events.append(
                ActivityEvent(
                    event_id=0,
                    session_id=created.session_id,
                    event_type=ActivityEventType.SESSION_CREATED,
                    occurred_at=now,
                    payload={},
                ),
                owner_user_id=user.user_id,
            )
        except Exception:
            return _error(*_WORKFLOW_STORE_UNAVAILABLE, status.HTTP_503_SERVICE_UNAVAILABLE)
        audit_store = cast(AuditStore | None, getattr(request.app.state, "audit_store", None))
        if audit_store is not None:
            with suppress(Exception):
                await audit_store.append(
                    AuditRecord(
                        audit_id=uuid4(),
                        action=AuditAction.SESSION_CREATED,
                        actor_user_id=user.user_id,
                        session_id=created.session_id,
                        outcome="created",
                        occurred_at=now,
                    )
                )
        return WorkflowSessionCreateResponse(
            session_id=created.session_id,
            workflow_type=created.workflow_type,
            title=created.title,
            stage=created.stage,
            status=created.status,
            created_at=created.created_at,
        )

    @router.post(
        "/sessions/{session_id}/uploads",
        response_model=WorkflowUploadResponse,
        status_code=status.HTTP_201_CREATED,
        responses=_UPLOAD_ERROR_RESPONSES,
    )
    async def upload_session_input(
        session_id: SessionId,
        _: AllowedOrigin,
        user: CurrentEmployee,
        request: Request,
        file: UploadFile = _UPLOAD_FILE,
    ) -> WorkflowUploadResponse | JSONResponse:
        """Accept exactly one explicitly selected file without exposing its local path."""

        session = await _owned_session(session_id, user.user_id, request)
        if isinstance(session, JSONResponse):
            return session
        if session.stage is not WorkflowStage.COLLECTING_INPUTS:
            return _error(*_INVALID_STAGE, status.HTTP_409_CONFLICT)
        files = _file_store(request)
        events = _event_store(request)
        if files is None or events is None:
            return _error(*_STORAGE_UNAVAILABLE, status.HTTP_503_SERVICE_UNAVAILABLE)
        try:
            file_name = _safe_display_name(file.filename)
            prefix = await file.read(_SNIFF_BYTES)
            if not prefix:
                return _error(*_INVALID_FILE, status.HTTP_422_UNPROCESSABLE_CONTENT)
            mime_type, is_text = _upload_type(session.workflow_type, file_name, prefix)
            if len(prefix) > request.app.state.upload_max_bytes:
                return _error(*_UPLOAD_TOO_LARGE, status.HTTP_413_CONTENT_TOO_LARGE)

            async def content() -> AsyncIterator[bytes]:
                total = 0
                decoder = codecs.getincrementaldecoder("utf-8")("strict") if is_text else None
                for chunk in (prefix,):
                    total += len(chunk)
                    if total > request.app.state.upload_max_bytes:
                        raise UploadTooLargeError("upload size exceeded")
                    if decoder is not None:
                        decoder.decode(chunk)
                    yield chunk
                while chunk := await file.read(_STREAM_CHUNK_BYTES):
                    total += len(chunk)
                    if total > request.app.state.upload_max_bytes:
                        raise UploadTooLargeError("upload size exceeded")
                    if decoder is not None:
                        decoder.decode(chunk)
                    yield chunk
                if decoder is not None:
                    decoder.decode(b"", final=True)

            stored = await files.save_upload(
                session=session,
                upload_id=uuid4(),
                source_id=uuid4(),
                file_name=file_name,
                mime_type=mime_type,
                content=content(),
            )
        except UploadTooLargeError:
            return _error(*_UPLOAD_TOO_LARGE, status.HTTP_413_CONTENT_TOO_LARGE)
        except UnsupportedMediaError:
            return _error(*_UNSUPPORTED_MEDIA, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
        except (UnicodeDecodeError, UploadValidationError, ValueError):
            return _error(*_INVALID_FILE, status.HTTP_422_UNPROCESSABLE_CONTENT)
        except UploadSessionStateConflictError:
            return _error(*_INVALID_STAGE, status.HTTP_409_CONFLICT)
        except Exception:
            return _error(*_STORAGE_UNAVAILABLE, status.HTTP_503_SERVICE_UNAVAILABLE)
        finally:
            with suppress(Exception):
                await file.close()
        try:
            await events.append(
                ActivityEvent(
                    event_id=0,
                    session_id=session.session_id,
                    event_type=ActivityEventType.UPLOAD_ACCEPTED,
                    occurred_at=stored.created_at,
                    payload={
                        "uploadId": str(stored.upload_id),
                        "sourceId": str(stored.source_id),
                        "fileName": stored.file_name,
                        "sizeBytes": stored.size_bytes,
                    },
                ),
                owner_user_id=user.user_id,
            )
        except Exception:
            return _error(*_STORAGE_UNAVAILABLE, status.HTTP_503_SERVICE_UNAVAILABLE)
        audit_store = cast(AuditStore | None, getattr(request.app.state, "audit_store", None))
        if audit_store is not None:
            with suppress(Exception):
                await audit_store.append(
                    AuditRecord(
                        audit_id=uuid4(),
                        action=AuditAction.UPLOAD_ACCEPTED,
                        actor_user_id=user.user_id,
                        session_id=session.session_id,
                        outcome="accepted",
                        occurred_at=stored.created_at,
                    )
                )
        return WorkflowUploadResponse.model_validate(stored.model_dump())

    @router.get(
        "/sessions/{session_id}/events",
        response_class=StreamingResponse,
        response_model=None,
        responses=_EVENT_ERROR_RESPONSES,
    )
    async def stream_session_events(
        session_id: SessionId,
        _: AllowedOrigin,
        user: CurrentEmployee,
        request: Request,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> StreamingResponse | JSONResponse:
        """Replay and then stream durable owner-scoped activity over credentialed SSE."""

        if any(name.lower() in {"token", "access_token", "jwt"} for name in request.query_params):
            return _error(
                "invalid_event_request",
                "Authentication tokens are not accepted in event URLs.",
                422,
            )
        if last_event_id is None:
            after_event_id = 0
        elif (
            not last_event_id.isascii()
            or not last_event_id.isdecimal()
            or len(last_event_id) > 19
        ):
            return _error(*_INVALID_EVENT_ID, status.HTTP_422_UNPROCESSABLE_CONTENT)
        else:
            after_event_id = int(last_event_id)
            if after_event_id > 9_223_372_036_854_775_807:
                return _error(*_INVALID_EVENT_ID, status.HTTP_422_UNPROCESSABLE_CONTENT)
        session = await _owned_session(session_id, user.user_id, request)
        if isinstance(session, JSONResponse):
            return session
        events = _event_store(request)
        if events is None:
            return _error(*_STORAGE_UNAVAILABLE, status.HTTP_503_SERVICE_UNAVAILABLE)
        try:
            replay = await events.replay(
                session_id=session.session_id,
                owner_user_id=user.user_id,
                after_event_id=after_event_id,
            )
        except Exception:
            return _error(*_STORAGE_UNAVAILABLE, status.HTTP_503_SERVICE_UNAVAILABLE)

        live_after_event_id = replay[-1].event_id if replay else after_event_id
        subscription = cast(
            AsyncGenerator[ActivityEvent],
            events.subscribe(
                session_id=session.session_id,
                owner_user_id=user.user_id,
                after_event_id=live_after_event_id,
            ),
        )

        async def event_stream() -> AsyncIterator[str]:
            latest_event_id = after_event_id
            next_event: asyncio.Future[ActivityEvent] | None = None

            async def still_authorized() -> bool:
                service = cast(AuthService, request.app.state.auth_service)
                try:
                    await service.authenticated_user(request.cookies.get(service.cookie_name))
                except AuthError:
                    return False
                return True

            try:
                for event in replay:
                    latest_event_id = event.event_id
                    yield _event_wire(event)
                next_event = asyncio.ensure_future(anext(subscription))
                while True:
                    done, _ = await asyncio.wait({next_event}, timeout=_HEARTBEAT_SECONDS)
                    if not done:
                        if await request.is_disconnected():
                            return
                        if not await still_authorized():
                            return
                        yield ": heartbeat\n\n"
                        continue
                    try:
                        event = next_event.result()
                    except StopAsyncIteration:
                        return
                    next_event = asyncio.ensure_future(anext(subscription))
                    if not await still_authorized():
                        return
                    if event.event_id > latest_event_id:
                        latest_event_id = event.event_id
                        yield _event_wire(event)
            finally:
                if next_event is not None and not next_event.done():
                    next_event.cancel()
                    with suppress(asyncio.CancelledError):
                        await next_event
                await subscription.aclose()

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
