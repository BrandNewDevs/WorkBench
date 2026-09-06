"""Phase 3 integration coverage for owner-scoped inputs and SSE activity."""

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import aiosqlite
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pwdlib import PasswordHash

from app.auth.contracts import UserRole
from app.config import ApplicationSettings
from app.health import ApplicationDependencies
from app.main import create_app
from app.ports.backend2 import AuditRecord, AuthSessionRecord, StoredIdentity
from app.storage import (
    LocalSessionWorkspaceStore,
    LocalSQLiteDatabase,
    SQLiteActivityEventStore,
    SQLiteSessionFileStore,
    SQLiteWorkflowStore,
)
from app.workflow.contracts import ActivityEvent, ActivityEventType, WorkflowSession, WorkflowType

_CAPABILITY = "c" * 43
_ORIGIN = "http://127.0.0.1:5173"
_SECRET = "session-input-events-test-signing-secret-material-at-least-forty-eight-bytes"
_PASSWORD = "correct horse battery staple"
_PNG = b"\x89PNG\r\n\x1a\n" + b"local image"
_PDF = b"%PDF-1.7\nlocal report"


class _IdentityStore:
    def __init__(self) -> None:
        password_hash = PasswordHash.recommended().hash(_PASSWORD)
        self.identities = {
            "engineer.one": StoredIdentity(
                user_id=uuid4(),
                username="engineer.one",
                display_name="Engineer One",
                role=UserRole.EMPLOYEE,
                password_hash=password_hash,
            ),
            "engineer.two": StoredIdentity(
                user_id=uuid4(),
                username="engineer.two",
                display_name="Engineer Two",
                role=UserRole.EMPLOYEE,
                password_hash=password_hash,
            ),
        }

    async def get_by_username(self, username: str) -> StoredIdentity | None:
        return self.identities.get(username)

    async def get_by_id(self, user_id: UUID) -> StoredIdentity | None:
        return next((item for item in self.identities.values() if item.user_id == user_id), None)


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
    async def append(self, record: AuditRecord) -> None:
        del record


class _FiniteEventStore:
    """A deterministic SSE test adapter that closes after replayed records."""

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
        del owner_user_id
        for event in self.events:
            if event.session_id == session_id and event.event_id > after_event_id:
                yield event


def _headers() -> dict[str, str]:
    return {"Origin": _ORIGIN, "x-workbench-capability": _CAPABILITY}


def _build_app(
    tmp_path: Path,
    *,
    event_store: SQLiteActivityEventStore | _FiniteEventStore,
    limit: int = 50 * 1024 * 1024,
) -> tuple[
    FastAPI,
    SQLiteSessionFileStore,
    SQLiteActivityEventStore | _FiniteEventStore,
    UUID,
]:
    database = LocalSQLiteDatabase(tmp_path / "state" / "workbench.db")
    workflow_store = SQLiteWorkflowStore(database)
    identity_store = _IdentityStore()
    file_store = SQLiteSessionFileStore(
        database, LocalSessionWorkspaceStore(tmp_path / "sessions")
    )
    app = create_app(
        settings=ApplicationSettings(
            auth_signing_secret=_SECRET,
            cors_allowed_origins=(_ORIGIN,),
            local_service_capability=_CAPABILITY,
            database_path=database.database_path,
            sessions_root=tmp_path / "sessions",
            upload_max_bytes=limit,
        ),
        dependencies=ApplicationDependencies(
            identity_store=identity_store,
            auth_session_store=_AuthSessionStore(),
            audit_store=_AuditStore(),
            workflow_store=workflow_store,
            session_file_store=file_store,
            activity_event_store=event_store,
            startup=database.initialize,
        ),
    )
    return app, file_store, event_store, identity_store.identities["engineer.one"].user_id


def _login(client: TestClient, username: str = "engineer.one") -> None:
    response = client.post(
        "/auth/login", headers=_headers(), json={"username": username, "password": _PASSWORD}
    )
    assert response.status_code == 200


def _create_session(client: TestClient, workflow_type: str) -> str:
    response = client.post("/sessions", headers=_headers(), json={"workflowType": workflow_type})
    assert response.status_code == 201
    session_id = response.json()["sessionId"]
    assert isinstance(session_id, str)
    return session_id


def test_uploads_are_contained_and_events_are_durable_and_ordered(tmp_path: Path) -> None:
    event_store = SQLiteActivityEventStore(LocalSQLiteDatabase(tmp_path / "state" / "workbench.db"))
    app, file_store, _, owner_user_id = _build_app(tmp_path, event_store=event_store)

    with TestClient(app) as client:
        _login(client)
        session_id = _create_session(client, "inspectionAnalysis")
        image = client.post(
            f"/sessions/{session_id}/uploads",
            headers=_headers(),
            files={"file": ("..\\site-photo.PNG", _PNG, "application/octet-stream")},
        )
        report = client.post(
            f"/sessions/{session_id}/uploads",
            headers=_headers(),
            files={"file": ("report.pdf", _PDF)},
        )

    assert image.status_code == report.status_code == 201
    payload = image.json()
    assert payload["fileName"] == "site-photo.PNG"
    assert payload["mimeType"] == "image/png"
    assert payload["sizeBytes"] == len(_PNG)
    assert "path" not in payload
    approved = asyncio.run(
        file_store.resolve_approved_path(UUID(payload["uploadId"]), UUID(session_id))
    )
    assert approved is not None
    assert approved.path.read_bytes() == _PNG
    events = asyncio.run(
        event_store.replay(
            session_id=UUID(session_id),
            owner_user_id=owner_user_id,
            after_event_id=0,
        )
    )
    assert [event.event_id for event in events] == [1, 2, 3]
    assert [event.event_type.value for event in events] == [
        "session.created",
        "upload.accepted",
        "upload.accepted",
    ]
    assert all("path" not in event.payload for event in events)
    assert asyncio.run(
        event_store.replay(
            session_id=UUID(session_id),
            owner_user_id=owner_user_id,
            after_event_id=2,
        )
    ) == [events[2]]


def test_upload_rejects_wrong_workflow_spoofed_empty_and_oversized_inputs(tmp_path: Path) -> None:
    event_store = SQLiteActivityEventStore(LocalSQLiteDatabase(tmp_path / "state" / "workbench.db"))
    app, _, _, _ = _build_app(tmp_path, event_store=event_store, limit=9000)

    with TestClient(app) as client:
        _login(client)
        inspection = _create_session(client, "inspectionAnalysis")
        repair = _create_session(client, "codeRepair")
        wrong_workflow = client.post(
            f"/sessions/{inspection}/uploads",
            headers=_headers(),
            files={"file": ("repair.py", b"print('local')")},
        )
        spoofed = client.post(
            f"/sessions/{inspection}/uploads",
            headers=_headers(),
            files={"file": ("image.png", _PDF)},
        )
        empty = client.post(
            f"/sessions/{repair}/uploads", headers=_headers(), files={"file": ("a.txt", b"")}
        )
        oversized = client.post(
            f"/sessions/{repair}/uploads",
            headers=_headers(),
            files={"file": ("large.txt", b"x" * 9001)},
        )

    assert [(response.status_code, response.json()["code"]) for response in (
        wrong_workflow,
        spoofed,
        empty,
        oversized,
    )] == [
        (415, "unsupported_media_type"),
        (415, "unsupported_media_type"),
        (422, "invalid_file"),
        (413, "upload_too_large"),
    ]
    workspace = tmp_path / "sessions" / repair / "uploads"
    assert not workspace.exists() or not list(workspace.iterdir())


def test_events_replay_as_sse_and_enforce_owner_and_event_id_validation(tmp_path: Path) -> None:
    events = _FiniteEventStore()
    app, _, _, _ = _build_app(tmp_path, event_store=events)

    with TestClient(app) as owner:
        _login(owner)
        session_id = _create_session(owner, "codeRepair")
        upload = owner.post(
            f"/sessions/{session_id}/uploads",
            headers=_headers(),
            files={"file": ("validator.py", b"print('local')")},
        )
        replay = owner.get(
            f"/sessions/{session_id}/events", headers={**_headers(), "Last-Event-ID": "1"}
        )
        invalid_id = owner.get(
            f"/sessions/{session_id}/events", headers={**_headers(), "Last-Event-ID": "-1"}
        )
        with TestClient(app) as other:
            _login(other, "engineer.two")
            foreign_upload = other.post(
                f"/sessions/{session_id}/uploads",
                headers=_headers(),
                files={"file": ("foreign.txt", b"local")},
            )
            foreign = other.get(f"/sessions/{session_id}/events", headers=_headers())

    assert upload.status_code == 201
    assert replay.status_code == 200
    assert replay.headers["content-type"].startswith("text/event-stream")
    assert "id: 2\nevent: upload.accepted\ndata: {" in replay.text
    assert "session.created" not in replay.text
    assert invalid_id.status_code == 422
    assert invalid_id.json()["code"] == "invalid_event_id"
    assert foreign_upload.status_code == 404
    assert foreign.status_code == 404
    assert foreign.json()["code"] == "session_not_found"


def test_upload_metadata_is_persisted_without_a_filesystem_path(tmp_path: Path) -> None:
    event_store = SQLiteActivityEventStore(LocalSQLiteDatabase(tmp_path / "state" / "workbench.db"))
    app, _, _, _ = _build_app(tmp_path, event_store=event_store)

    with TestClient(app) as client:
        _login(client)
        session_id = _create_session(client, "codeRepair")
        response = client.post(
            f"/sessions/{session_id}/uploads",
            headers=_headers(),
            files={"file": ("input.json", b'{"local": true}')},
        )

    async def persisted_upload() -> aiosqlite.Row | None:
        async with LocalSQLiteDatabase(tmp_path / "state" / "workbench.db").open() as connection:
            cursor = await connection.execute(
                """SELECT file_name, mime_type, size_bytes, sha256
                FROM workflow_uploads WHERE upload_id = ?""",
                (response.json()["uploadId"],),
            )
            return await cursor.fetchone()

    stored = asyncio.run(persisted_upload())
    assert response.status_code == 201
    assert stored is not None
    assert stored["file_name"] == "input.json"
    assert stored["mime_type"] == "application/json"
    assert "stored_file_name" not in response.json()


def test_upload_rejects_a_session_that_has_left_input_collection(tmp_path: Path) -> None:
    event_store = SQLiteActivityEventStore(LocalSQLiteDatabase(tmp_path / "state" / "workbench.db"))
    app, _, _, _ = _build_app(tmp_path, event_store=event_store)

    with TestClient(app) as client:
        _login(client)
        session_id = _create_session(client, "inspectionAnalysis")

        async def move_to_extracting() -> None:
            database = LocalSQLiteDatabase(tmp_path / "state" / "workbench.db")
            async with database.open() as connection:
                await connection.execute(
                    "UPDATE workflow_sessions SET stage = ? WHERE session_id = ?",
                    ("extracting", session_id),
                )

        asyncio.run(move_to_extracting())
        response = client.post(
            f"/sessions/{session_id}/uploads",
            headers=_headers(),
            files={"file": ("report.pdf", _PDF)},
        )

    assert response.status_code == 409
    assert response.json()["code"] == "invalid_workflow_stage"


def test_activity_store_delivers_a_live_record_after_subscribe(tmp_path: Path) -> None:
    """The local event adapter supports live delivery independently of SSE transport."""

    database = LocalSQLiteDatabase(tmp_path / "state" / "workbench.db")
    store = SQLiteActivityEventStore(database)

    async def observe() -> ActivityEvent:
        await database.initialize()
        session_id = uuid4()
        owner_user_id = uuid4()
        workflow_store = SQLiteWorkflowStore(database)
        now = datetime.now(UTC)
        await workflow_store.create_session(
            WorkflowSession(
                session_id=session_id,
                owner_user_id=owner_user_id,
                workflow_type=WorkflowType.CODE_REPAIR,
                title="Live event",
                created_at=now,
                updated_at=now,
            )
        )
        subscription = cast(
            AsyncGenerator[ActivityEvent, None],
            store.subscribe(
                session_id=session_id,
                owner_user_id=owner_user_id,
                after_event_id=0,
            ),
        )
        pending = asyncio.ensure_future(anext(subscription))
        await asyncio.sleep(0)
        await store.append(
            ActivityEvent(
                event_id=0,
                session_id=session_id,
                event_type=ActivityEventType.PROGRESS,
                occurred_at=now,
                payload={
                    "stage": "planning",
                    "completedUnits": 1,
                    "totalUnits": 1,
                },
            ),
            owner_user_id=owner_user_id,
        )
        received = await pending
        await subscription.aclose()
        return received

    received = asyncio.run(observe())
    assert received.event_id == 1
    assert received.event_type.value == "workflow.progress"


def test_initialize_migrates_the_temporary_phase3_activity_schema(tmp_path: Path) -> None:
    database = LocalSQLiteDatabase(tmp_path / "state" / "workbench.db")

    async def migrate() -> list[ActivityEvent]:
        await database.initialize()
        owner_user_id = uuid4()
        session = WorkflowSession(
            session_id=uuid4(),
            owner_user_id=owner_user_id,
            workflow_type=WorkflowType.INSPECTION_ANALYSIS,
            title="Legacy activity",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        await SQLiteWorkflowStore(database).create_session(session)
        async with database.open() as connection:
            await connection.execute("DROP INDEX activity_events_owner_replay")
            await connection.execute("DROP TABLE activity_events")
            await connection.execute(
                """CREATE TABLE activity_events (
                    session_id TEXT NOT NULL REFERENCES workflow_sessions(session_id),
                    event_id INTEGER NOT NULL CHECK (event_id >= 0),
                    workflow_run_id TEXT,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (session_id, event_id)
                )"""
            )
            await connection.execute(
                """INSERT INTO activity_events
                (session_id, event_id, workflow_run_id, event_type, occurred_at, payload)
                VALUES (?, 0, NULL, 'session.created', ?, ?)""",
                (
                    str(session.session_id),
                    session.created_at.isoformat(),
                    '{"stage":"collectingInputs","title":"Legacy activity"}',
                ),
            )

        await database.initialize()
        return await SQLiteActivityEventStore(database).replay(
            session_id=session.session_id,
            owner_user_id=owner_user_id,
            after_event_id=0,
        )

    migrated = asyncio.run(migrate())
    assert len(migrated) == 1
    assert migrated[0].event_id == 1
    assert migrated[0].event_type is ActivityEventType.SESSION_CREATED
    assert migrated[0].payload == {}
