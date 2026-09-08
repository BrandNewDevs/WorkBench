"""Ephemeral turn events. Tokens never enter the durable activity store."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from typing import Literal

from fastapi.responses import StreamingResponse
from pydantic import JsonValue

from app.ai.schemas import ContractModel


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
