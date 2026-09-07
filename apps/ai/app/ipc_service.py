"""Private stdio transport for Electron's managed FastAPI child process."""

import asyncio
import base64
import json
import sys
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import ApplicationSettings
from app.main import create_app

_MAX_FRAME_BYTES = 1024 * 1024


def _response_frame(
    request_id: str, status: int, headers: list[tuple[bytes, bytes]], body: bytes
) -> str:
    return json.dumps(
        {
            "id": request_id,
            "status": status,
            "headers": [[key.decode("latin-1"), value.decode("latin-1")] for key, value in headers],
            "body": base64.b64encode(body).decode("ascii"),
        },
        separators=(",", ":"),
    )


async def _dispatch(application: Any, frame: dict[str, Any]) -> str:
    request_id = frame.get("id")
    method = frame.get("method")
    path = frame.get("path")
    headers = frame.get("headers")
    body = frame.get("body", "")
    file_path_value = frame.get("filePath")
    if not isinstance(request_id, str) or not isinstance(method, str):
        raise ValueError("invalid IPC request")
    if not isinstance(path, str) or not isinstance(body, str):
        raise ValueError("invalid IPC request")
    if (
        not isinstance(headers, dict)
        or not path.startswith("/")
        or not path.isascii()
        or "?" in path
        or "#" in path
    ):
        raise ValueError("invalid IPC request")
    if len(path) > 2048 or len(body) > _MAX_FRAME_BYTES:
        raise ValueError("invalid IPC request")

    encoded_headers: list[tuple[bytes, bytes]] = []
    for name, value in headers.items():
        if not isinstance(name, str) or not isinstance(value, str) or not name.isascii():
            raise ValueError("invalid IPC request")
        try:
            encoded_headers.append((name.lower().encode("ascii"), value.encode("latin-1")))
        except UnicodeEncodeError as error:
            raise ValueError("invalid IPC request") from error
    try:
        request_body = base64.b64decode(body, validate=True)
    except ValueError as error:
        raise ValueError("invalid IPC request") from error

    if file_path_value is not None:
        if not isinstance(file_path_value, str):
            raise ValueError("invalid IPC request")
        selected_path = Path(file_path_value)
        if not selected_path.is_absolute() or not selected_path.is_file():
            raise ValueError("invalid upload file")
        boundary = f"workbench-{uuid4().hex}"
        safe_name = "".join(
            "_" if character in {'"', "\\"} or ord(character) < 32 else character
            for character in selected_path.name
        )
        prefix = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        suffix = f"\r\n--{boundary}--\r\n".encode()
        request_body = prefix + await asyncio.to_thread(selected_path.read_bytes) + suffix
        encoded_headers.append(
            (b"content-type", f"multipart/form-data; boundary={boundary}".encode())
        )

    response_status = 500
    response_headers: list[tuple[bytes, bytes]] = []
    response_body = bytearray()
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": request_body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        nonlocal response_status, response_headers
        if message["type"] == "http.response.start":
            response_status = int(message["status"])
            response_headers = list(message.get("headers", []))
        elif message["type"] == "http.response.body":
            response_body.extend(message.get("body", b""))

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": encoded_headers,
        "client": ("127.0.0.1", 0),
        "server": ("workbench-ipc", 0),
    }
    await application(scope, receive, send)
    return _response_frame(request_id, response_status, response_headers, bytes(response_body))


async def _dispatch_stream(
    application: Any,
    frame: dict[str, Any],
    emit: Callable[[dict[str, Any]], Awaitable[None]],
    disconnected: asyncio.Event,
) -> None:
    """Dispatch one long-lived ASGI response without buffering it into a finite frame."""

    request_id = frame.get("id")
    method = frame.get("method")
    path = frame.get("path")
    headers = frame.get("headers")
    body = frame.get("body", "")
    if not isinstance(request_id, str) or not isinstance(method, str):
        raise ValueError("invalid IPC request")
    if not isinstance(path, str) or not isinstance(body, str) or not isinstance(headers, dict):
        raise ValueError("invalid IPC request")
    if not path.startswith("/") or not path.isascii() or "?" in path or "#" in path:
        raise ValueError("invalid IPC request")
    encoded_headers: list[tuple[bytes, bytes]] = []
    for name, value in headers.items():
        if not isinstance(name, str) or not isinstance(value, str) or not name.isascii():
            raise ValueError("invalid IPC request")
        encoded_headers.append((name.lower().encode("ascii"), value.encode("latin-1")))
    request_body = base64.b64decode(body, validate=True)
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": request_body, "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            await emit({"id": request_id, "kind": "streamStart", "status": int(message["status"])})
        elif message["type"] == "http.response.body":
            chunk = bytes(message.get("body", b""))
            if chunk:
                await emit(
                    {
                        "id": request_id,
                        "kind": "streamData",
                        "body": base64.b64encode(chunk).decode("ascii"),
                    }
                )

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": encoded_headers,
        "client": ("127.0.0.1", 0),
        "server": ("workbench-ipc", 0),
    }
    await application(scope, receive, send)


async def run_ipc_service() -> None:
    """Serve frames over inherited Electron child pipes, never a TCP port."""

    settings = ApplicationSettings()
    application = create_app(settings=settings)
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(application.router.lifespan_context(application))
        write_lock = asyncio.Lock()
        tasks: dict[str, asyncio.Task[None]] = {}
        disconnects: dict[str, asyncio.Event] = {}

        async def emit(frame: dict[str, Any] | str) -> None:
            serialized = (
                frame
                if isinstance(frame, str)
                else json.dumps(frame, separators=(",", ":"))
            )
            async with write_lock:
                sys.stdout.write(serialized + "\n")
                sys.stdout.flush()

        async def handle(frame: dict[str, Any]) -> None:
            request_id = str(frame.get("id", ""))
            try:
                if frame.get("stream") is True:
                    disconnected = asyncio.Event()
                    disconnects[request_id] = disconnected
                    await _dispatch_stream(application, frame, emit, disconnected)
                    await emit({"id": request_id, "kind": "streamEnd"})
                else:
                    await emit(await _dispatch(application, frame))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if frame.get("stream") is True:
                    await emit({"id": request_id, "kind": "streamStart", "status": 400})
                    encoded_error = base64.b64encode(str(error).encode()).decode("ascii")
                    await emit(
                        {"id": request_id, "kind": "streamData", "body": encoded_error}
                    )
                    await emit({"id": request_id, "kind": "streamEnd"})
                else:
                    await emit(_response_frame(request_id, 400, [], str(error).encode("utf-8")))
            finally:
                disconnects.pop(request_id, None)
                tasks.pop(request_id, None)

        try:
            while True:
                line = await asyncio.to_thread(sys.stdin.buffer.readline)
                if not line:
                    return
                if len(line) > _MAX_FRAME_BYTES or not line.endswith(b"\n"):
                    continue
                try:
                    frame = json.loads(line)
                    if not isinstance(frame, dict):
                        raise ValueError("invalid IPC request")
                    cancel_id = frame.get("cancel")
                    if isinstance(cancel_id, str):
                        disconnected = disconnects.get(cancel_id)
                        task = tasks.get(cancel_id)
                        if disconnected is not None:
                            disconnected.set()
                        if task is not None:
                            task.cancel()
                        continue
                    request_id = frame.get("id")
                    if not isinstance(request_id, str) or request_id in tasks:
                        raise ValueError("invalid IPC request")
                    tasks[request_id] = asyncio.create_task(handle(frame))
                except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
                    await emit(_response_frame("", 400, [], str(error).encode("utf-8")))
        finally:
            remaining_tasks = list(tasks.values())
            for task in remaining_tasks:
                task.cancel()
            await asyncio.gather(*remaining_tasks, return_exceptions=True)


def main() -> None:
    asyncio.run(run_ipc_service())


if __name__ == "__main__":
    main()
