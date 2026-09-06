"""Focused tests for immutable curated knowledge storage."""

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.ai.schemas import SourceDocument
from app.auth.contracts import UserRole
from app.ports.local_backend import StoredIdentity, StoredKnowledgeSource
from app.storage import (
    KnowledgeSourceApprovalError,
    KnowledgeSourceConflictError,
    KnowledgeSourceIntegrityError,
    LocalKnowledgeSourceStore,
    LocalSQLiteDatabase,
)


async def _chunks(*values: bytes) -> AsyncIterator[bytes]:
    for value in values:
        yield value


async def _store(
    tmp_path: Path, *, limit: int = 1024
) -> tuple[LocalSQLiteDatabase, LocalKnowledgeSourceStore, StoredIdentity]:
    database = LocalSQLiteDatabase(tmp_path / "state" / "workbench.db")
    await database.initialize()
    operator = StoredIdentity(
        user_id=uuid4(),
        username="operator",
        display_name="Operator",
        role=UserRole.OPERATOR,
        password_hash="hash",
    )
    async with database.open() as connection:
        await connection.execute(
            """INSERT INTO identities
            (user_id, username, display_name, role, password_hash, disabled)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (
                str(operator.user_id),
                operator.username,
                operator.display_name,
                operator.role.value,
                operator.password_hash,
                0,
            ),
        )
    return (
        database,
        LocalKnowledgeSourceStore(
            database, tmp_path / "state" / "knowledge", max_source_bytes=limit
        ),
        operator,
    )


@pytest.mark.asyncio
async def test_source_round_trip_restart_and_ai_contract(tmp_path: Path) -> None:
    database, store, operator = await _store(tmp_path)
    source_id = uuid4()
    saved = await store.save_source(
        knowledge_source_id=source_id,
        document_id="sop-v1",
        source_id="source-v1",
        approved_by_user_id=operator.user_id,
        file_name="pump-sop.md",
        mime_type="text/markdown",
        content=_chunks(b"# Pump SOP\n", b"Isolate power."),
    )
    restarted = LocalKnowledgeSourceStore(database, tmp_path / "state" / "knowledge")
    approved = await restarted.resolve_approved_path(
        knowledge_source_id=source_id, approved_by_user_id=operator.user_id
    )
    assert approved is not None
    assert approved.path.name == f"{source_id}.source"
    assert (
        SourceDocument(
            document_id=saved.document_id,
            document_name=saved.file_name,
            mime_type=saved.mime_type,
            approved_path=approved,
        ).effective_source_id
        == saved.source_id
    )
    assert restarted.approved_knowledge_root().path == tmp_path / "state" / "knowledge" / "chroma"


@pytest.mark.asyncio
async def test_source_is_bounded_idempotent_and_integrity_checked(tmp_path: Path) -> None:
    _, store, operator = await _store(tmp_path, limit=8)
    source_id = uuid4()

    async def save(
        value: bytes,
        *,
        knowledge_source_id: UUID = source_id,
        document_id: str = "doc",
        stable_source_id: str = "source",
    ) -> StoredKnowledgeSource:
        return await store.save_source(
            knowledge_source_id=knowledge_source_id,
            document_id=document_id,
            source_id=stable_source_id,
            approved_by_user_id=operator.user_id,
            file_name="safe.txt",
            mime_type="text/plain",
            content=_chunks(value),
        )

    first = await save(b"content")
    assert await save(b"content") == first
    with pytest.raises(KnowledgeSourceConflictError):
        await save(b"changed")
    with pytest.raises(ValueError, match="byte limit"):
        await save(
            b"123456789",
            knowledge_source_id=uuid4(),
            document_id="large",
            stable_source_id="large",
        )
    approved = await store.resolve_approved_path(
        knowledge_source_id=source_id, approved_by_user_id=operator.user_id
    )
    assert approved is not None
    approved.path.write_bytes(b"tampered")
    with pytest.raises(KnowledgeSourceIntegrityError):
        await store.resolve_approved_path(
            knowledge_source_id=source_id, approved_by_user_id=operator.user_id
        )


@pytest.mark.asyncio
async def test_source_requires_enabled_operator_and_safe_name(tmp_path: Path) -> None:
    database, store, _ = await _store(tmp_path)
    employee = StoredIdentity(
        user_id=uuid4(),
        username="employee",
        display_name="Employee",
        role=UserRole.EMPLOYEE,
        password_hash="hash",
    )
    async with database.open() as connection:
        await connection.execute(
            """INSERT INTO identities
            (user_id, username, display_name, role, password_hash, disabled)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (
                str(employee.user_id),
                employee.username,
                employee.display_name,
                employee.role.value,
                employee.password_hash,
                0,
            ),
        )
    with pytest.raises(KnowledgeSourceApprovalError):
        await store.save_source(
            knowledge_source_id=uuid4(),
            document_id="doc",
            source_id="source",
            approved_by_user_id=employee.user_id,
            file_name="safe.txt",
            mime_type="text/plain",
            content=_chunks(b"content"),
        )
    with pytest.raises(ValueError):
        await store.save_source(
            knowledge_source_id=uuid4(),
            document_id="doc2",
            source_id="source2",
            approved_by_user_id=employee.user_id,
            file_name="../unsafe.txt",
            mime_type="text/plain",
            content=_chunks(b"content"),
        )
