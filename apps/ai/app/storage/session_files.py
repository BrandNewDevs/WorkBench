"""Durable, contained storage for explicitly selected workflow uploads."""

import hashlib
import os
import tempfile
from collections.abc import AsyncIterable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.ai.schemas import ApprovedPath
from app.ports.backend2 import StoredUpload
from app.storage.session_workspace import LocalSessionWorkspaceStore
from app.storage.sqlite import LocalSQLiteDatabase
from app.workflow.contracts import WorkflowSession, WorkflowStage


class UploadSessionStateConflictError(RuntimeError):
    """The workflow session changed before an upload could be committed."""


class SQLiteSessionFileStore:
    """Write validated uploads atomically and retain only safe metadata in SQLite."""

    def __init__(
        self, database: LocalSQLiteDatabase, workspace_store: LocalSessionWorkspaceStore
    ) -> None:
        self._database = database
        self._workspace_store = workspace_store

    async def save_upload(
        self,
        *,
        session: WorkflowSession,
        upload_id: UUID,
        source_id: UUID,
        file_name: str,
        mime_type: str,
        content: AsyncIterable[bytes],
    ) -> StoredUpload:
        """Stream one prevalidated file through a temporary file before final placement."""

        suffix = Path(file_name).suffix.lower()
        stored_file_name = f"{upload_id}{suffix}"
        workspace = self._workspace_store.create_session_workspace(str(session.session_id))
        destination = workspace.uploads / stored_file_name
        if destination.exists() or destination.is_symlink():
            raise RuntimeError("upload destination already exists")

        descriptor, temporary_name = tempfile.mkstemp(
            dir=workspace.uploads, prefix=f".{upload_id}.", suffix=".tmp"
        )
        temporary_path = Path(temporary_name)
        digest = hashlib.sha256()
        size_bytes = 0
        placed = False
        try:
            with os.fdopen(descriptor, "wb") as temporary_file:
                async for chunk in content:
                    if not chunk:
                        continue
                    temporary_file.write(chunk)
                    digest.update(chunk)
                    size_bytes += len(chunk)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            if size_bytes == 0:
                raise ValueError("upload content must not be empty")

            os.replace(temporary_path, destination)
            placed = True
            created_at = datetime.now(UTC)
            stored = StoredUpload(
                upload_id=upload_id,
                session_id=session.session_id,
                source_id=source_id,
                file_name=file_name,
                mime_type=mime_type,
                size_bytes=size_bytes,
                sha256=digest.hexdigest(),
                created_at=created_at,
            )
            async with self._database.open() as connection:
                cursor = await connection.execute(
                    """INSERT INTO workflow_uploads
                    (upload_id, session_id, source_id, stored_file_name, file_name, mime_type,
                    size_bytes, sha256, created_at)
                    SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?
                    FROM workflow_sessions
                    WHERE session_id = ?
                    AND owner_user_id = ?
                    AND workflow_type = ?
                    AND stage = ?""",
                    (
                        str(stored.upload_id),
                        str(stored.session_id),
                        str(stored.source_id),
                        stored_file_name,
                        stored.file_name,
                        stored.mime_type,
                        stored.size_bytes,
                        stored.sha256,
                        stored.created_at.isoformat(),
                        str(session.session_id),
                        str(session.owner_user_id),
                        session.workflow_type.value,
                        WorkflowStage.COLLECTING_INPUTS.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise UploadSessionStateConflictError(
                        "workflow session no longer accepts this upload"
                    )
            return stored
        except BaseException:
            if placed:
                destination.unlink(missing_ok=True)
            else:
                temporary_path.unlink(missing_ok=True)
            raise

    async def resolve_approved_path(
        self, upload_id: UUID, session_id: UUID
    ) -> ApprovedPath | None:
        """Return only the exact locally stored upload associated with this session."""

        async with self._database.open() as connection:
            cursor = await connection.execute(
                """SELECT source_id, stored_file_name
                FROM workflow_uploads
                WHERE upload_id = ? AND session_id = ?""",
                (str(upload_id), str(session_id)),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        try:
            workspace = self._workspace_store.get_session_workspace(str(session_id))
        except (FileNotFoundError, ValueError):
            return None
        path = workspace.uploads / row["stored_file_name"]
        if path.is_symlink() or not path.is_file():
            return None
        return ApprovedPath(path=path, source_id=row["source_id"], session_id=str(session_id))
