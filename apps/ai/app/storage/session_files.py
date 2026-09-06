"""Durable metadata and contained filesystem storage for session uploads."""

import hashlib
import os
import tempfile
from collections.abc import AsyncIterable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import aiosqlite

from app.ai.schemas import ApprovedPath
from app.ports.backend2 import StoredUpload
from app.storage.session_workspace import (
    LocalSessionWorkspaceStore,
    WorkspaceArea,
    WorkspacePathError,
    validate_file_name,
)
from app.storage.sqlite import LocalSQLiteDatabase
from app.workflow.contracts import WorkflowSession, WorkflowStage

_MEBIBYTE = 1024 * 1024
_UPLOAD_COLUMNS = """workflow_uploads.upload_id, workflow_uploads.session_id,
workflow_uploads.source_id, workflow_uploads.stored_file_name,
workflow_uploads.file_name, workflow_uploads.mime_type, workflow_uploads.size_bytes,
workflow_uploads.sha256, workflow_uploads.created_at"""


class UploadAlreadyExistsError(RuntimeError):
    """Raised when an upload or source ID is already bound to different content."""


class SessionFileContextMismatchError(PermissionError):
    """Raised when upload mutation does not match an owned workflow session."""


class UploadSessionStateConflictError(SessionFileContextMismatchError):
    """The workflow session changed before an upload could be committed."""


class UploadIntegrityError(RuntimeError):
    """Raised when durable upload metadata no longer matches its contained file."""


class SessionUploadCleanupError(RuntimeError):
    """Raised after cleanup retains metadata for files that could not be removed."""

    def __init__(self, *, cleaned_count: int, failed_count: int) -> None:
        super().__init__("one or more session uploads could not be safely removed")
        self.cleaned_count = cleaned_count
        self.failed_count = failed_count


class SQLiteSessionFileStore:
    """Stream session uploads locally and expose only owner-approved exact paths."""

    def __init__(
        self,
        database: LocalSQLiteDatabase,
        workspaces: LocalSessionWorkspaceStore,
        *,
        max_upload_bytes: int = 100 * _MEBIBYTE,
    ) -> None:
        if (
            isinstance(max_upload_bytes, bool)
            or not isinstance(max_upload_bytes, int)
            or max_upload_bytes <= 0
        ):
            raise ValueError("max_upload_bytes must be a positive integer")
        self._database = database
        self._workspaces = workspaces
        self._max_upload_bytes = max_upload_bytes

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
        """Persist one bounded stream without trusting caller-controlled paths."""

        validate_file_name(file_name)
        if not 1 <= len(mime_type) <= 255:
            raise ValueError("mime_type must contain between 1 and 255 characters")
        await self._require_session(session)

        workspace = self._workspaces.create_session_workspace(str(session.session_id))
        descriptor, temporary_name = tempfile.mkstemp(
            dir=workspace.uploads,
            prefix=f".{upload_id}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        destination: Path | None = None
        promoted = False
        persisted = False
        try:
            stream_descriptor = descriptor
            descriptor = -1
            size_bytes, sha256 = await self._write_stream(
                stream_descriptor,
                temporary_path,
                content,
            )
            descriptor = -1
            candidate = StoredUpload(
                upload_id=upload_id,
                session_id=session.session_id,
                source_id=source_id,
                file_name=file_name,
                mime_type=mime_type,
                size_bytes=size_bytes,
                sha256=sha256,
                created_at=datetime.now(UTC),
            )
            destination = self._upload_path(session.session_id, f"{upload_id}.upload")

            stored: StoredUpload | None = None
            async with self._database.open() as connection:
                await connection.execute("BEGIN IMMEDIATE")
                await self._require_session_connection(connection, session)
                existing = await self._find_conflicting_upload(
                    connection,
                    upload_id=upload_id,
                    source_id=source_id,
                )
                if existing is not None:
                    if not self._same_upload(existing, candidate):
                        raise UploadAlreadyExistsError(
                            "upload or source identifier is already in use"
                        )
                    stored = existing
                else:
                    try:
                        os.link(temporary_path, destination, follow_symlinks=False)
                    except FileExistsError as error:
                        raise UploadIntegrityError(
                            "controlled upload destination already exists"
                        ) from error
                    promoted = True
                    await connection.execute(
                        """INSERT INTO workflow_uploads
                        (upload_id, session_id, source_id, stored_file_name, file_name,
                         mime_type, size_bytes, sha256, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            str(candidate.upload_id),
                            str(candidate.session_id),
                            str(candidate.source_id),
                            destination.name,
                            candidate.file_name,
                            candidate.mime_type,
                            candidate.size_bytes,
                            candidate.sha256,
                            candidate.created_at.isoformat(),
                        ),
                    )
                    stored = candidate
            persisted = True
            if stored is None:  # pragma: no cover - every branch assigns it
                raise RuntimeError("upload persistence produced no record")
            self._verify_upload_file(stored)
            return stored
        except BaseException:
            if promoted and not persisted and destination is not None:
                destination.unlink(missing_ok=True)
            raise
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary_path.unlink(missing_ok=True)

    async def get_upload(
        self,
        *,
        upload_id: UUID,
        session_id: UUID,
        owner_user_id: UUID,
    ) -> StoredUpload | None:
        """Return durable metadata without revealing foreign ownership."""

        async with self._database.open() as connection:
            cursor = await connection.execute(
                f"""SELECT {_UPLOAD_COLUMNS}
                FROM workflow_uploads
                JOIN workflow_sessions USING (session_id)
                WHERE upload_id = ? AND session_id = ? AND owner_user_id = ?""",
                (str(upload_id), str(session_id), str(owner_user_id)),
            )
            row = await cursor.fetchone()
        return self._upload_from_row(row) if row is not None else None

    async def resolve_approved_path(
        self,
        *,
        upload_id: UUID,
        session_id: UUID,
        owner_user_id: UUID,
    ) -> ApprovedPath | None:
        """Resolve one owned upload after checking its current file integrity."""

        async with self._database.open() as connection:
            cursor = await connection.execute(
                f"""SELECT {_UPLOAD_COLUMNS} FROM workflow_uploads
                JOIN workflow_sessions USING (session_id)
                WHERE upload_id = ? AND session_id = ? AND owner_user_id = ?""",
                (str(upload_id), str(session_id), str(owner_user_id)),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        upload = self._upload_from_row(row)
        path = self._verify_upload_file(upload, row["stored_file_name"])
        return ApprovedPath(
            path=path,
            source_id=str(upload.source_id),
            session_id=str(upload.session_id),
        )

    async def cleanup_session_uploads(
        self,
        *,
        session_id: UUID,
        owner_user_id: UUID,
    ) -> int:
        """Delete owned uploads while retaining metadata for unsafe failures."""

        async with self._database.open() as connection:
            await self._require_owned_session(
                connection,
                session_id=session_id,
                owner_user_id=owner_user_id,
            )
            cursor = await connection.execute(
                f"""SELECT {_UPLOAD_COLUMNS} FROM workflow_uploads
                WHERE session_id = ? ORDER BY created_at, upload_id""",
                (str(session_id),),
            )
            rows = await cursor.fetchall()

        removable: list[UUID] = []
        failed_count = 0
        for row in rows:
            upload = self._upload_from_row(row)
            try:
                path = self._upload_path(session_id, row["stored_file_name"])
                if path.exists():
                    if not path.is_file():
                        raise UploadIntegrityError(
                            "controlled upload is not a regular file"
                        )
                    path.unlink()
                removable.append(upload.upload_id)
            except FileNotFoundError:
                removable.append(upload.upload_id)
            except (OSError, UploadIntegrityError, WorkspacePathError):
                failed_count += 1

        cleaned_count = 0
        if removable:
            async with self._database.open() as connection:
                await self._require_owned_session(
                    connection,
                    session_id=session_id,
                    owner_user_id=owner_user_id,
                )
                for upload_id in removable:
                    cursor = await connection.execute(
                        """DELETE FROM workflow_uploads
                        WHERE upload_id = ? AND session_id = ?
                          AND EXISTS (
                              SELECT 1 FROM workflow_sessions
                              WHERE workflow_sessions.session_id = workflow_uploads.session_id
                                AND workflow_sessions.owner_user_id = ?
                          )""",
                        (str(upload_id), str(session_id), str(owner_user_id)),
                    )
                    cleaned_count += cursor.rowcount

        if failed_count:
            raise SessionUploadCleanupError(
                cleaned_count=cleaned_count,
                failed_count=failed_count,
            )
        return cleaned_count

    async def _require_session(self, session: WorkflowSession) -> None:
        async with self._database.open() as connection:
            await self._require_session_connection(connection, session)

    @staticmethod
    async def _require_session_connection(
        connection: aiosqlite.Connection,
        session: WorkflowSession,
    ) -> None:
        cursor = await connection.execute(
            """SELECT 1 FROM workflow_sessions
            WHERE session_id = ? AND owner_user_id = ? AND workflow_type = ? AND stage = ?""",
            (
                str(session.session_id),
                str(session.owner_user_id),
                session.workflow_type.value,
                WorkflowStage.COLLECTING_INPUTS.value,
            ),
        )
        if await cursor.fetchone() is None:
            raise UploadSessionStateConflictError(
                "workflow session no longer accepts this upload"
            )

    @staticmethod
    async def _require_owned_session(
        connection: aiosqlite.Connection,
        *,
        session_id: UUID,
        owner_user_id: UUID,
    ) -> None:
        cursor = await connection.execute(
            """SELECT 1 FROM workflow_sessions
            WHERE session_id = ? AND owner_user_id = ?""",
            (str(session_id), str(owner_user_id)),
        )
        if await cursor.fetchone() is None:
            raise SessionFileContextMismatchError(
                "upload does not match an existing owned workflow session"
            )

    async def _write_stream(
        self,
        descriptor: int,
        temporary_path: Path,
        content: AsyncIterable[bytes],
    ) -> tuple[int, str]:
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            with os.fdopen(descriptor, "wb") as temporary_file:
                async for chunk in content:
                    if not isinstance(chunk, bytes):
                        raise TypeError("upload content chunks must be bytes")
                    if size_bytes + len(chunk) > self._max_upload_bytes:
                        raise ValueError("upload exceeds the configured byte limit")
                    temporary_file.write(chunk)
                    digest.update(chunk)
                    size_bytes += len(chunk)
                if size_bytes == 0:
                    raise ValueError("upload content must not be empty")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
        return size_bytes, digest.hexdigest()

    def _verify_upload_file(
        self, upload: StoredUpload, stored_file_name: str | None = None
    ) -> Path:
        try:
            path = self._upload_path(
                upload.session_id, stored_file_name or f"{upload.upload_id}.upload"
            )
            if not path.is_file():
                raise UploadIntegrityError("controlled upload file is missing")
            stat = path.stat()
            if stat.st_size != upload.size_bytes:
                raise UploadIntegrityError("controlled upload size does not match metadata")
            if self._sha256_file(path) != upload.sha256:
                raise UploadIntegrityError("controlled upload hash does not match metadata")
            return path
        except FileNotFoundError as error:
            raise UploadIntegrityError("controlled upload file is missing") from error
        except WorkspacePathError as error:
            raise UploadIntegrityError("controlled upload path is unsafe") from error

    def _upload_path(self, session_id: UUID, stored_file_name: str) -> Path:
        return self._workspaces.file_path(
            str(session_id),
            WorkspaceArea.UPLOADS,
            stored_file_name,
        )

    @staticmethod
    async def _find_conflicting_upload(
        connection: aiosqlite.Connection,
        *,
        upload_id: UUID,
        source_id: UUID,
    ) -> StoredUpload | None:
        cursor = await connection.execute(
            f"""SELECT {_UPLOAD_COLUMNS} FROM workflow_uploads
            WHERE upload_id = ? OR source_id = ?""",
            (str(upload_id), str(source_id)),
        )
        row = await cursor.fetchone()
        return SQLiteSessionFileStore._upload_from_row(row) if row is not None else None

    @staticmethod
    def _same_upload(existing: StoredUpload, candidate: StoredUpload) -> bool:
        return (
            existing.upload_id == candidate.upload_id
            and existing.session_id == candidate.session_id
            and existing.source_id == candidate.source_id
            and existing.file_name == candidate.file_name
            and existing.mime_type == candidate.mime_type
            and existing.size_bytes == candidate.size_bytes
            and existing.sha256 == candidate.sha256
        )

    @staticmethod
    def _upload_from_row(row: aiosqlite.Row) -> StoredUpload:
        return StoredUpload(
            upload_id=UUID(row["upload_id"]),
            session_id=UUID(row["session_id"]),
            source_id=UUID(row["source_id"]),
            file_name=row["file_name"],
            mime_type=row["mime_type"],
            size_bytes=row["size_bytes"],
            sha256=row["sha256"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


# Compatibility name retained for the feature branch's focused storage callers.
LocalSessionFileStore = SQLiteSessionFileStore
