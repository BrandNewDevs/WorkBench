"""Regression coverage for the private Electron-to-FastAPI transport."""

import asyncio
import base64
import json
import os
import socket
import sys
from pathlib import Path

import pytest

from app.auth.provisioning import provision_initial_employee
from app.config import ApplicationSettings
from app.ipc_service import _dispatch, _dispatch_stream
from app.main import create_app


async def test_ipc_dispatch_preserves_capability_origin_and_cookie_auth(tmp_path: Path) -> None:
    capability = "A" * 43
    password = "correct horse battery staple"
    database_path = tmp_path / "workbench.db"
    await provision_initial_employee(
        database_path=database_path,
        username="engineer.one",
        display_name="Engineer One",
        password=password,
    )
    app = create_app(
        settings=ApplicationSettings(
            auth_signing_secret="test-signing-secret-material-at-least-forty-eight-bytes-long",
            database_path=database_path,
            local_service_capability=capability,
        )
    )
    async with app.router.lifespan_context(app):
        blocked = json.loads(
            await _dispatch(
                app, {"id": "1", "method": "GET", "path": "/health", "headers": {}, "body": ""}
            )
        )
        login_body = base64.b64encode(
            json.dumps({"username": "engineer.one", "password": password}).encode()
        ).decode()
        login = json.loads(
            await _dispatch(
                app,
                {
                    "id": "2",
                    "method": "POST",
                    "path": "/auth/login",
                    "headers": {
                        "Content-Type": "application/json",
                        "X-Workbench-Capability": capability,
                        "Origin": "http://127.0.0.1:5173",
                    },
                    "body": login_body,
                },
            )
        )
        cookie = next(value for name, value in login["headers"] if name == "set-cookie")
        restored = json.loads(
            await _dispatch(
                app,
                {
                    "id": "3",
                    "method": "GET",
                    "path": "/auth/session",
                    "headers": {
                        "Cookie": cookie.split(";", 1)[0],
                        "X-Workbench-Capability": capability,
                        "Origin": "http://127.0.0.1:5173",
                    },
                    "body": "",
                },
            )
        )

    assert blocked["status"] == 403
    assert login["status"] == restored["status"] == 200
    assert (
        json.loads(base64.b64decode(restored["body"]))["session"]["user"]["username"]
        == "engineer.one"
    )


async def test_service_exit_cannot_be_rebound_into_the_inherited_pipe(tmp_path: Path) -> None:
    """A new TCP listener after child exit has no access to the dead child pipe."""

    environment = {
        **os.environ,
        "WORKBENCH_APP_AUTH_SIGNING_SECRET": (
            "test-signing-secret-material-at-least-forty-eight-bytes-long"
        ),
        "WORKBENCH_APP_LOCAL_SERVICE_CAPABILITY": "A" * 43,
        "WORKBENCH_APP_DATABASE_PATH": str(tmp_path / "workbench.db"),
    }
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.ipc_service",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
    )
    assert child.stdin is not None
    assert child.stdout is not None
    child.stdin.write(
        b'{"id":"ready","method":"GET","path":"/internal/ready","headers":{"X-Workbench-Readiness-Nonce":"n"},"body":""}\n'
    )
    await child.stdin.drain()
    assert b'"id":"ready"' in await asyncio.wait_for(child.stdout.readline(), timeout=5)
    child.kill()
    await child.wait()

    rebound = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    rebound.bind(("127.0.0.1", 0))
    rebound.listen()
    try:
        with pytest.raises((BrokenPipeError, ConnectionResetError)):
            child.stdin.write(b'{"id":"after-exit"}\n')
            await child.stdin.drain()
    finally:
        rebound.close()


async def test_stream_dispatch_emits_incremental_frames_and_cancels() -> None:
    emitted: list[dict[str, object]] = []
    disconnected = asyncio.Event()

    async def application(scope: dict[str, object], receive: object, send: object) -> None:
        del scope
        receive_call = receive  # Keep the small ASGI fixture explicit and typed locally.
        send_call = send
        assert callable(receive_call) and callable(send_call)
        await receive_call()
        await send_call({"type": "http.response.start", "status": 200, "headers": []})
        await send_call(
            {
                "type": "http.response.body",
                "body": b'id: 1\nevent: session.created\ndata: {"eventId":1}\n\n',
                "more_body": True,
            }
        )
        await receive_call()

    async def emit(frame: dict[str, object]) -> None:
        emitted.append(frame)

    task = asyncio.create_task(
        _dispatch_stream(
            application,
            {
                "id": "stream-1",
                "method": "GET",
                "path": "/sessions/00000000-0000-4000-8000-000000000000/events",
                "headers": {},
                "body": "",
            },
            emit,
            disconnected,
        )
    )
    for _ in range(20):
        if len(emitted) == 2:
            break
        await asyncio.sleep(0)

    assert emitted[0] == {
        "id": "stream-1",
        "kind": "streamStart",
        "status": 200,
        "headers": [],
    }
    assert emitted[1]["kind"] == "streamData"
    assert base64.b64decode(str(emitted[1]["body"])).startswith(b"id: 1\n")
    assert not task.done()

    disconnected.set()
    await asyncio.wait_for(task, timeout=1)


async def test_stream_dispatch_keeps_large_binary_responses_in_bounded_frames() -> None:
    emitted: list[dict[str, object]] = []
    content = b"P" * (2 * 1024 * 1024)

    async def application(scope: dict[str, object], receive: object, send: object) -> None:
        del scope, receive
        send_call = send
        assert callable(send_call)
        await send_call(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/pdf")],
            }
        )
        for start in range(0, len(content), 64 * 1024):
            await send_call(
                {
                    "type": "http.response.body",
                    "body": content[start : start + 64 * 1024],
                    "more_body": start + 64 * 1024 < len(content),
                }
            )

    async def emit(frame: dict[str, object]) -> None:
        emitted.append(frame)

    await _dispatch_stream(
        application,
        {
            "id": "artifact",
            "method": "GET",
            "path": "/pdf/sessions/session/artifacts/artifact",
            "headers": {},
            "body": "",
        },
        emit,
        asyncio.Event(),
    )

    assert emitted[0]["headers"] == [["content-type", "application/pdf"]]
    frames = [base64.b64decode(str(frame["body"])) for frame in emitted[1:]]
    assert b"".join(frames) == content
    assert max(len(json.dumps(frame).encode()) for frame in emitted) < 1024 * 1024
