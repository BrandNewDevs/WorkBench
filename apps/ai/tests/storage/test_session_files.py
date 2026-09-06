"""Focused tests for durable, owner-scoped local session uploads."""

import asyncio
import hashlib
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import aiosqlite
import pytest

from app.ai.knowledge.document_parser import LocalDocumentParser
from app.ai.schemas import SourceDocument
from app.ports.local_backend import StoredUpload
from app.storage import (
    LocalSessionFileStore,
    LocalSessionWorkspaceStore,
    LocalSQLiteDatabase,
    SessionFileContextMismatchError,
    SessionUploadCleanupError,
    SQLiteWorkflowStore,
    UploadAlreadyExistsError,
    UploadIntegrityError,
    WorkspacePathError,
)
from app.workflow.contracts import (
    WorkflowSession,
    WorkflowStage,
    WorkflowStatus,
    WorkflowType,
)

_CREATED_AT = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


async def _chunks(*values: bytes) -> AsyncIterator[bytes]:
    for value in values:
        yield value


def _session(
    *,
    session_id: UUID | None = None,
    owner_user_id: UUID | None = None,
) -> WorkflowSession:
    return WorkflowSession(
        session_id=session_id or uuid4(),
        owner_user_id=owner_user_id or uuid4(),
        workflow_type=WorkflowType.INSPECTION_ANALYSIS,
        title="Inspection workflow",
        stage=WorkflowStage.COLLECTING_INPUTS,
        status=WorkflowStatus.ACTIVE,
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )


async def _stores(
    tmp_path: Path,
    *,
    max_upload_bytes: int = 100 * 1024 * 1024,
) -> tuple[
    LocalSQLiteDatabase,
    LocalSessionWorkspaceStore,
    SQLiteWorkflowStore,
    LocalSessionFileStore,
]:
    database = LocalSQLiteDatabase(tmp_path / "state" / "workbench.db")
    await database.initialize()
    workspaces = LocalSessionWorkspaceStore(tmp_path / "sessions")
    return (
        database,
        workspaces,
        SQLiteWorkflowStore(database),
        LocalSessionFileStore(
            database,
            workspaces,
            max_upload_bytes=max_upload_bytes,
        ),
    )


async def _save(
    store: LocalSessionFileStore,
    session: WorkflowSession,
    *,
    upload_id: UUID | None = None,
    source_id: UUID | None = None,
    file_name: str = "report.txt",
    mime_type: str = "text/plain",
    content: tuple[bytes, ...] = (b"local ", b"inspection"),
) -> StoredUpload:
    return await store.save_upload(
        session=session,
        upload_id=upload_id or uuid4(),
        source_id=source_id or uuid4(),
        file_name=file_name,
        mime_type=mime_type,
        content=_chunks(*content),
    )


@pytest.mark.asyncio
async def test_initialize_creates_metadata_only_upload_schema(tmp_path: Path) -> None:
    database, _, _, _ = await _stores(tmp_path)
    await database.initialize()

    async with database.open() as connection:
        columns = await (
            await connection.execute("PRAGMA table_info(workflow_uploads)")
        ).fetchall()
        foreign_keys = list(
            await (
                await connection.execute("PRAGMA foreign_key_list(workflow_uploads)")
            ).fetchall()
        )

    names = [row["name"] for row in columns]
    assert names == [
        "upload_id",
        "session_id",
        "source_id",
        "stored_file_name",
        "file_name",
        "mime_type",
        "size_bytes",
        "sha256",
        "created_at",
    ]
    assert not {"content", "bytes", "path", "url"}.intersection(names)
    assert foreign_keys[0]["table"] == "workflow_sessions"
    assert foreign_keys[0]["on_delete"] == "CASCADE"


@pytest.mark.asyncio
async def test_initialize_migrates_legacy_upload_metadata_constraints(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state" / "workbench.db"
    database_path.parent.mkdir()
    session = _session()
    upload_id = uuid4()
    source_id = uuid4()

    async with aiosqlite.connect(database_path) as connection:
        await connection.execute(
            """CREATE TABLE workflow_sessions (
            session_id TEXT PRIMARY KEY NOT NULL,
            owner_user_id TEXT NOT NULL,
            workflow_type TEXT NOT NULL,
            title TEXT NOT NULL,
            stage TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            client_session_id TEXT)"""
        )
        await connection.execute(
            """CREATE TABLE workflow_uploads (
            upload_id TEXT PRIMARY KEY NOT NULL,
            session_id TEXT NOT NULL REFERENCES workflow_sessions(session_id)
                ON DELETE CASCADE,
            source_id TEXT NOT NULL UNIQUE,
            stored_file_name TEXT NOT NULL,
            file_name TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
            sha256 TEXT NOT NULL CHECK (
                length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            created_at TEXT NOT NULL,
            UNIQUE (session_id, stored_file_name))"""
        )
        await connection.execute(
            """INSERT INTO workflow_sessions
            (session_id, owner_user_id, workflow_type, title, stage, status,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(session.session_id),
                str(session.owner_user_id),
                session.workflow_type.value,
                session.title,
                session.stage.value,
                session.status.value,
                session.created_at.isoformat(),
                session.updated_at.isoformat(),
            ),
        )
        await connection.execute(
            """INSERT INTO workflow_uploads
            (upload_id, session_id, source_id, stored_file_name, file_name,
             mime_type, size_bytes, sha256, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(upload_id),
                str(session.session_id),
                str(source_id),
                f"{upload_id}.upload",
                "report.txt",
                "text/plain",
                4,
                hashlib.sha256(b"data").hexdigest(),
                _CREATED_AT.isoformat(),
            ),
        )
        await connection.commit()

    database = LocalSQLiteDatabase(database_path)
    await database.initialize()

    async with database.open() as connection:
        row = await (
            await connection.execute(
                "SELECT file_name FROM workflow_uploads WHERE upload_id = ?",
                (str(upload_id),),
            )
        ).fetchone()
        assert row is not None
        assert row["file_name"] == "report.txt"
        with pytest.raises(aiosqlite.IntegrityError):
            await connection.execute(
                """INSERT INTO workflow_uploads
                (upload_id, session_id, source_id, stored_file_name, file_name,
                 mime_type, size_bytes, sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid4()),
                    str(session.session_id),
                    str(uuid4()),
                    "unsafe.upload",
                    "../unsafe.txt",
                    "text/plain",
                    1,
                    hashlib.sha256(b"x").hexdigest(),
                    _CREATED_AT.isoformat(),
                ),
            )


@pytest.mark.asyncio
async def test_streamed_upload_round_trips_and_resolves_after_restart(tmp_path: Path) -> None:
    database, workspaces, workflows, store = await _stores(tmp_path)
    session = _session()
    await workflows.create_session(session)

    upload = await _save(store, session)
    restarted = LocalSessionFileStore(
        LocalSQLiteDatabase(database.database_path),
        LocalSessionWorkspaceStore(workspaces.sessions_root),
    )
    restored = await restarted.get_upload(
        upload_id=upload.upload_id,
        session_id=session.session_id,
        owner_user_id=session.owner_user_id,
    )
    approved = await restarted.resolve_approved_path(
        upload_id=upload.upload_id,
        session_id=session.session_id,
        owner_user_id=session.owner_user_id,
    )

    assert restored == upload
    assert upload.size_bytes == len(b"local inspection")
    assert upload.sha256 == hashlib.sha256(b"local inspection").hexdigest()
    assert approved is not None
    assert approved.path.name == f"{upload.upload_id}.upload"
    assert approved.path.read_bytes() == b"local inspection"
    assert approved.source_id == str(upload.source_id)
    assert approved.session_id == str(session.session_id)


@pytest.mark.asyncio
async def test_get_upload_is_owner_and_session_scoped(tmp_path: Path) -> None:
    _, _, workflows, store = await _stores(tmp_path)
    session = _session()
    await workflows.create_session(session)
    upload = await _save(store, session)

    assert (
        await store.get_upload(
            upload_id=upload.upload_id,
            session_id=session.session_id,
            owner_user_id=session.owner_user_id,
        )
        == upload
    )
    assert (
        await store.get_upload(
            upload_id=upload.upload_id,
            session_id=uuid4(),
            owner_user_id=session.owner_user_id,
        )
        is None
    )
    assert (
        await store.resolve_approved_path(
            upload_id=upload.upload_id,
            session_id=session.session_id,
            owner_user_id=uuid4(),
        )
        is None
    )
    assert (
        await store.get_upload(
            upload_id=upload.upload_id,
            session_id=session.session_id,
            owner_user_id=uuid4(),
        )
        is None
    )


@pytest.mark.asyncio
async def test_physical_upload_extension_is_compatible_with_explicit_mime_consumers(
    tmp_path: Path,
) -> None:
    _, _, workflows, store = await _stores(tmp_path)
    session = _session()
    await workflows.create_session(session)
    upload = await _save(store, session, content=(b"# Heading\nLocal text",))
    approved = await store.resolve_approved_path(
        upload_id=upload.upload_id,
        session_id=session.session_id,
        owner_user_id=session.owner_user_id,
    )

    assert approved is not None
    parsed = LocalDocumentParser().parse(
        SourceDocument(
            document_id=str(upload.upload_id),
            document_name=upload.file_name,
            mime_type=upload.mime_type,
            source_id=str(upload.source_id),
            approved_path=approved,
        )
    )
    assert approved.path.suffix == ".upload"
    assert parsed.source_id == str(upload.source_id)
    assert parsed.blocks


@pytest.mark.asyncio
async def test_save_upload_enforces_persisted_session_context(tmp_path: Path) -> None:
    _, _, workflows, store = await _stores(tmp_path)
    session = _session()

    with pytest.raises(SessionFileContextMismatchError):
        await _save(store, session)

    await workflows.create_session(session)
    spoofed = session.model_copy(update={"owner_user_id": uuid4()})
    with pytest.raises(SessionFileContextMismatchError):
        await _save(store, spoofed)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "file_name",
    [
        "../secret.txt",
        "C:\\secret.txt",
        "nested/report.txt",
        "report?.txt",
        "report.txt.",
        "NUL.txt",
    ],
)
async def test_upload_rejects_traversal_absolute_and_windows_unsafe_names(
    tmp_path: Path,
    file_name: str,
) -> None:
    _, _, workflows, store = await _stores(tmp_path)
    session = _session()
    await workflows.create_session(session)

    with pytest.raises(WorkspacePathError):
        await _save(store, session, file_name=file_name)


@pytest.mark.asyncio
async def test_oversized_or_cancelled_upload_removes_temporary_files(
    tmp_path: Path,
) -> None:
    _, workspaces, workflows, store = await _stores(tmp_path, max_upload_bytes=5)
    session = _session()
    await workflows.create_session(session)

    with pytest.raises(ValueError, match="byte limit"):
        await _save(store, session, content=(b"123", b"456"))
    assert list(workspaces.get_session_workspace(str(session.session_id)).uploads.iterdir()) == []

    started = asyncio.Event()

    async def interrupted() -> AsyncIterator[bytes]:
        yield b"12"
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        store.save_upload(
            session=session,
            upload_id=uuid4(),
            source_id=uuid4(),
            file_name="cancelled.txt",
            mime_type="text/plain",
            content=interrupted(),
        )
    )
    await started.wait()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert list(workspaces.get_session_workspace(str(session.session_id)).uploads.iterdir()) == []


@pytest.mark.asyncio
async def test_empty_and_non_bytes_upload_content_is_rejected(tmp_path: Path) -> None:
    _, workspaces, workflows, store = await _stores(tmp_path)
    session = _session()
    await workflows.create_session(session)

    with pytest.raises(ValueError, match="empty"):
        await _save(store, session, content=())

    async def invalid() -> AsyncIterator[bytes]:
        yield "not bytes"  # type: ignore[misc]

    with pytest.raises(TypeError, match="chunks must be bytes"):
        await store.save_upload(
            session=session,
            upload_id=uuid4(),
            source_id=uuid4(),
            file_name="bad.txt",
            mime_type="text/plain",
            content=invalid(),
        )
    assert list(workspaces.get_session_workspace(str(session.session_id)).uploads.iterdir()) == []


@pytest.mark.asyncio
async def test_duplicate_upload_is_idempotent_but_conflicts_are_rejected(
    tmp_path: Path,
) -> None:
    _, workspaces, workflows, store = await _stores(tmp_path)
    session = _session()
    await workflows.create_session(session)
    upload_id = uuid4()
    source_id = uuid4()

    first = await _save(store, session, upload_id=upload_id, source_id=source_id)
    repeated = await _save(store, session, upload_id=upload_id, source_id=source_id)
    assert repeated == first

    with pytest.raises(UploadAlreadyExistsError):
        await _save(
            store,
            session,
            upload_id=upload_id,
            source_id=source_id,
            content=(b"different",),
        )
    with pytest.raises(UploadAlreadyExistsError):
        await _save(store, session, source_id=source_id)
    upload_paths = workspaces.get_session_workspace(str(session.session_id)).uploads
    assert [path.name for path in upload_paths.iterdir()] == [f"{upload_id}.upload"]


@pytest.mark.asyncio
async def test_duplicate_visible_names_do_not_overwrite_each_other(tmp_path: Path) -> None:
    _, workspaces, workflows, store = await _stores(tmp_path)
    session = _session()
    await workflows.create_session(session)

    first, second = await asyncio.gather(
        _save(store, session, content=(b"first",)),
        _save(store, session, content=(b"second",)),
    )
    assert first.file_name == second.file_name == "report.txt"
    assert first.upload_id != second.upload_id
    paths = workspaces.get_session_workspace(str(session.session_id)).uploads
    assert (paths / f"{first.upload_id}.upload").read_bytes() == b"first"
    assert (paths / f"{second.upload_id}.upload").read_bytes() == b"second"


@pytest.mark.asyncio
async def test_concurrent_identical_upload_retries_share_one_record(tmp_path: Path) -> None:
    _, workspaces, workflows, store = await _stores(tmp_path)
    session = _session()
    await workflows.create_session(session)
    upload_id = uuid4()
    source_id = uuid4()

    first, second = await asyncio.gather(
        _save(store, session, upload_id=upload_id, source_id=source_id),
        _save(store, session, upload_id=upload_id, source_id=source_id),
    )

    assert first == second
    paths = workspaces.get_session_workspace(str(session.session_id)).uploads
    assert [path.name for path in paths.iterdir()] == [f"{upload_id}.upload"]


@pytest.mark.asyncio
async def test_symlink_or_tampered_upload_is_not_resolved(tmp_path: Path) -> None:
    _, _, workflows, store = await _stores(tmp_path)
    session = _session()
    await workflows.create_session(session)
    upload = await _save(store, session)
    approved = await store.resolve_approved_path(
        upload_id=upload.upload_id,
        session_id=session.session_id,
        owner_user_id=session.owner_user_id,
    )
    assert approved is not None

    approved.path.write_bytes(b"tampered")
    with pytest.raises(UploadIntegrityError, match=r"size|hash"):
        await store.resolve_approved_path(
            upload_id=upload.upload_id,
            session_id=session.session_id,
            owner_user_id=session.owner_user_id,
        )

    approved.path.unlink()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"local inspection")
    try:
        approved.path.symlink_to(outside)
    except OSError as error:
        if os.name == "nt" and getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows symlink creation requires Developer Mode or elevation")
        raise
    with pytest.raises(UploadIntegrityError, match="unsafe"):
        await store.resolve_approved_path(
            upload_id=upload.upload_id,
            session_id=session.session_id,
            owner_user_id=session.owner_user_id,
        )


@pytest.mark.asyncio
async def test_database_failure_removes_promoted_file(tmp_path: Path) -> None:
    database, workspaces, workflows, store = await _stores(tmp_path)
    session = _session()
    await workflows.create_session(session)
    upload_id = uuid4()
    async with database.open() as connection:
        await connection.execute(
            """CREATE TRIGGER reject_upload BEFORE INSERT ON workflow_uploads
            BEGIN SELECT RAISE(ABORT, 'test failure'); END"""
        )

    with pytest.raises(aiosqlite.IntegrityError):
        await _save(store, session, upload_id=upload_id)
    workspace = workspaces.get_session_workspace(str(session.session_id))
    assert not (workspace.uploads / f"{upload_id}.upload").exists()
    assert list(workspace.uploads.iterdir()) == []


@pytest.mark.asyncio
async def test_cleanup_removes_only_owned_session_uploads_and_metadata(
    tmp_path: Path,
) -> None:
    _, workspaces, workflows, store = await _stores(tmp_path)
    first_session = _session()
    second_session = _session()
    await workflows.create_session(first_session)
    await workflows.create_session(second_session)
    first = await _save(store, first_session)
    second = await _save(store, second_session)
    first_path = workspaces.file_path(
        str(first_session.session_id), "uploads", f"{first.upload_id}.upload"
    )
    first_path.unlink()

    assert (
        await store.cleanup_session_uploads(
            session_id=first_session.session_id,
            owner_user_id=first_session.owner_user_id,
        )
        == 1
    )
    assert (
        await store.get_upload(
            upload_id=first.upload_id,
            session_id=first_session.session_id,
            owner_user_id=first_session.owner_user_id,
        )
        is None
    )
    assert (
        await store.get_upload(
            upload_id=second.upload_id,
            session_id=second_session.session_id,
            owner_user_id=second_session.owner_user_id,
        )
        == second
    )
    with pytest.raises(SessionFileContextMismatchError):
        await store.cleanup_session_uploads(
            session_id=second_session.session_id,
            owner_user_id=first_session.owner_user_id,
        )


@pytest.mark.asyncio
async def test_cleanup_failure_preserves_metadata_and_can_be_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspaces, workflows, store = await _stores(tmp_path)
    session = _session()
    await workflows.create_session(session)
    retained = await _save(store, session, content=(b"retained",))
    removed = await _save(store, session, content=(b"removed",))
    retained_path = workspaces.file_path(
        str(session.session_id), "uploads", f"{retained.upload_id}.upload"
    )
    original_unlink = Path.unlink
    failed_once = False

    def fail_once(candidate: Path, missing_ok: bool = False) -> None:
        nonlocal failed_once
        if candidate == retained_path and not failed_once:
            failed_once = True
            raise PermissionError("simulated permission failure")
        original_unlink(candidate, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_once)
    with pytest.raises(SessionUploadCleanupError) as captured:
        await store.cleanup_session_uploads(
            session_id=session.session_id,
            owner_user_id=session.owner_user_id,
        )
    assert captured.value.cleaned_count == 1
    assert captured.value.failed_count == 1
    assert (
        await store.get_upload(
            upload_id=retained.upload_id,
            session_id=session.session_id,
            owner_user_id=session.owner_user_id,
        )
        == retained
    )
    assert (
        await store.get_upload(
            upload_id=removed.upload_id,
            session_id=session.session_id,
            owner_user_id=session.owner_user_id,
        )
        is None
    )

    assert (
        await store.cleanup_session_uploads(
            session_id=session.session_id,
            owner_user_id=session.owner_user_id,
        )
        == 1
    )
    assert not retained_path.exists()
