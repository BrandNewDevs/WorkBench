"""Employee chat route coverage through the private IPC dispatch path."""

import base64
import json
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from pwdlib import PasswordHash

from app.auth.provisioning import provision_initial_employee
from app.config import ApplicationSettings
from app.ipc_service import _dispatch
from app.main import create_app
from app.storage import LocalSQLiteDatabase

ORIGIN = "http://127.0.0.1:5173"
CAPABILITY = "A" * 43
SECRET = "test-signing-secret-material-at-least-forty-eight-bytes-long"
PASSWORD = "correct horse battery staple"


def _frame(
    request_id: str,
    method: str,
    path: str,
    *,
    cookie: str | None = None,
    body: dict[str, object] | None = None,
    capability: str | None = CAPABILITY,
) -> dict[str, object]:
    encoded_body = "" if body is None else base64.b64encode(json.dumps(body).encode()).decode()
    headers: dict[str, str] = {"Origin": ORIGIN}
    if capability is not None:
        headers["X-Workbench-Capability"] = capability
    if body is not None:
        headers["Content-Type"] = "application/json"
    if cookie is not None:
        headers["Cookie"] = cookie
    return {
        "id": request_id,
        "method": method,
        "path": path,
        "headers": headers,
        "body": encoded_body,
    }


def _payload(frame: dict[str, object]) -> dict[str, object]:
    parsed = json.loads(base64.b64decode(str(frame["body"])))
    if not isinstance(parsed, dict):
        raise AssertionError("Expected a JSON object response body")
    return parsed


async def _insert_employee(database_path: Path, username: str) -> None:
    password_hash = PasswordHash.recommended().hash(PASSWORD)
    database = LocalSQLiteDatabase(database_path)
    async with database.open() as connection:
        await connection.execute("BEGIN IMMEDIATE")
        await connection.execute(
            """INSERT INTO identities
            (user_id, username, display_name, role, password_hash, disabled)
            VALUES (?, ?, ?, ?, ?, 0)""",
            (str(uuid4()), username, username, "employee", password_hash),
        )


async def _build_app_with_two_employees(tmp_path: Path) -> tuple[FastAPI, str, str]:
    database_path = tmp_path / "workbench.db"
    await provision_initial_employee(
        database_path=database_path,
        username="engineer.one",
        display_name="Engineer One",
        password=PASSWORD,
    )
    await _insert_employee(database_path, "engineer.two")
    app = create_app(
        settings=ApplicationSettings(
            auth_signing_secret=SECRET,
            database_path=database_path,
            local_service_capability=CAPABILITY,
        )
    )

    async def login(username: str, request_id: str) -> str:
        response = json.loads(
            await _dispatch(
                app,
                _frame(
                    request_id,
                    "POST",
                    "/auth/login",
                    body={"username": username, "password": PASSWORD},
                ),
            )
        )
        cookie_pair = next(
            value for name, value in response["headers"] if name == "set-cookie"
        )
        assert isinstance(cookie_pair, str)
        return cookie_pair.split(";", 1)[0]

    async with app.router.lifespan_context(app):
        first_cookie = await login("engineer.one", "login-one")
        second_cookie = await login("engineer.two", "login-two")
    return app, first_cookie, second_cookie


async def _create_session(
    app: FastAPI, cookie: str, title: str = "Inspection review"
) -> str:
    response = json.loads(
        await _dispatch(
            app,
            _frame(
                "create-session",
                "POST",
                "/chat/sessions",
                cookie=cookie,
                body={"workflowType": "inspectionAnalysis", "title": title},
            ),
        )
    )
    assert response["status"] == 200
    session = _payload(response)
    session_id = session["sessionId"]
    assert isinstance(session_id, str)
    return session_id


async def test_chat_requires_capability_and_an_authenticated_employee(tmp_path: Path) -> None:
    app, cookie, _ = await _build_app_with_two_employees(tmp_path)
    async with app.router.lifespan_context(app):
        without_capability = json.loads(
            await _dispatch(
                app, _frame("no-capability", "GET", "/chat/sessions", capability=None)
            )
        )
        without_cookie = json.loads(
            await _dispatch(app, _frame("no-cookie", "GET", "/chat/sessions"))
        )
        unauthorized_cookie = json.loads(
            await _dispatch(
                app,
                _frame(
                    "bad-cookie",
                    "GET",
                    "/chat/sessions",
                    cookie="workbench_session=ffffffffffffffffffffffffffffffff",
                ),
            )
        )
        allowed = json.loads(
            await _dispatch(app, _frame("ok", "GET", "/chat/sessions", cookie=cookie))
        )

    assert without_capability["status"] == 403
    assert without_cookie["status"] == 401
    assert unauthorized_cookie["status"] == 401
    assert allowed["status"] == 200
    assert _payload(allowed)["sessions"] == []


async def test_create_list_and_append_chat_messages(tmp_path: Path) -> None:
    app, cookie, _ = await _build_app_with_two_employees(tmp_path)
    async with app.router.lifespan_context(app):
        created_frame = json.loads(
            await _dispatch(
                app,
                _frame(
                    "create",
                    "POST",
                    "/chat/sessions",
                    cookie=cookie,
                    body={"workflowType": "inspectionAnalysis", "title": "Inspection review"},
                ),
            )
        )
        created = _payload(created_frame)
        session_id = created["sessionId"]
        owner_id = created["ownerUserId"]
        first_append = json.loads(
            await _dispatch(
                app,
                _frame(
                    "append-1",
                    "POST",
                    f"/chat/sessions/{session_id}/messages",
                    cookie=cookie,
                    body={"content": "  Find the corrosion findings.  "},
                ),
            )
        )
        second_append = json.loads(
            await _dispatch(
                app,
                _frame(
                    "append-2",
                    "POST",
                    f"/chat/sessions/{session_id}/messages",
                    cookie=cookie,
                    body={"content": "Second message"},
                ),
            )
        )
        listed = json.loads(
            await _dispatch(
                app,
                _frame("messages", "GET", f"/chat/sessions/{session_id}/messages", cookie=cookie),
            )
        )
        sessions = json.loads(
            await _dispatch(app, _frame("sessions", "GET", "/chat/sessions", cookie=cookie))
        )

    assert created_frame["status"] == 200
    assert created["title"] == "Inspection review"
    assert created["workflowType"] == "inspectionAnalysis"
    assert created["stage"] == "collectingInputs"
    assert created["status"] == "active"
    assert first_append["status"] == 200
    assert second_append["status"] == 200
    messages = _payload(listed)["messages"]
    assert listed["status"] == 200
    assert isinstance(messages, list) and len(messages) == 2
    assert messages[0]["content"] == "Find the corrosion findings."
    assert messages[0]["role"] == "user"
    assert messages[0]["authorUserId"] == owner_id
    assert messages[1]["content"] == "Second message"
    assert messages[0]["createdAt"] <= messages[1]["createdAt"]
    listed_sessions = _payload(sessions)["sessions"]
    assert isinstance(listed_sessions, list) and len(listed_sessions) == 1
    assert listed_sessions[0]["sessionId"] == session_id


async def test_session_detail_rejects_unknown_sessions(tmp_path: Path) -> None:
    app, cookie, _ = await _build_app_with_two_employees(tmp_path)
    async with app.router.lifespan_context(app):
        session_id = await _create_session(app, cookie)
        missing = json.loads(
            await _dispatch(
                app, _frame("missing", "GET", f"/chat/sessions/{uuid4()}", cookie=cookie)
            )
        )
        detail = json.loads(
            await _dispatch(
                app, _frame("detail", "GET", f"/chat/sessions/{session_id}", cookie=cookie)
            )
        )

    assert missing["status"] == 404
    assert _payload(missing)["code"] == "session_not_found"
    assert detail["status"] == 200
    assert _payload(detail)["sessionId"] == session_id


async def test_message_validation_rejects_blank_and_overlong_content(tmp_path: Path) -> None:
    app, cookie, _ = await _build_app_with_two_employees(tmp_path)
    async with app.router.lifespan_context(app):
        session_id = await _create_session(app, cookie)
        blank = json.loads(
            await _dispatch(
                app,
                _frame(
                    "blank",
                    "POST",
                    f"/chat/sessions/{session_id}/messages",
                    cookie=cookie,
                    body={"content": "   "},
                ),
            )
        )
        overlong = json.loads(
            await _dispatch(
                app,
                _frame(
                    "overlong",
                    "POST",
                    f"/chat/sessions/{session_id}/messages",
                    cookie=cookie,
                    body={"content": "x" * 20_001},
                ),
            )
        )
        blank_title = json.loads(
            await _dispatch(
                app,
                _frame(
                    "blank-title",
                    "POST",
                    "/chat/sessions",
                    cookie=cookie,
                    body={"workflowType": "inspectionAnalysis", "title": "   "},
                ),
            )
        )
        invalid_type = json.loads(
            await _dispatch(
                app,
                _frame(
                    "invalid-type",
                    "POST",
                    "/chat/sessions",
                    cookie=cookie,
                    body={"workflowType": "notAWorkflow", "title": "Valid title"},
                ),
            )
        )

    assert blank["status"] == 422
    assert _payload(blank)["code"] == "invalid_message"
    assert overlong["status"] == 422
    assert blank_title["status"] == 422
    assert _payload(blank_title)["code"] == "invalid_title"
    assert invalid_type["status"] == 422


async def test_chat_data_is_scoped_to_the_owning_employee(tmp_path: Path) -> None:
    app, first_cookie, second_cookie = await _build_app_with_two_employees(tmp_path)
    async with app.router.lifespan_context(app):
        session_id = await _create_session(app, first_cookie)
        foreign_messages = json.loads(
            await _dispatch(
                app,
                _frame(
                    "foreign-messages",
                    "GET",
                    f"/chat/sessions/{session_id}/messages",
                    cookie=second_cookie,
                ),
            )
        )
        foreign_append = json.loads(
            await _dispatch(
                app,
                _frame(
                    "foreign-append",
                    "POST",
                    f"/chat/sessions/{session_id}/messages",
                    cookie=second_cookie,
                    body={"content": "Not my session"},
                ),
            )
        )
        own_sessions = json.loads(
            await _dispatch(
                app, _frame("own", "GET", "/chat/sessions", cookie=second_cookie)
            )
        )

    assert foreign_messages["status"] == 404
    assert foreign_append["status"] == 404
    assert _payload(own_sessions)["sessions"] == []
