"""Atomic workflow admission, checkpoints, approval, and recovery tests."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.ai.evaluation.samples import sample_grounded_draft
from app.ports.local_backend import (
    StoredExtractionFindings,
    StoredRetrievedEvidence,
    WorkflowAdmissionStatus,
    WorkflowMessage,
    WorkflowRunAdmissionRequest,
)
from app.storage import LocalSQLiteDatabase, SQLiteDraftStore, SQLiteWorkflowStore
from app.storage.sqlite import (
    _NONTERMINAL_RUN_STATUSES,
    WorkflowAdmissionConflictError,
    WorkflowApprovalConflictError,
)
from app.tools.contracts import ArtifactFormat, DocumentExportArguments, ToolName, ValidatedToolCall
from app.tools.registry import argument_hash
from app.workflow.contracts import (
    Approval,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowSession,
    WorkflowStage,
    WorkflowType,
)

NOW = datetime(2026, 9, 7, 10, tzinfo=UTC)


def session() -> WorkflowSession:
    return WorkflowSession(
        session_id=uuid4(),
        owner_user_id=uuid4(),
        workflow_type=WorkflowType.INSPECTION_ANALYSIS,
        title="Inspection",
        created_at=NOW,
        updated_at=NOW,
    )


def admission(
    item: WorkflowSession, client_id: UUID, content: str = "Analyze"
) -> WorkflowRunAdmissionRequest:
    run = WorkflowRun(
        workflow_run_id=uuid4(),
        session_id=item.session_id,
        owner_user_id=item.owner_user_id,
        workflow_type=item.workflow_type,
        stage=WorkflowStage.COLLECTING_INPUTS,
        stage_version=0,
        status=WorkflowRunStatus.QUEUED,
        created_at=NOW,
        updated_at=NOW,
    )
    message = WorkflowMessage(
        message_id=uuid4(),
        session_id=item.session_id,
        author_user_id=item.owner_user_id,
        role="user",
        content=content,
        created_at=NOW,
        client_message_id=client_id,
    )
    return WorkflowRunAdmissionRequest(run=run, message=message)


@pytest.mark.asyncio
async def test_identical_admission_race_is_one_atomic_result(tmp_path: Path) -> None:
    database = LocalSQLiteDatabase(tmp_path / "workbench.db")
    await database.initialize()
    store = SQLiteWorkflowStore(database)
    item = session()
    await store.create_session(item)
    client_id = uuid4()
    left, right = await asyncio.gather(
        SQLiteWorkflowStore(database).admit_run(admission(item, client_id)),
        SQLiteWorkflowStore(database).admit_run(admission(item, client_id)),
    )
    assert {left.status, right.status} == {
        WorkflowAdmissionStatus.CREATED,
        WorkflowAdmissionStatus.REPLAYED,
    }
    assert left.run.workflow_run_id == right.run.workflow_run_id
    async with database.open() as connection:
        counts = []
        for table in (
            "workflow_runs",
            "workflow_messages",
            "workflow_run_admissions",
            "activity_events",
        ):
            row = await (await connection.execute(f"SELECT count(*) n FROM {table}")).fetchone()
            assert row is not None
            counts.append(row["n"])
    assert counts == [1, 1, 1, 1]


@pytest.mark.asyncio
async def test_competing_admissions_allow_one_nonterminal_run(tmp_path: Path) -> None:
    database = LocalSQLiteDatabase(tmp_path / "workbench.db")
    await database.initialize()
    item = session()
    await SQLiteWorkflowStore(database).create_session(item)
    outcomes = await asyncio.gather(
        SQLiteWorkflowStore(database).admit_run(admission(item, uuid4(), "First")),
        SQLiteWorkflowStore(database).admit_run(admission(item, uuid4(), "Second")),
        return_exceptions=True,
    )
    assert sum(not isinstance(value, Exception) for value in outcomes) == 1
    assert sum(isinstance(value, WorkflowAdmissionConflictError) for value in outcomes) == 1


def test_nonterminal_index_matches_domain_partition() -> None:
    terminal = {
        WorkflowRunStatus.COMPLETED,
        WorkflowRunStatus.FAILED,
        WorkflowRunStatus.APPROVAL_REJECTED,
    }
    assert set(_NONTERMINAL_RUN_STATUSES) == {
        status.value for status in WorkflowRunStatus if status not in terminal
    }


@pytest.mark.asyncio
async def test_checkpoints_retry_and_restart_strictly(tmp_path: Path) -> None:
    database = LocalSQLiteDatabase(tmp_path / "workbench.db")
    await database.initialize()
    store = SQLiteWorkflowStore(database)
    item = session()
    await store.create_session(item)
    admitted = await store.admit_run(admission(item, uuid4()))
    draft = sample_grounded_draft()
    findings = StoredExtractionFindings(
        session_id=item.session_id,
        workflow_run_id=admitted.run.workflow_run_id,
        owner_user_id=item.owner_user_id,
        source_upload_ids=(),
        findings=draft.findings,
        created_at=NOW,
    )
    evidence = StoredRetrievedEvidence(
        session_id=item.session_id,
        workflow_run_id=admitted.run.workflow_run_id,
        owner_user_id=item.owner_user_id,
        evidence=(),
        created_at=NOW,
    )
    await store.save_findings(findings)
    await store.save_findings(findings)
    await store.save_evidence(evidence)
    await store.save_evidence(evidence)
    restarted = SQLiteWorkflowStore(LocalSQLiteDatabase(database.database_path))
    assert (
        await restarted.get_findings(
            session_id=item.session_id,
            workflow_run_id=admitted.run.workflow_run_id,
            owner_user_id=item.owner_user_id,
        )
        == findings
    )
    assert (
        await restarted.get_evidence(
            session_id=item.session_id,
            workflow_run_id=admitted.run.workflow_run_id,
            owner_user_id=item.owner_user_id,
        )
        == evidence
    )


@pytest.mark.asyncio
async def test_admission_event_failure_rolls_back_everything(tmp_path: Path) -> None:
    database = LocalSQLiteDatabase(tmp_path / "workbench.db")
    await database.initialize()
    store = SQLiteWorkflowStore(database)
    item = session()
    await store.create_session(item)
    async with database.open() as connection:
        await connection.execute(
            """CREATE TRIGGER fail_accepted BEFORE INSERT ON activity_events
            WHEN NEW.event_type = 'message.accepted'
            BEGIN SELECT RAISE(ABORT, 'fault'); END"""
        )
    with pytest.raises(WorkflowAdmissionConflictError):
        await store.admit_run(admission(item, uuid4()))
    async with database.open() as connection:
        for table in ("workflow_runs", "workflow_messages", "workflow_run_admissions"):
            row = await (await connection.execute(f"SELECT count(*) n FROM {table}")).fetchone()
            assert row is not None and row["n"] == 0


@pytest.mark.asyncio
async def test_pending_approval_race_is_one_atomic_result(tmp_path: Path) -> None:
    database = LocalSQLiteDatabase(tmp_path / "workbench.db")
    await database.initialize()
    store = SQLiteWorkflowStore(database)
    item = session()
    await store.create_session(item)
    run = WorkflowRun(
        workflow_run_id=uuid4(),
        session_id=item.session_id,
        owner_user_id=item.owner_user_id,
        workflow_type=item.workflow_type,
        stage=WorkflowStage.VALIDATING,
        stage_version=4,
        status=WorkflowRunStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )
    await store.create_run(run)
    durable = await SQLiteDraftStore(database).save(
        workflow_run=run, draft=sample_grounded_draft(), created_at=NOW
    )
    arguments = DocumentExportArguments(draft_id=durable.draft_id, formats=(ArtifactFormat.DOCX,))
    output = ValidatedToolCall(tool_name=ToolName.REQUEST_DOCUMENT_EXPORT, arguments=arguments)
    approval = Approval(
        approval_id=uuid4(),
        session_id=item.session_id,
        workflow_run_id=run.workflow_run_id,
        owner_user_id=item.owner_user_id,
        workflow_type=item.workflow_type,
        stage=WorkflowStage.AWAITING_APPROVAL,
        stage_version=5,
        tool_name=output.tool_name.value,
        normalized_arguments=arguments.model_dump(mode="json", by_alias=True),
        arguments_hash=argument_hash(arguments),
        requested_at=NOW + timedelta(seconds=1),
    )
    async with database.open() as connection:
        await connection.execute(
            """CREATE TRIGGER fail_required BEFORE INSERT ON activity_events
            WHEN NEW.event_type = 'approval.required'
            BEGIN SELECT RAISE(ABORT, 'fault'); END"""
        )
    with pytest.raises(WorkflowApprovalConflictError):
        await store.prepare_pending_approval(run=run, approval=approval, output=output)
    async with database.open() as connection:
        for table in ("workflow_outputs", "approvals"):
            row = await (await connection.execute(f"SELECT count(*) n FROM {table}")).fetchone()
            assert row is not None and row["n"] == 0
        row = await (
            await connection.execute(
                "SELECT stage, status FROM workflow_runs WHERE workflow_run_id = ?",
                (str(run.workflow_run_id),),
            )
        ).fetchone()
        assert row is not None
        assert (row["stage"], row["status"]) == ("validating", "active")
        await connection.execute("DROP TRIGGER fail_required")
    results = await asyncio.gather(
        SQLiteWorkflowStore(database).prepare_pending_approval(
            run=run, approval=approval, output=output
        ),
        SQLiteWorkflowStore(database).prepare_pending_approval(
            run=run,
            approval=approval.model_copy(
                update={
                    "approval_id": uuid4(),
                    "requested_at": approval.requested_at + timedelta(seconds=1),
                }
            ),
            output=output,
        ),
    )
    assert {result.created_now for result in results} == {True, False}
    assert results[0].approval.approval_id == results[1].approval.approval_id
    async with database.open() as connection:
        for table in ("workflow_outputs", "approvals"):
            row = await (await connection.execute(f"SELECT count(*) n FROM {table}")).fetchone()
            assert row is not None and row["n"] == 1
        row = await (
            await connection.execute(
                "SELECT count(*) n FROM activity_events WHERE event_type='approval.required'"
            )
        ).fetchone()
        assert row is not None and row["n"] == 1


@pytest.mark.asyncio
async def test_recovery_marks_only_expired_active_run(tmp_path: Path) -> None:
    database = LocalSQLiteDatabase(tmp_path / "workbench.db")
    await database.initialize()
    store = SQLiteWorkflowStore(database)
    active_session, waiting_session = session(), session()
    await store.create_session(active_session)
    await store.create_session(waiting_session)
    active = WorkflowRun(
        workflow_run_id=uuid4(),
        session_id=active_session.session_id,
        owner_user_id=active_session.owner_user_id,
        workflow_type=active_session.workflow_type,
        stage=WorkflowStage.EXTRACTING,
        stage_version=1,
        status=WorkflowRunStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
        execution_lease_expires_at=NOW + timedelta(seconds=1),
    )
    waiting = WorkflowRun(
        workflow_run_id=uuid4(),
        session_id=waiting_session.session_id,
        owner_user_id=waiting_session.owner_user_id,
        workflow_type=waiting_session.workflow_type,
        stage=WorkflowStage.AWAITING_APPROVAL,
        stage_version=2,
        status=WorkflowRunStatus.WAITING_FOR_APPROVAL,
        created_at=NOW,
        updated_at=NOW,
    )
    await store.create_run(active)
    await store.create_run(waiting)
    interrupted_at = NOW + timedelta(seconds=3)
    first = await store.mark_stale_runs_interrupted(
        stale_before=NOW + timedelta(seconds=2), interrupted_at=interrupted_at
    )
    second = await SQLiteWorkflowStore(database).mark_stale_runs_interrupted(
        stale_before=NOW + timedelta(seconds=2), interrupted_at=interrupted_at
    )
    assert len(first) == 1 and second == []
    unfinished = await store.list_unfinished_runs()
    assert len(unfinished) == 2
    marked = next(value for value in unfinished if value.workflow_run_id == active.workflow_run_id)
    assert marked.retryable and marked.interrupted_at == interrupted_at


@pytest.mark.asyncio
async def test_startup_recovers_expired_active_runs(tmp_path: Path) -> None:
    """Regression: startup must mark stale active runs retryable, not just initialize schema."""

    database = LocalSQLiteDatabase(tmp_path / "workbench.db")
    await database.initialize()
    store = SQLiteWorkflowStore(database)

    item = session()
    await store.create_session(item)

    expired_run = WorkflowRun(
        workflow_run_id=uuid4(),
        session_id=item.session_id,
        owner_user_id=item.owner_user_id,
        workflow_type=item.workflow_type,
        stage=WorkflowStage.EXTRACTING,
        stage_version=1,
        status=WorkflowRunStatus.ACTIVE,
        sandbox_attempts=0,
        created_at=NOW,
        updated_at=NOW,
        execution_lease_expires_at=NOW - timedelta(seconds=10),
    )
    await store.create_run(expired_run)

    fresh = LocalSQLiteDatabase(tmp_path / "workbench.db")
    await fresh.initialize()
    fresh_store = SQLiteWorkflowStore(fresh)

    now = NOW + timedelta(seconds=5)
    interrupted = await fresh_store.mark_stale_runs_interrupted(
        stale_before=now, interrupted_at=now,
    )

    assert len(interrupted) == 1
    assert interrupted[0].workflow_run_id == expired_run.workflow_run_id
    assert interrupted[0].retryable is True
