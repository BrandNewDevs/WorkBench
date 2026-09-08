"""Employee chat routes over the private managed pipe; no workflow control here."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Annotated, cast
from uuid import UUID, uuid4, uuid5

from fastapi import APIRouter, Path, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.ai.engine import AIEngine
from app.ai.errors import (
    ConversationContextTooLarge,
    InvalidStructuredOutput,
    ModelCapacityError,
    ModelNotInstalled,
    ModelRequestFailed,
    ModelRequestTimeout,
    ModelRuntimeUnavailable,
)
from app.ai.schemas import (
    MAX_CONVERSATION_HISTORY_MESSAGES,
    ConversationMessage,
    ConversationRequest,
)
from app.api.auth import AllowedOrigin, CurrentEmployee
from app.api.chat_contracts import (
    ChatMessageAppendRequest,
    ChatMessageListEnvelope,
    ChatSessionCreateRequest,
    ChatSessionListEnvelope,
    ConversationCreateRequest,
    ConversationCreateResponse,
)
from app.api.contracts import ErrorResponse
from app.api.turn_stream import Emit, TurnEvent, turn_response
from app.auth.contracts import AuthenticatedUser
from app.ports.local_backend import (
    AuditAction,
    AuditRecord,
    AuditStore,
    ChatStore,
    SelectedUploadSnapshot,
    SessionFileStore,
    WorkflowAdmissionStatus,
    WorkflowMessage,
    WorkflowRunAdmissionRequest,
    WorkflowStore,
)
from app.storage import (
    ConversationTurnConflictError,
    SessionAlreadyExistsError,
    WorkflowSessionNotFoundError,
)
from app.storage.sqlite import WorkflowAdmissionConflictError
from app.workflow.contracts import (
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowSession,
    WorkflowStage,
    WorkflowStatus,
    WorkflowType,
)
from app.workflow.runner import WorkflowRunner

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
_CONVERSATION_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    **_READ_ERROR_RESPONSES,
    409: {"model": ErrorResponse, "description": "The chat session no longer accepts messages"},
    413: {"model": ErrorResponse, "description": "Conversation context is too large"},
    502: {"model": ErrorResponse, "description": "Invalid local AI response"},
    504: {"model": ErrorResponse, "description": "Local generation timed out"},
}

SessionId = Annotated[UUID, Path(description="Owned chat session identifier")]


class _ConversationTurnLocks:
    """Serialize complete turns per session without retaining inactive locks."""

    def __init__(self) -> None:
        self._registry_lock = asyncio.Lock()
        self._locks: dict[UUID, tuple[asyncio.Lock, int]] = {}

    @asynccontextmanager
    async def serialize(self, session_id: UUID) -> AsyncIterator[None]:
        async with self._registry_lock:
            lock, users = self._locks.get(session_id, (asyncio.Lock(), 0))
            self._locks[session_id] = (lock, users + 1)

        acquired = False
        try:
            await lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                lock.release()
            async with self._registry_lock:
                current_lock, current_users = self._locks[session_id]
                if current_users == 1:
                    del self._locks[session_id]
                else:
                    self._locks[session_id] = (current_lock, current_users - 1)


class _ConversationHistoryConflict(RuntimeError):
    """Stored messages do not form complete user/assistant turns."""


def _completed_history(messages: list[WorkflowMessage]) -> tuple[ConversationMessage, ...]:
    """Return complete ordered pairs, rejecting an in-progress cross-route turn."""

    if len(messages) % 2 != 0 or any(
        message.role != ("user" if index % 2 == 0 else "assistant")
        for index, message in enumerate(messages)
    ):
        raise _ConversationHistoryConflict
    return tuple(
        ConversationMessage(role=message.role, content=message.content) for message in messages
    )


def _error(code: str, message: str, status: int) -> JSONResponse:
    body = ErrorResponse(code=code, message=message)
    return JSONResponse(status_code=status, content=body.model_dump(mode="json", by_alias=True))


def build_chat_router() -> APIRouter:
    """Build the employee chat surface over owned workflow sessions."""

    router = APIRouter(prefix="/chat", tags=["chat"])
    turn_locks = _ConversationTurnLocks()

    def _chat_store(request: Request) -> ChatStore | None:
        return cast(ChatStore | None, getattr(request.app.state, "chat_store", None))

    def _audit_store(request: Request) -> AuditStore:
        return cast(AuditStore, request.app.state.audit_store)

    def _session_files(request: Request) -> SessionFileStore | None:
        return cast(
            SessionFileStore | None,
            getattr(request.app.state, "session_file_store", None),
        )

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
                    client_session_id=payload.client_session_id,
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
        "/sessions/{session_id}/conversation",
        response_model=ConversationCreateResponse,
        responses=_CONVERSATION_ERROR_RESPONSES,
    )
    async def create_conversation_turn(
        session_id: SessionId,
        payload: ConversationCreateRequest,
        _: AllowedOrigin,
        user: CurrentEmployee,
        request: Request,
    ) -> ConversationCreateResponse | JSONResponse:
        """Generate and persist one local text reply without workflow admission."""

        content = payload.message.strip()
        if not content:
            return _error("invalid_message", "The message must not be blank.", 422)
        store = _chat_store(request)
        engine = cast(AIEngine | None, getattr(request.app.state, "ai_engine", None))
        if store is None:
            return _error(*_UNAVAILABLE, 503)
        if engine is None:
            return _error(
                "text_model_unavailable",
                "The local text model is unavailable.",
                503,
            )

        session = await _owned_session(session_id, user, request)
        if isinstance(session, JSONResponse):
            return session

        async with turn_locks.serialize(session.session_id):
            current_session = await _owned_session(session_id, user, request)
            if isinstance(current_session, JSONResponse):
                return current_session
            if current_session.status is not WorkflowStatus.ACTIVE:
                return _error(
                    "session_not_active",
                    "This chat session is closed and no longer accepts messages.",
                    409,
                )

            if current_session.workflow_type is WorkflowType.PDF_DOCUMENT:
                return _error("workflow_not_allowed", "Use the session's workflow route.", 409)

            stored_messages = await store.list_messages(
                current_session.session_id,
                user.user_id,
                limit=MAX_CONVERSATION_HISTORY_MESSAGES,
            )
            try:
                history = _completed_history(stored_messages)
            except _ConversationHistoryConflict:
                return _error(
                    "conversation_conflict",
                    "Another operation is updating this chat session.",
                    409,
                )
            expected_latest_message_id = stored_messages[-1].message_id if stored_messages else None
            user_message = WorkflowMessage(
                message_id=uuid4(),
                session_id=current_session.session_id,
                author_user_id=user.user_id,
                role="user",
                content=content,
                created_at=datetime.now(UTC),
                client_message_id=payload.client_request_id or uuid4(),
            )
            try:
                ai_request = ConversationRequest(
                    session_id=str(current_session.session_id),
                    user_message=user_message.content,
                    history=history,
                )
                on_delta = getattr(request.state, "answer_delta", None)
                if on_delta is None:
                    reply = await engine.reply_to_conversation(ai_request)
                else:
                    reply = await engine.reply_to_conversation(ai_request, on_delta=on_delta)
                if reply.session_id != str(current_session.session_id):
                    raise ValueError("conversation reply session did not match the request")
                assistant_message_id = uuid5(user_message.message_id, "assistant-reply")
                assistant_message = WorkflowMessage(
                    message_id=assistant_message_id,
                    session_id=current_session.session_id,
                    role="assistant",
                    content=reply.assistant_text,
                    created_at=datetime.now(UTC),
                    client_message_id=assistant_message_id,
                )
            except ModelNotInstalled, ModelCapacityError:
                return _error(
                    "text_model_unavailable",
                    "The local text model is unavailable.",
                    503,
                )
            except ModelRuntimeUnavailable:
                return _error(
                    "ollama_unavailable",
                    "The local Ollama service is unavailable.",
                    503,
                )
            except ModelRequestTimeout:
                return _error(
                    "generation_timeout",
                    "The local text generation request timed out.",
                    504,
                )
            except ConversationContextTooLarge:
                return _error(
                    "conversation_too_large",
                    "The conversation is too large for the configured local text model.",
                    413,
                )
            except InvalidStructuredOutput, ModelRequestFailed, ValueError:
                return _error(
                    "invalid_ai_response",
                    "The local text model returned an invalid response.",
                    502,
                )

            try:
                stored_turn = await store.append_conversation_turn(
                    user_message=user_message,
                    assistant_message=assistant_message,
                    expected_latest_message_id=expected_latest_message_id,
                )
                stored_assistant = stored_turn[1]
            except ConversationTurnConflictError:
                return _error(
                    "conversation_conflict",
                    "Another operation updated this chat session. Please retry.",
                    409,
                )
            return ConversationCreateResponse(
                session_id=current_session.session_id,
                user_message_id=user_message.message_id,
                assistant_message_id=stored_assistant.message_id,
                assistant_text=stored_assistant.content,
                selected_model=reply.model,
                used_fallback=reply.used_fallback,
                fallback_reason=reply.fallback_reason,
                metrics=reply.metrics,
            )

    @router.post("/sessions/{session_id}/conversation/stream", response_model=None)
    async def stream_conversation(
        session_id: SessionId,
        payload: ConversationCreateRequest,
        _: AllowedOrigin,
        user: CurrentEmployee,
        request: Request,
    ) -> StreamingResponse | JSONResponse:
        session = await _owned_session(session_id, user, request)
        if isinstance(session, JSONResponse):
            return session
        if session.workflow_type is not WorkflowType.LOCAL_CONVERSATION:
            return _error("workflow_not_allowed", "Use the PDF workflow route.", 409)

        async def run(emit: Emit) -> None:
            async def delta(text: str | None) -> None:
                await emit(
                    TurnEvent(
                        event="assistant.reset" if text is None else "assistant.delta",
                        text=text or "",
                    )
                )

            request.state.answer_delta = delta
            await emit(TurnEvent(event="turn.accepted"))
            result = await create_conversation_turn(session_id, payload, _, user, request)
            if isinstance(result, JSONResponse):
                await emit(
                    TurnEvent(
                        event="turn.failed",
                        text="Local chat failed.",
                        result=bytes(result.body).decode(),
                    )
                )
            else:
                await emit(
                    TurnEvent(
                        event="assistant.completed",
                        result=result.model_dump(mode="json", by_alias=True),
                    )
                )

        return turn_response(run)

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
        if session.workflow_type in {WorkflowType.LOCAL_CONVERSATION, WorkflowType.PDF_DOCUMENT}:
            # Plain chat sessions have no workflow pipeline; admitting a run
            # would mix generated workflow turns into a conversation history.
            return _error(
                "workflow_not_allowed",
                "Plain chat sessions do not run workflows.",
                409,
            )
        store = _chat_store(request)
        if store is None:
            return _error(*_UNAVAILABLE, 503)
        message = WorkflowMessage(
            message_id=uuid4(),
            session_id=session.session_id,
            author_user_id=user.user_id,
            role="user",
            content=content,
            created_at=datetime.now(UTC),
            client_message_id=payload.client_message_id,
        )
        workflow_store = cast(
            WorkflowStore | None, getattr(request.app.state, "workflow_store", None)
        )
        files = _session_files(request)
        runner = cast(WorkflowRunner | None, getattr(request.app.state, "workflow_runner", None))
        if workflow_store is None or files is None or runner is None:
            return _error(*_UNAVAILABLE, 503)
        snapshots: list[SelectedUploadSnapshot] = []
        for upload_id in payload.selected_upload_ids:
            approved = await files.resolve_approved_path(
                upload_id=upload_id,
                session_id=session.session_id,
                owner_user_id=user.user_id,
            )
            stored = await files.get_upload(
                upload_id=upload_id,
                session_id=session.session_id,
                owner_user_id=user.user_id,
            )
            if approved is None or stored is None:
                return _error("upload_not_found", "A selected upload is unavailable.", 404)
            snapshots.append(
                SelectedUploadSnapshot(
                    upload_id=stored.upload_id,
                    session_id=stored.session_id,
                    owner_user_id=user.user_id,
                    source_id=stored.source_id,
                    file_name=stored.file_name,
                    mime_type=stored.mime_type,
                    size_bytes=stored.size_bytes,
                    sha256=stored.sha256,
                )
            )
        now = datetime.now(UTC)
        try:
            admission = await workflow_store.admit_run(
                WorkflowRunAdmissionRequest(
                    run=WorkflowRun(
                        workflow_run_id=uuid4(),
                        session_id=session.session_id,
                        owner_user_id=user.user_id,
                        workflow_type=session.workflow_type,
                        stage=WorkflowStage.COLLECTING_INPUTS,
                        stage_version=0,
                        status=WorkflowRunStatus.QUEUED,
                        created_at=now,
                        updated_at=now,
                    ),
                    message=message,
                    selected_uploads=tuple(snapshots),
                )
            )
        except WorkflowAdmissionConflictError:
            return _error("workflow_conflict", "This session already has active work.", 409)
        if admission.status is WorkflowAdmissionStatus.CREATED:
            await runner.run(admission)
        return admission.message

    return router
