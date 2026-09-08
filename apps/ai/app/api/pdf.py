"""Authenticated PDF routes with opaque source and artifact identifiers."""

from base64 import b64encode
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.auth import AllowedOrigin, CurrentEmployee
from app.api.turn_stream import Emit, TurnEvent, turn_response
from app.pdf.contracts import PdfApprovalDecision, PdfSessionView, PdfTurnRequest, PdfTurnResult
from app.pdf.service import PdfWorkflowService


def pdf_service(request: Request) -> PdfWorkflowService:
    service = cast(PdfWorkflowService | None, request.app.state.pdf_service)
    if service is None:
        raise RuntimeError("PDF service unavailable")
    return service


def build_pdf_router() -> APIRouter:
    router = APIRouter(prefix="/pdf/sessions", tags=["pdf"])

    @router.post("/{session_id}/delete")
    async def delete(
        session_id: UUID, _: AllowedOrigin, user: CurrentEmployee, request: Request
    ) -> dict[str, bool]:
        await pdf_service(request).delete(session_id, user.user_id)
        return {"deleted": True}

    @router.get("/{session_id}")
    async def view(
        session_id: UUID, _: AllowedOrigin, user: CurrentEmployee, request: Request
    ) -> PdfSessionView:
        return await pdf_service(request).store.get(session_id, user.user_id)

    @router.post("/{session_id}/turns")
    async def turn(
        session_id: UUID,
        payload: PdfTurnRequest,
        _: AllowedOrigin,
        user: CurrentEmployee,
        request: Request,
    ) -> PdfTurnResult:
        return await pdf_service(request).turn(session_id, user.user_id, payload)

    @router.post("/{session_id}/approvals/{approval_id}")
    async def approve(
        session_id: UUID,
        approval_id: UUID,
        payload: PdfApprovalDecision,
        _: AllowedOrigin,
        user: CurrentEmployee,
        request: Request,
    ) -> PdfTurnResult:
        return await pdf_service(request).resolve(session_id, user.user_id, approval_id, payload)

    @router.post("/{session_id}/turns/stream", response_model=None)
    async def stream(
        session_id: UUID,
        payload: PdfTurnRequest,
        _: AllowedOrigin,
        user: CurrentEmployee,
        request: Request,
    ) -> StreamingResponse:
        service = pdf_service(request)
        await service.store.get(session_id, user.user_id)

        async def run(emit: Emit) -> None:
            await emit(TurnEvent(event="turn.accepted"))

            async def progress(text: str) -> None:
                await emit(TurnEvent(event="tool.progress", text=text))

            async def delta(text: str | None) -> None:
                await emit(
                    TurnEvent(
                        event="assistant.reset" if text is None else "assistant.delta",
                        text=text or "",
                    )
                )

            result = await service.turn(session_id, user.user_id, payload, progress, on_delta=delta)
            await emit(TurnEvent(event="tool.completed", text=result.tool))
            await emit(
                TurnEvent(
                    event="assistant.completed",
                    result=result.model_dump(mode="json", by_alias=True),
                )
            )

        return turn_response(run)

    @router.get("/{session_id}/artifacts/{artifact_id}")
    async def artifact(
        session_id: UUID,
        artifact_id: UUID,
        _: AllowedOrigin,
        user: CurrentEmployee,
        request: Request,
    ) -> JSONResponse:
        metadata, content = await pdf_service(request).artifact(
            session_id, user.user_id, artifact_id
        )
        return JSONResponse(
            {
                "artifact": metadata.model_dump(mode="json", by_alias=True),
                "contentBase64": b64encode(content).decode("ascii"),
            }
        )

    return router
