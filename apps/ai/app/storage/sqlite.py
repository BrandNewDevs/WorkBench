"""Local SQLite foundation and workflow-session metadata persistence."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import aiosqlite
from pydantic import BaseModel, ConfigDict, field_validator

from app.workflow.contracts import WorkflowStatus

_CREATE_SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY NOT NULL,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('active', 'completed', 'failed', 'approvalRejected')
    )
)
"""


class SessionAlreadyExistsError(RuntimeError):
    """Raised when session metadata already exists for a session identifier."""


class InvalidSessionStatusError(ValueError):
    """Raised when a caller supplies a status outside the canonical session states."""


class SessionMetadata(BaseModel):
    """Durable ownership and lifecycle metadata for one workflow session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: UUID
    user_id: UUID
    created_at: datetime
    status: WorkflowStatus

    @field_validator("created_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        """Require an unambiguous UTC timestamp before persistence."""

        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("created_at must be timezone-aware UTC")
        return value.astimezone(UTC)


class LocalSQLiteDatabase:
    """Open and initialize one application-controlled SQLite database file."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path.resolve(strict=False)

    @property
    def database_path(self) -> Path:
        """Return the resolved path of the local database file."""

        return self._database_path

    async def initialize(self) -> None:
        """Create the current schema safely when it does not already exist."""

        async with self.open() as connection:
            await connection.execute(_CREATE_SESSIONS_TABLE)

    @asynccontextmanager
    async def open(self) -> AsyncIterator[aiosqlite.Connection]:
        """Open a configured local connection with transaction handling."""

        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(self._database_path)
        connection.row_factory = aiosqlite.Row
        try:
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.execute("PRAGMA busy_timeout = 5000")
            yield connection
            await connection.commit()
        except BaseException:
            await connection.rollback()
            raise
        finally:
            await connection.close()


class SQLiteSessionMetadataStore:
    """Persist session metadata without controlling workflow transitions."""

    def __init__(self, database: LocalSQLiteDatabase) -> None:
        self._database = database

    async def create_session(self, session: SessionMetadata) -> SessionMetadata:
        """Insert one session record and reject duplicate identifiers."""

        try:
            async with self._database.open() as connection:
                await connection.execute(
                    """
                    INSERT INTO sessions (session_id, user_id, created_at, status)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        str(session.session_id),
                        str(session.user_id),
                        session.created_at.isoformat(),
                        session.status.value,
                    ),
                )
        except aiosqlite.IntegrityError as error:
            raise SessionAlreadyExistsError(
                f"Session metadata already exists: {session.session_id}"
            ) from error
        return session

    async def get_session(self, session_id: UUID) -> SessionMetadata | None:
        """Retrieve one session by its exact identifier."""

        async with self._database.open() as connection:
            cursor = await connection.execute(
                """
                SELECT session_id, user_id, created_at, status
                FROM sessions
                WHERE session_id = ?
                """,
                (str(session_id),),
            )
            row = await cursor.fetchone()
        return self._session_from_row(row) if row is not None else None

    async def update_session_status(
        self,
        session_id: UUID,
        status: WorkflowStatus | str,
    ) -> SessionMetadata | None:
        """Set a validated status and return the updated record if it exists."""

        validated_status = self._validate_status(status)
        async with self._database.open() as connection:
            cursor = await connection.execute(
                """
                UPDATE sessions
                SET status = ?
                WHERE session_id = ?
                """,
                (validated_status.value, str(session_id)),
            )
            if cursor.rowcount == 0:
                return None

            cursor = await connection.execute(
                """
                SELECT session_id, user_id, created_at, status
                FROM sessions
                WHERE session_id = ?
                """,
                (str(session_id),),
            )
            row = await cursor.fetchone()

        if row is None:
            raise RuntimeError("Updated session metadata could not be retrieved")
        return self._session_from_row(row)

    @staticmethod
    def _validate_status(status: WorkflowStatus | str) -> WorkflowStatus:
        try:
            return WorkflowStatus(status)
        except ValueError as error:
            allowed = ", ".join(item.value for item in WorkflowStatus)
            raise InvalidSessionStatusError(
                f"Session status must be one of: {allowed}"
            ) from error

    @staticmethod
    def _session_from_row(row: aiosqlite.Row) -> SessionMetadata:
        return SessionMetadata(
            session_id=row["session_id"],
            user_id=row["user_id"],
            created_at=row["created_at"],
            status=row["status"],
        )
