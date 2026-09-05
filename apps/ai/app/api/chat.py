"""Employee chat routes over the private managed pipe; no workflow control here."""

from contextlib import suppress
from datetime import UTC, datetime
from typing import Annotated, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Path, Request
from fastapi.responses import JSONResponse

from app.api.auth import AllowedOrigin, CurrentEmployee
from app.api.chat_contracts import (
    ChatMessageAppendRequest,
    ChatMessageListEnvelope,
    ChatSessionCreateRequest,
    ChatSessionListEnvelope,
)
from app.api.contracts import ErrorResponse
from app.auth.contracts import AuthenticatedUser
from app.ports.backend2 import (
    AuditAction,
    AuditRecord,
    AuditStore,
    ChatStore,
    WorkflowMessage,
)
from app.storage import SessionAlreadyExistsError, WorkflowSessionNotFoundError
from app.workflow.contracts import (
    WorkflowSession,
    WorkflowStage,
    WorkflowStatus,
)

_UNAVAILABLE = ("chat_store_unavailable", "The local chat storage is unavailable.")
_NOT_FOUND = (
    "session_not_found",
    "The chat session was not found for this employee.",
)

_ANY_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse, "description": "Invalid employee session"},
    403: {"model": ErrorResponse, "description": "Request origin is not allowed"},
    422: {"model": ErrorResponse, "description": "Request validation failed"},
    503: {"model": ErrorResponse, "description": "Local chat storage unavailable"},
}
_READ_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    **_ANY_ERROR_RESPONSES,
    404: {"model": ErrorResponse, "description": "Chat session not found for this employee"},
}
_APPEND_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    **_READ_ERROR_RESPONSES,
    409: {"model": ErrorResponse, "description": "The chat session no longer accepts messages"},
}

SessionId = Annotated[UUID, Path(description="Owned chat session identifier")]


def _error(code: str, message: str, status: int) -> JSONResponse:
    body = ErrorResponse(code=code, message=message)
    return JSONResponse(status_code=status, content=body.model_dump(mode="json", by_alias=True))


def build_chat_router() -> APIRouter:
    """Build the employee chat surface over owned workflow sessions."""

    router = APIRouter(prefix="/chat", tags=["chat"])

    def _chat_store(request: Request) -> ChatStore | None:
        return cast(ChatStore | None, getattr(request.app.state, "chat_store", None))

    def _audit_store(request: Request) -> AuditStore:
        return cast(AuditStore, request.app.state.audit_store)

    async def _owned_session(
        session_id: SessionId, user: AuthenticatedUser, request: Request
    ) -> WorkflowSession | JSONResponse:
        """Return the owned session, or the sanitized failure response."""

        store = _chat_store(request)
        if store is None:
            return _error(*_UNAVAILABLE, 503)
        try:
            return await store.get_session(session_id, user.user_id)
        except WorkflowSessionNotFoundError:
            return _error(*_NOT_FOUND, 404)

    @router.post(
        "/sessions",
        response_model=WorkflowSession,
        responses=_ANY_ERROR_RESPONSES,
    )
    async def create_session(
        payload: ChatSessionCreateRequest,
        _: AllowedOrigin,
        user: CurrentEmployee,
        request: Request,
    ) -> WorkflowSession | JSONResponse:
        store = _chat_store(request)
        if store is None:
            return _error(*_UNAVAILABLE, 503)
        title = payload.title.strip()
        if not title:
            return _error("invalid_title", "The chat title must not be blank.", 422)
        now = datetime.now(UTC)
        try:
            created = await store.create_session(
                WorkflowSession(
                    session_id=uuid4(),
                    owner_user_id=user.user_id,
                    workflow_type=payload.workflow_type,
                    title=title,
                    stage=WorkflowStage.COLLECTING_INPUTS,
                    status=WorkflowStatus.ACTIVE,
                    created_at=now,
                    updated_at=now,
                )
            )
        except SessionAlreadyExistsError:
            return _error(
                "session_conflict",
                "The chat session could not be created.",
                500,
            )
        # Best-effort like the auth service: chat creation stays available if
        # only the independent audit writer is down.
        with suppress(Exception):
            await _audit_store(request).append(
                AuditRecord(
                    audit_id=uuid4(),
                    action=AuditAction.SESSION_CREATED,
                    actor_user_id=user.user_id,
                    session_id=created.session_id,
                    outcome="created",
                    occurred_at=now,
                )
            )
        return created

    @router.get(
        "/sessions",
        response_model=ChatSessionListEnvelope,
        responses=_ANY_ERROR_RESPONSES,
    )
    async def list_sessions(
        _: AllowedOrigin,
        user: CurrentEmployee,
        request: Request,
    ) -> ChatSessionListEnvelope | JSONResponse:
        store = _chat_store(request)
        if store is None:
            return _error(*_UNAVAILABLE, 503)
        return ChatSessionListEnvelope(sessions=await store.list_sessions(user.user_id))

    @router.get(
        "/sessions/{session_id}",
        response_model=WorkflowSession,
        responses=_READ_ERROR_RESPONSES,
    )
    async def get_session(
        session_id: SessionId,
        _: AllowedOrigin,
        user: CurrentEmployee,
        request: Request,
    ) -> WorkflowSession | JSONResponse:
        return await _owned_session(session_id, user, request)

    @router.get(
        "/sessions/{session_id}/messages",
        response_model=ChatMessageListEnvelope,
        responses=_READ_ERROR_RESPONSES,
    )
    async def list_messages(
        session_id: SessionId,
        _: AllowedOrigin,
        user: CurrentEmployee,
        request: Request,
    ) -> ChatMessageListEnvelope | JSONResponse:
        session = await _owned_session(session_id, user, request)
        if isinstance(session, JSONResponse):
            return session
        store = _chat_store(request)
        if store is None:
            return _error(*_UNAVAILABLE, 503)
        return ChatMessageListEnvelope(
            messages=await store.list_messages(session.session_id, user.user_id)
        )

    @router.post(
        "/sessions/{session_id}/messages",
        response_model=WorkflowMessage,
        responses=_APPEND_ERROR_RESPONSES,
    )
    async def append_message(
        session_id: SessionId,
        payload: ChatMessageAppendRequest,
        _: AllowedOrigin,
        user: CurrentEmployee,
        request: Request,
    ) -> WorkflowMessage | JSONResponse:
        content = payload.content.strip()
        if not content:
            return _error("invalid_message", "The message must not be blank.", 422)
        session = await _owned_session(session_id, user, request)
        if isinstance(session, JSONResponse):
            return session
        if session.status is not WorkflowStatus.ACTIVE:
            return _error(
                "session_not_active",
                "This chat session is closed and no longer accepts messages.",
                409,
            )
        store = _chat_store(request)
        if store is None:
            return _error(*_UNAVAILABLE, 503)
        return await store.append_message(
            WorkflowMessage(
                message_id=uuid4(),
                session_id=session.session_id,
                author_user_id=user.user_id,
                role="user",
                content=content,
                created_at=datetime.now(UTC),
            )
        )

    return router
