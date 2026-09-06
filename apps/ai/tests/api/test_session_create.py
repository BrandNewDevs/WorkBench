"""Focused API tests for authenticated workflow-session creation."""

import asyncio
from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import aiosqlite
from fastapi.testclient import TestClient
from pwdlib import PasswordHash

from app.auth.contracts import UserRole
from app.auth.provisioning import provision_initial_employee
from app.config import ApplicationSettings
from app.health import ApplicationDependencies
from app.main import create_app
from app.ports.local_backend import AuditRecord, AuthSessionRecord, StoredIdentity, WorkflowStore
from app.storage import LocalSQLiteDatabase
from app.workflow.contracts import ActivityEvent, WorkflowSession, WorkflowType

_CAPABILITY = "c" * 43
_ORIGIN = "http://127.0.0.1:5173"
_PASSWORD = "correct horse battery staple"
_SECRET = "session-create-test-signing-secret-material-at-least-forty-eight-bytes"


class _IdentityStore:
    def __init__(self) -> None:
        identity = StoredIdentity(
            user_id=uuid4(),
            username="engineer.one",
            display_name="Engineer One",
            role=UserRole.EMPLOYEE,
            password_hash=PasswordHash.recommended().hash(_PASSWORD),
        )
        self._identity = identity

    async def get_by_username(self, username: str) -> StoredIdentity | None:
        return self._identity if username == self._identity.username else None

    async def get_by_id(self, user_id: UUID) -> StoredIdentity | None:
        return self._identity if user_id == self._identity.user_id else None


class _AuthSessionStore:
    def __init__(self) -> None:
        self.records: dict[UUID, AuthSessionRecord] = {}

    async def create(self, record: AuthSessionRecord) -> None:
        self.records[record.token_id] = record

    async def get_active(self, token_id: UUID, now: datetime) -> AuthSessionRecord | None:
        record = self.records.get(token_id)
        if record is None or record.revoked_at is not None or record.expires_at <= now:
            return None
        return record

    async def revoke(self, token_id: UUID, revoked_at: datetime) -> bool:
        record = self.records.get(token_id)
        if record is None or record.revoked_at is not None:
            return False
        self.records[token_id] = record.model_copy(update={"revoked_at": revoked_at})
        return True


class _AuditStore:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []
        self.fail = False

    async def append(self, record: AuditRecord) -> None:
        if self.fail:
            raise RuntimeError("audit unavailable")
        self.records.append(record)


class _EventStore:
    def __init__(self) -> None:
        self.events: list[ActivityEvent] = []

    async def append(
        self, event: ActivityEvent, *, owner_user_id: UUID
    ) -> ActivityEvent:
        del owner_user_id
        stored = event.model_copy(update={"event_id": len(self.events) + 1})
        self.events.append(stored)
        return stored

    async def replay(
        self, *, session_id: UUID, owner_user_id: UUID, after_event_id: int
    ) -> list[ActivityEvent]:
        del owner_user_id
        return [
            event
            for event in self.events
            if event.session_id == session_id and event.event_id > after_event_id
        ]

    async def subscribe(
        self, *, session_id: UUID, owner_user_id: UUID, after_event_id: int
    ) -> AsyncGenerator[ActivityEvent, None]:
        del session_id, owner_user_id, after_event_id
        while True:
            await asyncio.sleep(3600)
            if self.events:
                yield self.events[-1]


class _WorkflowStore:
    def __init__(self) -> None:
        self.sessions: list[WorkflowSession] = []
        self.fail = False

    async def create_session(self, session: WorkflowSession) -> WorkflowSession:
        if self.fail:
            raise RuntimeError("workflow store unavailable")
        self.sessions.append(session)
        return session


def _headers(*, origin: str | None = _ORIGIN) -> dict[str, str]:
    headers = {"x-workbench-capability": _CAPABILITY}
    if origin is not None:
        headers["Origin"] = origin
    return headers


def _client() -> tuple[TestClient, _WorkflowStore, _AuditStore, _EventStore]:
    workflow_store = _WorkflowStore()
    audit_store = _AuditStore()
    event_store = _EventStore()
    app = create_app(
        settings=ApplicationSettings(
            auth_signing_secret=_SECRET,
            cors_allowed_origins=(_ORIGIN,),
            local_service_capability=_CAPABILITY,
        ),
        dependencies=ApplicationDependencies(
            identity_store=_IdentityStore(),
            auth_session_store=_AuthSessionStore(),
            audit_store=audit_store,
            workflow_store=cast(WorkflowStore, workflow_store),
            activity_event_store=event_store,
        ),
    )
    return TestClient(app), workflow_store, audit_store, event_store


def _login(client: TestClient) -> str:
    response = client.post(
        "/auth/login",
        headers=_headers(),
        json={"username": "engineer.one", "password": _PASSWORD},
    )
    assert response.status_code == 200
    employee_id = response.json()["session"]["user"]["employeeId"]
    assert isinstance(employee_id, str)
    return employee_id


def test_creates_both_workflow_types_with_public_initial_state_and_audit() -> None:
    client, workflow_store, audit_store, event_store = _client()

    with client:
        _login(client)
        inspection = client.post(
            "/sessions",
            headers=_headers(),
            json={"workflowType": "inspectionAnalysis"},
        )
        repair = client.post(
            "/sessions",
            headers=_headers(),
            json={"workflowType": "codeRepair", "title": "  Repair pump parser  "},
        )

    assert inspection.status_code == 201
    assert repair.status_code == 201
    assert inspection.json() == {
        "sessionId": inspection.json()["sessionId"],
        "workflowType": "inspectionAnalysis",
        "title": "Inspection analysis",
        "stage": "collectingInputs",
        "status": "active",
        "createdAt": inspection.json()["createdAt"],
    }
    assert repair.json()["title"] == "Repair pump parser"
    assert set(inspection.json()) == {
        "sessionId",
        "workflowType",
        "title",
        "stage",
        "status",
        "createdAt",
    }
    assert len(workflow_store.sessions) == 2
    assert workflow_store.sessions[0].owner_user_id == workflow_store.sessions[1].owner_user_id
    assert workflow_store.sessions[0].workflow_type is WorkflowType.INSPECTION_ANALYSIS
    assert workflow_store.sessions[0].client_session_id is None
    assert workflow_store.sessions[0].stage.value == "collectingInputs"
    assert workflow_store.sessions[0].status.value == "active"
    created_audits = [
        record for record in audit_store.records if record.action.value == "sessionCreated"
    ]
    assert len(created_audits) == 2
    assert created_audits[0].actor_user_id == workflow_store.sessions[0].owner_user_id
    assert created_audits[0].session_id == workflow_store.sessions[0].session_id
    assert created_audits[0].workflow_run_id is None
    assert [event.event_type.value for event in event_store.events] == [
        "session.created",
        "session.created",
    ]


def test_rejects_unauthenticated_and_untrusted_origin_requests() -> None:
    client, workflow_store, _, _ = _client()

    with client:
        unauthenticated = client.post(
            "/sessions",
            headers=_headers(),
            json={"workflowType": "inspectionAnalysis"},
        )
        _login(client)
        untrusted_origin = client.post(
            "/sessions",
            headers=_headers(origin="http://127.0.0.1:8999"),
            json={"workflowType": "inspectionAnalysis"},
        )

    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["code"] == "invalid_session"
    assert untrusted_origin.status_code == 403
    assert untrusted_origin.json()["code"] == "invalid_origin"
    assert workflow_store.sessions == []


def test_validates_title_workflow_type_and_unknown_fields() -> None:
    client, workflow_store, _, _ = _client()

    with client:
        _login(client)
        blank_title = client.post(
            "/sessions",
            headers=_headers(),
            json={"workflowType": "inspectionAnalysis", "title": "   "},
        )
        unsupported_workflow = client.post(
            "/sessions",
            headers=_headers(),
            json={"workflowType": "unsupported"},
        )
        unknown_field = client.post(
            "/sessions",
            headers=_headers(),
            json={"workflowType": "inspectionAnalysis", "unexpected": True},
        )

    assert [
        response.status_code for response in (blank_title, unsupported_workflow, unknown_field)
    ] == [
        422,
        422,
        422,
    ]
    assert all(
        response.json()["code"] == "validation_error"
        for response in (
            blank_title,
            unsupported_workflow,
            unknown_field,
        )
    )
    assert workflow_store.sessions == []


def test_returns_sanitized_store_error_and_keeps_creation_when_audit_is_down() -> None:
    client, workflow_store, audit_store, _ = _client()

    with client:
        _login(client)
        workflow_store.fail = True
        unavailable = client.post(
            "/sessions",
            headers=_headers(),
            json={"workflowType": "inspectionAnalysis"},
        )
        workflow_store.fail = False
        audit_store.fail = True
        created = client.post(
            "/sessions",
            headers=_headers(),
            json={"workflowType": "codeRepair"},
        )

    assert unavailable.status_code == 503
    assert unavailable.json() == {
        "code": "workflow_store_unavailable",
        "message": "The local workflow storage is unavailable.",
        "requestId": None,
    }
    assert created.status_code == 201
    assert len(workflow_store.sessions) == 1


def test_creates_the_workflow_session_and_audit_row_in_local_sqlite(tmp_path: Path) -> None:
    database_path = tmp_path / "state" / "workbench.db"
    asyncio.run(
        provision_initial_employee(
            database_path=database_path,
            username="engineer.one",
            display_name="Engineer One",
            password=_PASSWORD,
        )
    )
    app = create_app(
        settings=ApplicationSettings(
            auth_signing_secret=_SECRET,
            database_path=database_path,
            cors_allowed_origins=(_ORIGIN,),
            local_service_capability=_CAPABILITY,
        )
    )

    with TestClient(app) as client:
        employee_id = _login(client)
        response = client.post(
            "/sessions",
            headers=_headers(),
            json={"workflowType": "inspectionAnalysis", "title": "SQLite smoke"},
        )

    session_id = response.json()["sessionId"]

    async def persisted_rows() -> tuple[aiosqlite.Row | None, aiosqlite.Row | None]:
        async with LocalSQLiteDatabase(database_path).open() as connection:
            session = await (
                await connection.execute(
                    """SELECT owner_user_id, workflow_type, title, stage, status
                    FROM workflow_sessions WHERE session_id = ?""",
                    (session_id,),
                )
            ).fetchone()
            audit = await (
                await connection.execute(
                    """SELECT action, session_id, workflow_run_id FROM audit_records
                    WHERE session_id = ?""",
                    (session_id,),
                )
            ).fetchone()
        return session, audit

    session_row, audit_row = asyncio.run(persisted_rows())
    assert response.status_code == 201
    assert session_row is not None
    assert session_row["owner_user_id"] == employee_id
    assert session_row["workflow_type"] == "inspectionAnalysis"
    assert session_row["title"] == "SQLite smoke"
    assert session_row["stage"] == "collectingInputs"
    assert session_row["status"] == "active"
    assert audit_row is not None
    assert audit_row["action"] == "sessionCreated"
    assert audit_row["session_id"] == session_id
    assert audit_row["workflow_run_id"] is None
