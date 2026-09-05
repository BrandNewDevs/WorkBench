"""Private stdio transport for Electron's managed FastAPI child process."""

import asyncio
import base64
import json
import sys
from contextlib import AsyncExitStack, suppress
from typing import Any

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


async def run_ipc_service() -> None:
    """Serve frames over inherited Electron child pipes, never a TCP port."""

    settings = ApplicationSettings()
    application = create_app(settings=settings)
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(application.router.lifespan_context(application))
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
                response = await _dispatch(application, frame)
            except (
                UnicodeDecodeError,
                UnicodeEncodeError,
                ValueError,
                json.JSONDecodeError,
            ) as error:
                request_id = ""
                with suppress(
                    UnicodeDecodeError, UnicodeEncodeError, ValueError, json.JSONDecodeError
                ):
                    request_id = str(json.loads(line).get("id", ""))
                response = _response_frame(request_id, 400, [], str(error).encode("utf-8"))
            sys.stdout.write(response + "\n")
            sys.stdout.flush()


def main() -> None:
    asyncio.run(run_ipc_service())


if __name__ == "__main__":
    main()
