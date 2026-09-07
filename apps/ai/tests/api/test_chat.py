"""Employee chat route coverage through the private IPC dispatch path."""

import asyncio
import base64
import json
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from pwdlib import PasswordHash

from app.auth.provisioning import provision_initial_employee
from app.config import ApplicationSettings
from app.health import ApplicationDependencies
from app.ipc_service import _dispatch
from app.main import create_app
from app.ports.local_backend import WorkflowRunAdmission
from app.storage import (
    LocalSQLiteDatabase,
    SQLiteAuthSessionStore,
    SQLiteIdentityStore,
    SQLiteWorkflowStore,
)

ORIGIN = "http://127.0.0.1:5173"
CAPABILITY = "A" * 43
SECRET = "test-signing-secret-material-at-least-forty-eight-bytes-long"
PASSWORD = "correct horse battery staple"


class RecordingWorkflowRunner:
    def __init__(self) -> None:
        self.admissions: list[WorkflowRunAdmission] = []

    async def run(self, admission: WorkflowRunAdmission) -> None:
        self.admissions.append(admission)


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
        cookie_pair = next(value for name, value in response["headers"] if name == "set-cookie")
        assert isinstance(cookie_pair, str)
        return cookie_pair.split(";", 1)[0]

    async with app.router.lifespan_context(app):
        first_cookie = await login("engineer.one", "login-one")
        second_cookie = await login("engineer.two", "login-two")
    return app, first_cookie, second_cookie


async def _create_session(app: FastAPI, cookie: str, title: str = "Inspection review") -> str:
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


async def test_idempotent_session_creation_replays_the_stored_session(tmp_path: Path) -> None:
    app, cookie, second_cookie = await _build_app_with_two_employees(tmp_path)
    async with app.router.lifespan_context(app):
        session_key = str(uuid4())
        created = json.loads(
            await _dispatch(
                app,
                _frame(
                    "create-keyed",
                    "POST",
                    "/chat/sessions",
                    cookie=cookie,
                    body={
                        "workflowType": "inspectionAnalysis",
                        "title": "Inspection review",
                        "clientSessionId": session_key,
                    },
                ),
            )
        )
        # A lost create response replays the committed session, even when the
        # retry drifted to a different title.
        replayed = json.loads(
            await _dispatch(
                app,
                _frame(
                    "create-retry",
                    "POST",
                    "/chat/sessions",
                    cookie=cookie,
                    body={
                        "workflowType": "inspectionAnalysis",
                        "title": "Edited title",
                        "clientSessionId": session_key,
                    },
                ),
            )
        )
        raced = await asyncio.gather(
            *(
                _dispatch(
                    app,
                    _frame(
                        f"create-race-{index}",
                        "POST",
                        "/chat/sessions",
                        cookie=cookie,
                        body={
                            "workflowType": "inspectionAnalysis",
                            "title": "Inspection review",
                            "clientSessionId": session_key,
                        },
                    ),
                )
                for index in range(2)
            )
        )
        # A foreign employee reusing the key must not reach the session.
        foreign = json.loads(
            await _dispatch(
                app,
                _frame(
                    "create-foreign",
                    "POST",
                    "/chat/sessions",
                    cookie=second_cookie,
                    body={
                        "workflowType": "inspectionAnalysis",
                        "title": "Inspection review",
                        "clientSessionId": session_key,
                    },
                ),
            )
        )
        listed = json.loads(
            await _dispatch(app, _frame("sessions", "GET", "/chat/sessions", cookie=cookie))
        )
        foreign_listed = json.loads(
            await _dispatch(
                app,
                _frame("foreign-sessions", "GET", "/chat/sessions", cookie=second_cookie),
            )
        )

    responses = [json.loads(response) for response in raced]
    created_session = _payload(created)
    assert created["status"] == 200
    assert created_session["clientSessionId"] == session_key
    # Every replay and concurrent create returns the first committed session.
    assert replayed["status"] == 200
    assert _payload(replayed)["sessionId"] == created_session["sessionId"]
    assert _payload(replayed)["title"] == "Inspection review"
    assert [response["status"] for response in responses] == [200, 200]
    assert all(
        _payload(response)["sessionId"] == created_session["sessionId"] for response in responses
    )
    # The foreign key reuse is rejected without exposing the owner's session.
    assert foreign["status"] == 500
    assert _payload(foreign)["code"] == "session_conflict"
    sessions = _payload(listed)["sessions"]
    assert isinstance(sessions, list) and len(sessions) == 1
    foreign_sessions = _payload(foreign_listed)["sessions"]
    assert isinstance(foreign_sessions, list) and len(foreign_sessions) == 0


async def test_chat_requires_capability_and_an_authenticated_employee(tmp_path: Path) -> None:
    app, cookie, _ = await _build_app_with_two_employees(tmp_path)
    async with app.router.lifespan_context(app):
        without_capability = json.loads(
            await _dispatch(app, _frame("no-capability", "GET", "/chat/sessions", capability=None))
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
    app.state.workflow_runner = RecordingWorkflowRunner()
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
                    body={
                        "content": "  Find the corrosion findings.  ",
                        "clientMessageId": str(uuid4()),
                    },
                ),
            )
        )
        retry_key = str(uuid4())
        second_append = json.loads(
            await _dispatch(
                app,
                _frame(
                    "append-2",
                    "POST",
                    f"/chat/sessions/{session_id}/messages",
                    cookie=cookie,
                    body={"content": "Second message", "clientMessageId": retry_key},
                ),
            )
        )
        committed_retry = json.loads(
            await _dispatch(
                app,
                _frame(
                    "append-retry",
                    "POST",
                    f"/chat/sessions/{session_id}/messages",
                    cookie=cookie,
                    body={"content": "Second message", "clientMessageId": retry_key},
                ),
            )
        )
        conflicting_retry = json.loads(
            await _dispatch(
                app,
                _frame(
                    "append-conflict",
                    "POST",
                    f"/chat/sessions/{session_id}/messages",
                    cookie=cookie,
                    body={"content": "Edited retry text", "clientMessageId": retry_key},
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
    assert second_append["status"] == 409
    # A retry of a committed append returns the stored message unchanged, so
    # an ambiguous renderer failure can never duplicate the employee message.
    assert committed_retry["status"] == 409
    assert conflicting_retry["status"] == 409
    messages = _payload(listed)["messages"]
    assert listed["status"] == 200
    assert isinstance(messages, list) and len(messages) == 1
    assert messages[0]["content"] == "Find the corrosion findings."
    assert messages[0]["role"] == "user"
    assert messages[0]["authorUserId"] == owner_id
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


async def test_concurrent_retries_of_one_append_replay_the_stored_message(tmp_path: Path) -> None:
    app, cookie, _ = await _build_app_with_two_employees(tmp_path)
    app.state.workflow_runner = RecordingWorkflowRunner()
    async with app.router.lifespan_context(app):
        session_id = await _create_session(app, cookie)
        retry_key = str(uuid4())
        raced = await asyncio.gather(
            *(
                _dispatch(
                    app,
                    _frame(
                        f"append-race-{index}",
                        "POST",
                        f"/chat/sessions/{session_id}/messages",
                        cookie=cookie,
                        body={"content": "Raced append", "clientMessageId": retry_key},
                    ),
                )
                for index in range(2)
            )
        )
        listed = json.loads(
            await _dispatch(
                app,
                _frame("messages", "GET", f"/chat/sessions/{session_id}/messages", cookie=cookie),
            )
        )

    responses = [json.loads(response) for response in raced]
    assert [response["status"] for response in responses] == [200, 200]
    # The losing insert replays the winning row instead of surfacing an
    # integrity error, so a concurrent retry never returns a 500.
    assert _payload(responses[0])["messageId"] == _payload(responses[1])["messageId"]
    messages = _payload(listed)["messages"]
    assert isinstance(messages, list) and len(messages) == 1
    assert messages[0]["clientMessageId"] == retry_key


async def test_message_admission_requires_a_configured_workflow_runner(tmp_path: Path) -> None:
    app, cookie, _ = await _build_app_with_two_employees(tmp_path)
    async with app.router.lifespan_context(app):
        session_id = await _create_session(app, cookie)
        response = json.loads(
            await _dispatch(
                app,
                _frame(
                    "unavailable-runner",
                    "POST",
                    f"/chat/sessions/{session_id}/messages",
                    cookie=cookie,
                    body={"content": "Do not accept inert work", "clientMessageId": str(uuid4())},
                ),
            )
        )
        listed = json.loads(
            await _dispatch(
                app,
                _frame("messages", "GET", f"/chat/sessions/{session_id}/messages", cookie=cookie),
            )
    )

    assert response["status"] == 503
    assert _payload(response)["code"] == "chat_store_unavailable"
    assert _payload(listed)["messages"] == []


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
                    body={"content": "   ", "clientMessageId": str(uuid4())},
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
                    body={"content": "x" * 20_001, "clientMessageId": str(uuid4())},
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
                    body={"content": "Not my session", "clientMessageId": str(uuid4())},
                ),
            )
        )
        own_sessions = json.loads(
            await _dispatch(app, _frame("own", "GET", "/chat/sessions", cookie=second_cookie))
        )

    assert foreign_messages["status"] == 404
    assert foreign_append["status"] == 404
    assert _payload(own_sessions)["sessions"] == []


async def test_message_replay_does_not_launch_duplicate_work(tmp_path: Path) -> None:
    app, cookie, _ = await _build_app_with_two_employees(tmp_path)
    runner = RecordingWorkflowRunner()
    app.state.workflow_runner = runner
    async with app.router.lifespan_context(app):
        session_id = await _create_session(app, cookie)
        client_message_id = str(uuid4())
        body: dict[str, object] = {
            "content": "Inspect the selected evidence",
            "clientMessageId": client_message_id,
        }
        first = json.loads(
            await _dispatch(
                app,
                _frame(
                    "message-created",
                    "POST",
                    f"/chat/sessions/{session_id}/messages",
                    cookie=cookie,
                    body=body,
                ),
            )
        )
        replay = json.loads(
            await _dispatch(
                app,
                _frame(
                    "message-replayed",
                    "POST",
                    f"/chat/sessions/{session_id}/messages",
                    cookie=cookie,
                    body=body,
                ),
            )
        )

    assert first["status"] == replay["status"] == 200
    assert _payload(first)["messageId"] == _payload(replay)["messageId"]
    assert len(runner.admissions) == 1


class FailingAuditStore:
    """An audit writer that is down; chat creation must stay available."""

    async def append(self, record: object) -> None:
        raise RuntimeError("audit writer unavailable")


async def test_session_creation_survives_an_unavailable_audit_writer(tmp_path: Path) -> None:
    database_path = tmp_path / "workbench.db"
    await provision_initial_employee(
        database_path=database_path,
        username="engineer.one",
        display_name="Engineer One",
        password=PASSWORD,
    )
    database = LocalSQLiteDatabase(database_path)
    app = create_app(
        settings=ApplicationSettings(
            auth_signing_secret=SECRET,
            database_path=database_path,
            local_service_capability=CAPABILITY,
        ),
        dependencies=ApplicationDependencies(
            identity_store=SQLiteIdentityStore(database),
            auth_session_store=SQLiteAuthSessionStore(database),
            audit_store=FailingAuditStore(),
            chat_store=SQLiteWorkflowStore(database),
            startup=database.initialize,
        ),
    )
    async with app.router.lifespan_context(app):
        login = json.loads(
            await _dispatch(
                app,
                _frame(
                    "login",
                    "POST",
                    "/auth/login",
                    body={"username": "engineer.one", "password": PASSWORD},
                ),
            )
        )
        cookie = next(value for name, value in login["headers"] if name == "set-cookie").split(
            ";", 1
        )[0]
        created = json.loads(
            await _dispatch(
                app,
                _frame(
                    "create",
                    "POST",
                    "/chat/sessions",
                    cookie=cookie,
                    body={"workflowType": "inspectionAnalysis", "title": "Audit down"},
                ),
            )
        )
        listed = json.loads(
            await _dispatch(app, _frame("list", "GET", "/chat/sessions", cookie=cookie))
        )

    assert created["status"] == 200
    assert _payload(created)["title"] == "Audit down"
    sessions = _payload(listed)["sessions"]
    assert isinstance(sessions, list) and len(sessions) == 1
