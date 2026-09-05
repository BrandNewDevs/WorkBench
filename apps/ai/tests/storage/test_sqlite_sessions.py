"""Tests for local SQLite initialization and session metadata persistence."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import aiosqlite
import pytest
from pydantic import ValidationError

from app.storage import (
    InvalidSessionStatusError,
    LocalSQLiteDatabase,
    SessionAlreadyExistsError,
    SessionMetadata,
    SQLiteSessionMetadataStore,
)
from app.workflow.contracts import WorkflowStatus


def _session_metadata(
    *,
    session_id: UUID | None = None,
    user_id: UUID | None = None,
    status: WorkflowStatus = WorkflowStatus.ACTIVE,
) -> SessionMetadata:
    return SessionMetadata(
        session_id=session_id or uuid4(),
        user_id=user_id or uuid4(),
        created_at=datetime(2026, 9, 5, 10, 30, tzinfo=UTC),
        status=status,
    )


@pytest.mark.asyncio
async def test_initialize_is_idempotent_and_creates_only_sessions_table(
    tmp_path: Path,
) -> None:
    database = LocalSQLiteDatabase(tmp_path / "metadata" / "workbench.db")

    await database.initialize()
    await database.initialize()

    async with database.open() as connection:
        cursor = await connection.execute(
            "SELECT name FROM sqlite_master WHERE type = ? ORDER BY name",
            ("table",),
        )
        rows = await cursor.fetchall()

    assert database.database_path.is_file()
    assert [row["name"] for row in rows] == ["sessions"]


@pytest.mark.asyncio
async def test_session_lifecycle_create_retrieve_and_update(tmp_path: Path) -> None:
    database = LocalSQLiteDatabase(tmp_path / "workbench.db")
    await database.initialize()
    store = SQLiteSessionMetadataStore(database)
    session = _session_metadata()

    created = await store.create_session(session)
    retrieved = await store.get_session(session.session_id)
    updated = await store.update_session_status(
        session.session_id,
        WorkflowStatus.COMPLETED,
    )

    assert created == session
    assert retrieved == session
    assert updated == session.model_copy(update={"status": WorkflowStatus.COMPLETED})


@pytest.mark.asyncio
async def test_session_metadata_persists_across_store_instances(tmp_path: Path) -> None:
    database_path = tmp_path / "workbench.db"
    first_database = LocalSQLiteDatabase(database_path)
    await first_database.initialize()
    session = _session_metadata()
    await SQLiteSessionMetadataStore(first_database).create_session(session)

    second_database = LocalSQLiteDatabase(database_path)
    retrieved = await SQLiteSessionMetadataStore(second_database).get_session(
        session.session_id
    )

    assert retrieved == session


@pytest.mark.asyncio
async def test_duplicate_session_id_is_rejected_without_overwriting(
    tmp_path: Path,
) -> None:
    database = LocalSQLiteDatabase(tmp_path / "workbench.db")
    await database.initialize()
    store = SQLiteSessionMetadataStore(database)
    original = _session_metadata()
    duplicate = _session_metadata(session_id=original.session_id)
    await store.create_session(original)

    with pytest.raises(SessionAlreadyExistsError, match=str(original.session_id)):
        await store.create_session(duplicate)

    assert await store.get_session(original.session_id) == original


@pytest.mark.asyncio
async def test_missing_session_operations_return_none(tmp_path: Path) -> None:
    database = LocalSQLiteDatabase(tmp_path / "workbench.db")
    await database.initialize()
    store = SQLiteSessionMetadataStore(database)
    missing_session_id = uuid4()

    assert await store.get_session(missing_session_id) is None
    assert (
        await store.update_session_status(missing_session_id, WorkflowStatus.FAILED)
        is None
    )


@pytest.mark.asyncio
async def test_update_rejects_arbitrary_status(tmp_path: Path) -> None:
    database = LocalSQLiteDatabase(tmp_path / "workbench.db")
    await database.initialize()
    store = SQLiteSessionMetadataStore(database)

    with pytest.raises(InvalidSessionStatusError, match="must be one of"):
        await store.update_session_status(uuid4(), "deleted")


def test_session_metadata_requires_utc_created_at() -> None:
    with pytest.raises(ValidationError, match="timezone-aware UTC"):
        SessionMetadata(
            session_id=uuid4(),
            user_id=uuid4(),
            created_at=datetime.now() - timedelta(days=1),
            status=WorkflowStatus.ACTIVE,
        )


@pytest.mark.asyncio
async def test_database_constraint_rejects_arbitrary_status(tmp_path: Path) -> None:
    database = LocalSQLiteDatabase(tmp_path / "workbench.db")
    await database.initialize()

    with pytest.raises(aiosqlite.IntegrityError):
        async with database.open() as connection:
            await connection.execute(
                """
                INSERT INTO sessions (session_id, user_id, created_at, status)
                VALUES (?, ?, ?, ?)
                """,
                (str(uuid4()), str(uuid4()), datetime.now(UTC).isoformat(), "deleted"),
            )
