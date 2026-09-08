from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.storage import LocalSQLiteDatabase, SQLiteWorkflowStore
from app.workflow.contracts import WorkflowSession, WorkflowStage, WorkflowStatus, WorkflowType


@pytest.mark.asyncio
async def test_existing_local_chat_database_migrates_without_losing_sessions(tmp_path: Path) -> None:
    database = LocalSQLiteDatabase(tmp_path / "existing.db")
    async with database.open() as connection:
        await connection.execute("""CREATE TABLE workflow_sessions (
            session_id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL,
            workflow_type TEXT CHECK(workflow_type IN ('inspectionAnalysis','codeRepair','localConversation')),
            title TEXT, stage TEXT, status TEXT, created_at TEXT, updated_at TEXT,
            client_session_id TEXT)""")
        now = datetime.now(UTC)
        existing_id, owner = uuid4(), uuid4()
        await connection.execute("INSERT INTO workflow_sessions VALUES(?,?,?,?,?,?,?,?,?)",
            (str(existing_id), str(owner), "localConversation", "Existing chat", "collectingInputs",
             "active", now.isoformat(), now.isoformat(), None))
    await database.initialize()
    await database.initialize()
    store = SQLiteWorkflowStore(database)
    existing = await store.get_session(existing_id, owner)
    assert existing.title == "Existing chat"
    await store.create_session(WorkflowSession(session_id=uuid4(), owner_user_id=owner,
        workflow_type=WorkflowType.PDF_DOCUMENT, title="PDF", stage=WorkflowStage.COLLECTING_INPUTS,
        status=WorkflowStatus.ACTIVE, created_at=now, updated_at=now))
    assert len(await store.list_sessions(owner)) == 2
