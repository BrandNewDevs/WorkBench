"""Focused tests for local SQLite approval metadata persistence."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import aiosqlite
import pytest

from app.storage import LocalSQLiteDatabase, SQLiteApprovalStore
from app.tools.contracts import (
    DocumentExportResult,
    SandboxExecutionResult,
    ToolName,
)
from app.workflow.contracts import (
    Approval,
    ApprovalDecision,
    ApprovalExecutionClaim,
    ApprovalResolution,
    ApprovalStatus,
    ExecutionStatus,
    WorkflowStage,
    WorkflowType,
)

_REQUESTED_AT = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)
_RESOLVED_AT = datetime(2026, 9, 6, 8, 5, tzinfo=UTC)


def _approval(
    *,
    approval_id: UUID | None = None,
    session_id: UUID | None = None,
    workflow_run_id: UUID | None = None,
    owner_user_id: UUID | None = None,
    tool_name: ToolName = ToolName.RUN_SANDBOX,
) -> Approval:
    workflow_type = (
        WorkflowType.CODE_REPAIR
        if tool_name is ToolName.RUN_SANDBOX
        else WorkflowType.INSPECTION_ANALYSIS
    )
    return Approval(
        approval_id=approval_id or uuid4(),
        session_id=session_id or uuid4(),
        workflow_run_id=workflow_run_id or uuid4(),
        owner_user_id=owner_user_id or uuid4(),
        workflow_type=workflow_type,
        stage=WorkflowStage.AWAITING_APPROVAL,
        stage_version=3,
        tool_name=tool_name.value,
        normalized_arguments={"nested": {"enabled": True}, "attempt": 1},
        arguments_hash="a" * 64,
        requested_at=_REQUESTED_AT,
    )


async def _store(tmp_path: Path) -> tuple[Path, SQLiteApprovalStore]:
    database_path = tmp_path / "state" / "workbench.db"
    database = LocalSQLiteDatabase(database_path)
    await database.initialize()
    return database_path, SQLiteApprovalStore(database)


async def _resolve(
    store: SQLiteApprovalStore,
    approval: Approval,
    *,
    approval_id: UUID | None = None,
    session_id: UUID | None = None,
    workflow_run_id: UUID | None = None,
    owner_user_id: UUID | None = None,
    expected_stage: WorkflowStage | None = None,
    expected_stage_version: int | None = None,
    decision: ApprovalDecision = ApprovalDecision.APPROVED,
    resolved_at: datetime = _RESOLVED_AT,
    comment: str | None = "  Confirmed by operator  ",
) -> ApprovalResolution | None:
    return await store.resolve_pending_approval(
        approval_id=approval_id or approval.approval_id,
        session_id=session_id or approval.session_id,
        workflow_run_id=workflow_run_id or approval.workflow_run_id,
        owner_user_id=owner_user_id or approval.owner_user_id,
        expected_stage=expected_stage or approval.stage,
        expected_stage_version=(
            expected_stage_version
            if expected_stage_version is not None
            else approval.stage_version
        ),
        decision=decision,
        resolved_at=resolved_at,
        comment=comment,
    )


async def _claim(
    store: SQLiteApprovalStore,
    approval: Approval,
    *,
    approval_id: UUID | None = None,
    session_id: UUID | None = None,
    workflow_run_id: UUID | None = None,
    owner_user_id: UUID | None = None,
    workflow_type: WorkflowType | None = None,
    expected_stage: WorkflowStage | None = None,
    expected_stage_version: int | None = None,
    tool_name: str | None = None,
    arguments_hash: str | None = None,
) -> ApprovalExecutionClaim | None:
    return await store.claim_execution(
        approval_id=approval_id or approval.approval_id,
        session_id=session_id or approval.session_id,
        workflow_run_id=workflow_run_id or approval.workflow_run_id,
        owner_user_id=owner_user_id or approval.owner_user_id,
        workflow_type=workflow_type or approval.workflow_type,
        expected_stage=expected_stage or approval.stage,
        expected_stage_version=(
            expected_stage_version
            if expected_stage_version is not None
            else approval.stage_version
        ),
        tool_name=tool_name or approval.tool_name,
        arguments_hash=arguments_hash or approval.arguments_hash,
    )


@pytest.mark.asyncio
async def test_initialize_is_idempotent_and_creates_approval_table(tmp_path: Path) -> None:
    database = LocalSQLiteDatabase(tmp_path / "workbench.db")

    await database.initialize()
    await database.initialize()

    async with database.open() as connection:
        cursor = await connection.execute("PRAGMA table_info(approvals)")
        columns = {row["name"] for row in await cursor.fetchall()}

    assert columns == {
        "approval_id",
        "session_id",
        "workflow_run_id",
        "owner_user_id",
        "workflow_type",
        "stage",
        "stage_version",
        "tool_name",
        "normalized_arguments",
        "arguments_hash",
        "status",
        "requested_at",
        "resolved_at",
        "resolved_by_user_id",
        "decision",
        "comment",
        "execution_status",
        "execution_result",
    }


@pytest.mark.asyncio
async def test_pending_intent_round_trips_across_store_instances(tmp_path: Path) -> None:
    database_path, store = await _store(tmp_path)
    approval = _approval()

    assert await store.create_pending(approval) == approval
    restarted_store = SQLiteApprovalStore(LocalSQLiteDatabase(database_path))
    resolution = await _resolve(restarted_store, approval)

    assert resolution is not None
    assert resolution.resolved_now is True
    persisted = resolution.approval
    assert persisted.approval_id == approval.approval_id
    assert persisted.session_id == approval.session_id
    assert persisted.workflow_run_id == approval.workflow_run_id
    assert persisted.owner_user_id == approval.owner_user_id
    assert persisted.workflow_type is approval.workflow_type
    assert persisted.stage is approval.stage
    assert persisted.stage_version == approval.stage_version
    assert persisted.tool_name == approval.tool_name
    assert persisted.normalized_arguments == approval.normalized_arguments
    assert persisted.arguments_hash == approval.arguments_hash
    assert persisted.requested_at == approval.requested_at
    assert persisted.status is ApprovalStatus.APPROVED
    assert persisted.execution_status is ExecutionStatus.NOT_STARTED
    assert persisted.comment == "Confirmed by operator"

    async with LocalSQLiteDatabase(database_path).open() as connection:
        cursor = await connection.execute(
            "SELECT normalized_arguments FROM approvals WHERE approval_id = ?",
            (str(approval.approval_id),),
        )
        row = await cursor.fetchone()
    assert row is not None
    assert row["normalized_arguments"] == '{"attempt":1,"nested":{"enabled":true}}'


@pytest.mark.asyncio
async def test_rejected_resolution_is_idempotent_and_preserves_original_data(
    tmp_path: Path,
) -> None:
    _, store = await _store(tmp_path)
    approval = _approval()
    await store.create_pending(approval)

    first = await _resolve(
        store,
        approval,
        decision=ApprovalDecision.REJECTED,
        comment="Not authorized",
    )
    repeated = await _resolve(
        store,
        approval,
        decision=ApprovalDecision.APPROVED,
        resolved_at=_RESOLVED_AT + timedelta(minutes=10),
        comment="Changed answer",
    )

    assert first is not None and repeated is not None
    assert first.resolved_now is True
    assert repeated.resolved_now is False
    assert repeated.approval == first.approval
    assert first.approval.status is ApprovalStatus.REJECTED
    assert first.approval.decision is ApprovalDecision.REJECTED
    assert first.approval.execution_status is ExecutionStatus.NOT_APPLICABLE
    assert first.approval.resolved_at == _RESOLVED_AT
    assert first.approval.resolved_by_user_id == approval.owner_user_id
    assert first.approval.comment == "Not authorized"


@pytest.mark.asyncio
async def test_concurrent_resolution_changes_pending_approval_exactly_once(
    tmp_path: Path,
) -> None:
    _, store = await _store(tmp_path)
    approval = _approval()
    await store.create_pending(approval)

    resolutions = await asyncio.gather(
        _resolve(store, approval, comment="First request"),
        _resolve(
            store,
            approval,
            resolved_at=_RESOLVED_AT + timedelta(seconds=1),
            comment="Second request",
        ),
    )

    assert all(item is not None for item in resolutions)
    assert sorted(item.resolved_now for item in resolutions if item is not None) == [False, True]
    assert resolutions[0] is not None and resolutions[1] is not None
    assert resolutions[0].approval == resolutions[1].approval
    assert resolutions[0].approval.comment in {"First request", "Second request"}


@pytest.mark.asyncio
async def test_resolution_requires_exact_bound_identity(tmp_path: Path) -> None:
    _, store = await _store(tmp_path)
    approval = _approval()
    await store.create_pending(approval)

    assert await _resolve(store, approval, approval_id=uuid4()) is None
    assert await _resolve(store, approval, session_id=uuid4()) is None
    assert await _resolve(store, approval, workflow_run_id=uuid4()) is None
    assert await _resolve(store, approval, owner_user_id=uuid4()) is None
    assert await _resolve(store, approval, expected_stage=WorkflowStage.PLANNING) is None
    assert (
        await _resolve(
            store,
            approval,
            expected_stage_version=approval.stage_version + 1,
        )
        is None
    )

    successful = await _resolve(store, approval)
    assert successful is not None and successful.resolved_now is True


@pytest.mark.asyncio
async def test_only_approved_intent_can_be_claimed_and_retry_is_idempotent(
    tmp_path: Path,
) -> None:
    _, store = await _store(tmp_path)
    pending = _approval()
    rejected = _approval()
    approved = _approval()
    for approval in (pending, rejected, approved):
        await store.create_pending(approval)
    await _resolve(store, rejected, decision=ApprovalDecision.REJECTED)
    await _resolve(store, approved)

    assert await _claim(store, pending) is None
    assert await _claim(store, rejected) is None
    first = await _claim(store, approved)
    repeated = await _claim(store, approved)

    assert first is not None and repeated is not None
    assert first.claimed_now is True
    assert repeated.claimed_now is False
    assert first.approval.execution_status is ExecutionStatus.QUEUED
    assert repeated.approval == first.approval


@pytest.mark.asyncio
async def test_claim_requires_exact_execution_binding(tmp_path: Path) -> None:
    _, store = await _store(tmp_path)
    approval = _approval()
    await store.create_pending(approval)
    await _resolve(store, approval)

    assert await _claim(store, approval, approval_id=uuid4()) is None
    assert await _claim(store, approval, session_id=uuid4()) is None
    assert await _claim(store, approval, workflow_run_id=uuid4()) is None
    assert await _claim(store, approval, owner_user_id=uuid4()) is None
    assert (
        await _claim(store, approval, workflow_type=WorkflowType.INSPECTION_ANALYSIS)
        is None
    )
    assert await _claim(store, approval, expected_stage=WorkflowStage.PLANNING) is None
    assert (
        await _claim(
            store,
            approval,
            expected_stage_version=approval.stage_version + 1,
        )
        is None
    )
    assert (
        await _claim(
            store,
            approval,
            tool_name=ToolName.REQUEST_DOCUMENT_EXPORT.value,
        )
        is None
    )
    assert await _claim(store, approval, arguments_hash="b" * 64) is None

    claimed = await _claim(store, approval)
    assert claimed is not None and claimed.claimed_now is True


@pytest.mark.asyncio
async def test_concurrent_execution_claim_succeeds_exactly_once(tmp_path: Path) -> None:
    _, store = await _store(tmp_path)
    approval = _approval()
    await store.create_pending(approval)
    await _resolve(store, approval)

    claims = await asyncio.gather(_claim(store, approval), _claim(store, approval))

    assert all(item is not None for item in claims)
    assert sorted(item.claimed_now for item in claims if item is not None) == [False, True]


@pytest.mark.asyncio
async def test_completed_result_persists_and_is_owner_scoped(tmp_path: Path) -> None:
    database_path, store = await _store(tmp_path)
    approval = _approval(tool_name=ToolName.RUN_SANDBOX)
    await store.create_pending(approval)
    await _resolve(store, approval)
    result = SandboxExecutionResult(
        status=ExecutionStatus.COMPLETED,
        exit_code=0,
        passed=True,
    )

    assert (
        await store.record_execution_result(
            approval_id=approval.approval_id,
            result=result,
        )
        is None
    )
    await _claim(store, approval)
    persisted = await store.record_execution_result(
        approval_id=approval.approval_id,
        result=result,
    )
    restarted = SQLiteApprovalStore(LocalSQLiteDatabase(database_path))
    retrieved = await restarted.get_execution_result(
        approval_id=approval.approval_id,
        session_id=approval.session_id,
        workflow_run_id=approval.workflow_run_id,
        owner_user_id=approval.owner_user_id,
    )

    assert persisted is not None
    assert persisted.execution_status is ExecutionStatus.COMPLETED
    assert retrieved == result
    assert (
        await restarted.get_execution_result(
            approval_id=approval.approval_id,
            session_id=uuid4(),
            workflow_run_id=approval.workflow_run_id,
            owner_user_id=approval.owner_user_id,
        )
        is None
    )
    assert (
        await restarted.get_execution_result(
            approval_id=approval.approval_id,
            session_id=approval.session_id,
            workflow_run_id=uuid4(),
            owner_user_id=approval.owner_user_id,
        )
        is None
    )
    assert (
        await restarted.get_execution_result(
            approval_id=approval.approval_id,
            session_id=approval.session_id,
            workflow_run_id=approval.workflow_run_id,
            owner_user_id=uuid4(),
        )
        is None
    )
    assert (
        await restarted.get_execution_result(
            approval_id=uuid4(),
            session_id=approval.session_id,
            workflow_run_id=approval.workflow_run_id,
            owner_user_id=approval.owner_user_id,
        )
        is None
    )


@pytest.mark.asyncio
async def test_failed_typed_result_round_trips_and_cannot_be_overwritten(
    tmp_path: Path,
) -> None:
    _, store = await _store(tmp_path)
    approval = _approval(tool_name=ToolName.REQUEST_DOCUMENT_EXPORT)
    await store.create_pending(approval)
    await _resolve(store, approval)
    await _claim(store, approval)
    mismatched_result = SandboxExecutionResult(
        status=ExecutionStatus.COMPLETED,
        exit_code=0,
        passed=True,
    )
    result = DocumentExportResult(
        status=ExecutionStatus.FAILED,
        failure_code="conversion_failed",
    )

    assert (
        await store.record_execution_result(
            approval_id=approval.approval_id,
            result=mismatched_result,
        )
        is None
    )
    persisted = await store.record_execution_result(
        approval_id=approval.approval_id,
        result=result,
    )
    repeated = await store.record_execution_result(
        approval_id=approval.approval_id,
        result=result,
    )
    retrieved = await store.get_execution_result(
        approval_id=approval.approval_id,
        session_id=approval.session_id,
        workflow_run_id=approval.workflow_run_id,
        owner_user_id=approval.owner_user_id,
    )

    assert persisted is not None
    assert persisted.execution_status is ExecutionStatus.FAILED
    assert repeated is None
    assert retrieved == result


@pytest.mark.asyncio
async def test_database_constraints_reject_invalid_approval_status(tmp_path: Path) -> None:
    database_path, store = await _store(tmp_path)
    approval = _approval()
    await store.create_pending(approval)

    with pytest.raises(aiosqlite.IntegrityError):
        async with LocalSQLiteDatabase(database_path).open() as connection:
            await connection.execute(
                "UPDATE approvals SET status = ? WHERE approval_id = ?",
                ("invalid", str(approval.approval_id)),
            )
