"""Immutable local storage for operator-approved knowledge sources."""

import hashlib
import os
import tempfile
from collections.abc import AsyncIterable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import aiosqlite

from app.ai.schemas import ApprovedKnowledgePath, ApprovedKnowledgeRoot
from app.ports.local_backend import StoredKnowledgeSource
from app.storage.session_workspace import WorkspacePathError, validate_file_name
from app.storage.sqlite import LocalSQLiteDatabase

_COLUMNS = """knowledge_source_id, document_id, source_id, approved_by_user_id,
file_name, mime_type, size_bytes, sha256, created_at"""


class KnowledgeSourceConflictError(RuntimeError):
    """A stable source identifier is already bound to different content."""


class KnowledgeSourceIntegrityError(RuntimeError):
    """A curated source file no longer matches durable metadata."""


class KnowledgeSourceApprovalError(PermissionError):
    """The approving identity is not an enabled local operator."""


class LocalKnowledgeSourceStore:
    """Store curated bytes separately from the AI-owned Chroma directory."""

    def __init__(
        self,
        database: LocalSQLiteDatabase,
        knowledge_root: Path,
        *,
        max_source_bytes: int = 100 * 1024 * 1024,
    ) -> None:
        if max_source_bytes <= 0:
            raise ValueError("max_source_bytes must be positive")
        if knowledge_root.is_symlink():
            raise WorkspacePathError("knowledge root must not be a symbolic link")
        self._database = database
        self._root = knowledge_root.expanduser().resolve(strict=False)
        self._sources = self._root / "sources"
        self._chroma = self._root / "chroma"
        self._max_source_bytes = max_source_bytes
        self._prepare_roots()

    def approved_knowledge_root(self) -> ApprovedKnowledgeRoot:
        self._prepare_roots()
        return ApprovedKnowledgeRoot(path=self._chroma)

    async def save_source(
        self,
        *,
        knowledge_source_id: UUID,
        document_id: str,
        source_id: str,
        approved_by_user_id: UUID,
        file_name: str,
        mime_type: str,
        content: AsyncIterable[bytes],
    ) -> StoredKnowledgeSource:
        validate_file_name(file_name)
        if not 1 <= len(document_id) <= 200 or not 1 <= len(source_id) <= 200:
            raise ValueError("document_id and source_id must contain between 1 and 200 characters")
        if not 1 <= len(mime_type) <= 255:
            raise ValueError("mime_type must contain between 1 and 255 characters")
        self._prepare_roots()
        destination = self._contained_source(f"{knowledge_source_id}.source")
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self._sources, prefix=f".{knowledge_source_id}.", suffix=".tmp"
        )
        temporary = Path(temporary_name)
        promoted = False
        persisted = False
        try:
            stream_descriptor = descriptor
            descriptor = -1
            size, digest = await self._write(stream_descriptor, temporary, content)
            candidate = StoredKnowledgeSource(
                knowledge_source_id=knowledge_source_id,
                document_id=document_id,
                source_id=source_id,
                approved_by_user_id=approved_by_user_id,
                file_name=file_name,
                mime_type=mime_type,
                size_bytes=size,
                sha256=digest,
                created_at=datetime.now(UTC),
            )
            async with self._database.open() as connection:
                await connection.execute("BEGIN IMMEDIATE")
                await self._require_operator(connection, approved_by_user_id)
                existing = await self._find_conflict(
                    connection, knowledge_source_id, document_id, source_id
                )
                if existing is not None:
                    stored = self._from_row(existing)
                    if not self._same(stored, candidate):
                        raise KnowledgeSourceConflictError(
                            "knowledge source identifier is already in use"
                        )
                else:
                    try:
                        os.link(temporary, destination, follow_symlinks=False)
                    except FileExistsError as error:
                        raise KnowledgeSourceIntegrityError(
                            "knowledge source destination exists"
                        ) from error
                    promoted = True
                    await connection.execute(
                        f"""INSERT INTO knowledge_sources ({_COLUMNS})
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            str(candidate.knowledge_source_id),
                            candidate.document_id,
                            candidate.source_id,
                            str(candidate.approved_by_user_id),
                            candidate.file_name,
                            candidate.mime_type,
                            candidate.size_bytes,
                            candidate.sha256,
                            candidate.created_at.isoformat(),
                        ),
                    )
                    stored = candidate
            persisted = True
            self._verify(stored)
            return stored
        except BaseException:
            if promoted and not persisted:
                destination.unlink(missing_ok=True)
            raise
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)

    async def get_source(
        self, *, knowledge_source_id: UUID, approved_by_user_id: UUID
    ) -> StoredKnowledgeSource | None:
        async with self._database.open() as connection:
            cursor = await connection.execute(
                f"""SELECT {_COLUMNS} FROM knowledge_sources
                WHERE knowledge_source_id = ? AND approved_by_user_id = ?""",
                (str(knowledge_source_id), str(approved_by_user_id)),
            )
            row = await cursor.fetchone()
        return self._from_row(row) if row is not None else None

    async def resolve_approved_path(
        self, *, knowledge_source_id: UUID, approved_by_user_id: UUID
    ) -> ApprovedKnowledgePath | None:
        stored = await self.get_source(
            knowledge_source_id=knowledge_source_id, approved_by_user_id=approved_by_user_id
        )
        if stored is None:
            return None
        return ApprovedKnowledgePath(path=self._verify(stored), source_id=stored.source_id)

    def _prepare_roots(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        for path in (self._root, self._sources, self._chroma):
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise WorkspacePathError("knowledge storage paths must be local directories")
            path.mkdir(exist_ok=True)

    def _contained_source(self, name: str) -> Path:
        path = self._sources / validate_file_name(name)
        if path.is_symlink() or path.resolve(strict=False).parent != self._sources.resolve():
            raise WorkspacePathError("knowledge source path escapes its root")
        return path

    async def _write(
        self, descriptor: int, path: Path, content: AsyncIterable[bytes]
    ) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(descriptor, "wb") as target:
                async for chunk in content:
                    if not isinstance(chunk, bytes):
                        raise TypeError("knowledge source chunks must be bytes")
                    size += len(chunk)
                    if size > self._max_source_bytes:
                        raise ValueError("knowledge source exceeds configured byte limit")
                    target.write(chunk)
                    digest.update(chunk)
                if size == 0:
                    raise ValueError("knowledge source must not be empty")
                target.flush()
                os.fsync(target.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return size, digest.hexdigest()

    def _verify(self, stored: StoredKnowledgeSource) -> Path:
        path = self._contained_source(f"{stored.knowledge_source_id}.source")
        if not path.is_file() or path.stat().st_size != stored.size_bytes:
            raise KnowledgeSourceIntegrityError("knowledge source size does not match metadata")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != stored.sha256:
            raise KnowledgeSourceIntegrityError("knowledge source hash does not match metadata")
        return path

    @staticmethod
    async def _require_operator(connection: aiosqlite.Connection, user_id: UUID) -> None:
        row = await (
            await connection.execute(
                "SELECT 1 FROM identities WHERE user_id = ? AND role = 'operator' AND disabled = 0",
                (str(user_id),),
            )
        ).fetchone()
        if row is None:
            raise KnowledgeSourceApprovalError("knowledge source requires an enabled operator")

    @staticmethod
    async def _find_conflict(
        connection: aiosqlite.Connection, kid: UUID, document_id: str, source_id: str
    ) -> aiosqlite.Row | None:
        return await (
            await connection.execute(
                f"""SELECT {_COLUMNS} FROM knowledge_sources
                WHERE knowledge_source_id = ? OR document_id = ? OR source_id = ?""",
                (str(kid), document_id, source_id),
            )
        ).fetchone()

    @staticmethod
    def _from_row(row: aiosqlite.Row) -> StoredKnowledgeSource:
        return StoredKnowledgeSource(**dict(row))

    @staticmethod
    def _same(left: StoredKnowledgeSource, right: StoredKnowledgeSource) -> bool:
        return left.model_dump(exclude={"created_at"}) == right.model_dump(exclude={"created_at"})
