"""Ephemeral turn events. Tokens never enter the durable activity store."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from typing import Literal

from fastapi.responses import StreamingResponse
from pydantic import JsonValue

from app.ai.errors import (
    ConversationContextTooLarge,
    InvalidStructuredOutput,
    ModelNotInstalled,
    ModelRequestTimeout,
    ModelRuntimeUnavailable,
)
from app.ai.schemas import ContractModel
from app.pdf.engine import PdfDocumentError


class TurnEvent(ContractModel):
    event: Literal[
        "turn.accepted",
        "tool.started",
        "tool.progress",
        "tool.completed",
        "assistant.delta",
        "assistant.reset",
        "assistant.completed",
        "turn.failed",
    ]
    text: str = ""
    result: JsonValue = None


Emit = Callable[[TurnEvent], Awaitable[None]]


def turn_response(run: Callable[[Emit], Awaitable[None]]) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        queue: asyncio.Queue[TurnEvent | None] = asyncio.Queue(maxsize=32)

        async def emit(event: TurnEvent) -> None:
            await queue.put(event)

        async def produce() -> None:
            try:
                await run(emit)
            except asyncio.CancelledError:
                raise
            except ModelNotInstalled:
                await emit(
                    TurnEvent(
                        event="turn.failed",
                        text=(
                            "A required local model is missing. "
                            "Preload the configured text, embedding or vision model."
                        ),
                        result={"code": "model_missing"},
                    )
                )
            except ModelRuntimeUnavailable:
                await emit(
                    TurnEvent(
                        event="turn.failed",
                        text="Local Ollama is unavailable. Start Ollama and retry.",
                        result={"code": "ollama_unavailable"},
                    )
                )
            except ModelRequestTimeout:
                await emit(
                    TurnEvent(
                        event="turn.failed",
                        text="The local model timed out. Retry or use a smaller approved model.",
                        result={"code": "generation_timeout"},
                    )
                )
            except ConversationContextTooLarge:
                await emit(
                    TurnEvent(
                        event="turn.failed",
                        text=(
                            "This task exceeds the model context limit. "
                            "Start a new conversation or use a shorter PDF."
                        ),
                        result={"code": "conversation_too_large"},
                    )
                )
            except InvalidStructuredOutput:
                await emit(
                    TurnEvent(
                        event="turn.failed",
                        text=(
                            "The model returned invalid or ungrounded output. No answer was saved."
                        ),
                        result={"code": "invalid_ai_response"},
                    )
                )
            except PdfDocumentError as error:
                await emit(
                    TurnEvent(
                        event="turn.failed", text=str(error), result={"code": "pdf_request_failed"}
                    )
                )
            except Exception:
                await emit(
                    TurnEvent(
                        event="turn.failed",
                        text="Local generation failed. Check model availability and try again.",
                    )
                )
            finally:
                # Cancellation must never block on a disconnected consumer's full queue.
                current = asyncio.current_task()
                if current is not None and not current.cancelling():
                    await queue.put(None)

        task = asyncio.create_task(produce())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield "data: " + event.model_dump_json(by_alias=True) + "\n\n"
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
