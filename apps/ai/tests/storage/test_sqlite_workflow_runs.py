"""Focused tests for durable SQLite workflow-run persistence."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import aiosqlite
import pytest
from pydantic import ValidationError

from app.storage import (
    LocalSQLiteDatabase,
    SQLiteWorkflowStore,
    WorkflowRunAlreadyExistsError,
    WorkflowRunContextMismatchError,
)
from app.workflow.contracts import (
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowSession,
    WorkflowStage,
    WorkflowStatus,
    WorkflowType,
)

_CREATED_AT = datetime(2026, 9, 6, 10, 0, tzinfo=UTC)


def _session(
    *,
    session_id: UUID | None = None,
    owner_user_id: UUID | None = None,
    workflow_type: WorkflowType = WorkflowType.INSPECTION_ANALYSIS,
) -> WorkflowSession:
    return WorkflowSession(
        session_id=session_id or uuid4(),
        owner_user_id=owner_user_id or uuid4(),
        workflow_type=workflow_type,
        title="Inspection workflow",
        stage=WorkflowStage.COLLECTING_INPUTS,
        status=WorkflowStatus.ACTIVE,
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )


def _run(
    session: WorkflowSession,
    *,
    workflow_run_id: UUID | None = None,
    stage: WorkflowStage = WorkflowStage.COLLECTING_INPUTS,
    stage_version: int = 0,
    status: WorkflowRunStatus = WorkflowRunStatus.QUEUED,
    sandbox_attempts: int = 0,
    created_at: datetime = _CREATED_AT + timedelta(minutes=1),
) -> WorkflowRun:
    return WorkflowRun(
        workflow_run_id=workflow_run_id or uuid4(),
        session_id=session.session_id,
        owner_user_id=session.owner_user_id,
        workflow_type=session.workflow_type,
        stage=stage,
        stage_version=stage_version,
        status=status,
        sandbox_attempts=sandbox_attempts,
        created_at=created_at,
        updated_at=created_at,
    )


async def _store(tmp_path: Path) -> tuple[LocalSQLiteDatabase, SQLiteWorkflowStore]:
    database = LocalSQLiteDatabase(tmp_path / "state" / "workbench.db")
    await database.initialize()
    return database, SQLiteWorkflowStore(database)


@pytest.mark.asyncio
async def test_initialize_creates_workflow_runs_without_public_sequence(
    tmp_path: Path,
) -> None:
    database = LocalSQLiteDatabase(tmp_path / "workbench.db")

    await database.initialize()
    await database.initialize()

    async with database.open() as connection:
        columns = await (await connection.execute("PRAGMA table_info(workflow_runs)")).fetchall()
        indexes = await (await connection.execute("PRAGMA index_list(workflow_runs)")).fetchall()
        foreign_keys = list(
            await (await connection.execute("PRAGMA foreign_key_list(workflow_runs)")).fetchall()
        )
        tables = await (
            await connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        ).fetchall()

    assert [row["name"] for row in columns] == [
        "sequence",
        "workflow_run_id",
        "session_id",
        "owner_user_id",
        "workflow_type",
        "stage",
        "stage_version",
        "status",
        "sandbox_attempts",
        "created_at",
        "updated_at",
        "execution_lease_expires_at",
        "interrupted_at",
        "retryable",
    ]
    assert "sequence" not in WorkflowRun.model_fields
    assert "workflow_runs_session_current" in {row["name"] for row in indexes}
    assert foreign_keys[0]["table"] == "workflow_sessions"
    assert foreign_keys[0]["on_delete"] == "NO ACTION"
    assert "sqlite_sequence" not in {row["name"] for row in tables}


@pytest.mark.asyncio
async def test_create_run_round_trips_after_restart_and_updates_session_projection(
    tmp_path: Path,
) -> None:
    database, store = await _store(tmp_path)
    session = _session()
    run = _run(
        session,
        stage=WorkflowStage.DRAFTING,
        status=WorkflowRunStatus.ACTIVE,
    )
    await store.create_session(session)

    created = await store.create_run(run)
    restarted = SQLiteWorkflowStore(LocalSQLiteDatabase(database.database_path))
    restored = await restarted.get_run(
        workflow_run_id=run.workflow_run_id,
        session_id=session.session_id,
        owner_user_id=session.owner_user_id,
    )
    projected = await restarted.get_session(session.session_id, session.owner_user_id)

    assert created == run
    assert restored == run
    assert projected.stage is WorkflowStage.DRAFTING
    assert projected.status is WorkflowStatus.ACTIVE
    assert projected.updated_at == run.updated_at


@pytest.mark.asyncio
async def test_get_run_is_owner_and_session_scoped(tmp_path: Path) -> None:
    _, store = await _store(tmp_path)
    session = _session()
    run = _run(session)
    await store.create_session(session)
    await store.create_run(run)

    assert (
        await store.get_run(
            workflow_run_id=run.workflow_run_id,
            session_id=session.session_id,
            owner_user_id=session.owner_user_id,
        )
        == run
    )
    assert (
        await store.get_run(
            workflow_run_id=run.workflow_run_id,
            session_id=uuid4(),
            owner_user_id=session.owner_user_id,
        )
        is None
    )
    assert (
        await store.get_run(
            workflow_run_id=run.workflow_run_id,
            session_id=session.session_id,
            owner_user_id=uuid4(),
        )
        is None
    )
    assert (
        await store.get_run(
            workflow_run_id=uuid4(),
            session_id=session.session_id,
            owner_user_id=session.owner_user_id,
        )
        is None
    )


@pytest.mark.asyncio
async def test_get_current_run_returns_newest_owner_scoped_run_after_restart(
    tmp_path: Path,
) -> None:
    database, store = await _store(tmp_path)
    session = _session()
    empty_session = _session(owner_user_id=session.owner_user_id)
    first_run = _run(
        session,
        stage=WorkflowStage.COMPLETED,
        status=WorkflowRunStatus.COMPLETED,
    )
    second_run = _run(
        session,
        stage=WorkflowStage.EXTRACTING,
        status=WorkflowRunStatus.ACTIVE,
        created_at=_CREATED_AT + timedelta(minutes=2),
    )
    await store.create_session(session)
    await store.create_session(empty_session)
    await store.create_run(first_run)
    await store.create_run(second_run)

    restarted = SQLiteWorkflowStore(LocalSQLiteDatabase(database.database_path))

    assert (
        await restarted.get_current_run(
            session_id=session.session_id,
            owner_user_id=session.owner_user_id,
        )
        == second_run
    )
    assert (
        await restarted.get_current_run(
            session_id=session.session_id,
            owner_user_id=uuid4(),
        )
        is None
    )
    assert (
        await restarted.get_current_run(
            session_id=empty_session.session_id,
            owner_user_id=empty_session.owner_user_id,
        )
        is None
    )
    assert (
        await restarted.get_current_run(
            session_id=uuid4(),
            owner_user_id=session.owner_user_id,
        )
        is None
    )
    assert "sequence" not in WorkflowRun.model_fields


@pytest.mark.asyncio
async def test_create_rejects_missing_or_mismatched_session_context(tmp_path: Path) -> None:
    _, store = await _store(tmp_path)
    session = _session()
    run = _run(session)

    with pytest.raises(WorkflowRunContextMismatchError):
        await store.create_run(run)

    await store.create_session(session)
    for update in (
        {"owner_user_id": uuid4()},
        {"workflow_type": WorkflowType.CODE_REPAIR},
        {"session_id": uuid4()},
    ):
        with pytest.raises(WorkflowRunContextMismatchError):
            await store.create_run(run.model_copy(update=update))


@pytest.mark.asyncio
async def test_duplicate_run_id_is_rejected_without_overwrite(tmp_path: Path) -> None:
    _, store = await _store(tmp_path)
    session = _session()
    original = _run(session)
    duplicate = _run(
        session,
        workflow_run_id=original.workflow_run_id,
        stage=WorkflowStage.DRAFTING,
        status=WorkflowRunStatus.ACTIVE,
    )
    await store.create_session(session)
    await store.create_run(original)

    with pytest.raises(WorkflowRunAlreadyExistsError):
        await store.create_run(duplicate)

    assert (
        await store.get_run(
            workflow_run_id=original.workflow_run_id,
            session_id=session.session_id,
            owner_user_id=session.owner_user_id,
        )
        == original
    )


@pytest.mark.asyncio
async def test_compare_and_set_updates_run_version_and_session_projection(
    tmp_path: Path,
) -> None:
    _, store = await _store(tmp_path)
    session = _session()
    run = _run(session)
    await store.create_session(session)
    await store.create_run(run)

    updated = await store.compare_and_set_stage(
        session_id=session.session_id,
        workflow_run_id=run.workflow_run_id,
        owner_user_id=session.owner_user_id,
        expected_stage=run.stage,
        expected_stage_version=run.stage_version,
        next_stage=WorkflowStage.EXTRACTING,
        next_status=WorkflowRunStatus.ACTIVE,
        sandbox_attempts=0,
    )

    assert updated is not None
    assert updated.stage is WorkflowStage.EXTRACTING
    assert updated.stage_version == run.stage_version + 1
    assert updated.status is WorkflowRunStatus.ACTIVE
    assert updated.updated_at.tzinfo is UTC
    assert updated.updated_at > run.updated_at
    projected = await store.get_session(session.session_id, session.owner_user_id)
    assert projected.stage is WorkflowStage.EXTRACTING
    assert projected.status is WorkflowStatus.ACTIVE
    assert projected.updated_at == updated.updated_at


@pytest.mark.asyncio
async def test_stale_or_missing_compare_and_set_returns_none(tmp_path: Path) -> None:
    _, store = await _store(tmp_path)
    session = _session()
    run = _run(session)
    await store.create_session(session)
    await store.create_run(run)

    assert (
        await store.compare_and_set_stage(
            session_id=uuid4(),
            workflow_run_id=run.workflow_run_id,
            owner_user_id=session.owner_user_id,
            expected_stage=run.stage,
            expected_stage_version=run.stage_version,
            next_stage=WorkflowStage.EXTRACTING,
            next_status=WorkflowRunStatus.ACTIVE,
            sandbox_attempts=0,
        )
        is None
    )
    assert (
        await store.compare_and_set_stage(
            session_id=session.session_id,
            workflow_run_id=uuid4(),
            owner_user_id=session.owner_user_id,
            expected_stage=run.stage,
            expected_stage_version=run.stage_version,
            next_stage=WorkflowStage.EXTRACTING,
            next_status=WorkflowRunStatus.ACTIVE,
            sandbox_attempts=0,
        )
        is None
    )
    assert (
        await store.compare_and_set_stage(
            session_id=session.session_id,
            workflow_run_id=run.workflow_run_id,
            owner_user_id=uuid4(),
            expected_stage=run.stage,
            expected_stage_version=run.stage_version,
            next_stage=WorkflowStage.EXTRACTING,
            next_status=WorkflowRunStatus.ACTIVE,
            sandbox_attempts=0,
        )
        is None
    )
    assert (
        await store.compare_and_set_stage(
            session_id=session.session_id,
            workflow_run_id=run.workflow_run_id,
            owner_user_id=session.owner_user_id,
            expected_stage=WorkflowStage.PLANNING,
            expected_stage_version=run.stage_version,
            next_stage=WorkflowStage.EXTRACTING,
            next_status=WorkflowRunStatus.ACTIVE,
            sandbox_attempts=0,
        )
        is None
    )
    assert (
        await store.compare_and_set_stage(
            session_id=session.session_id,
            workflow_run_id=run.workflow_run_id,
            owner_user_id=session.owner_user_id,
            expected_stage=run.stage,
            expected_stage_version=run.stage_version + 1,
            next_stage=WorkflowStage.EXTRACTING,
            next_status=WorkflowRunStatus.ACTIVE,
            sandbox_attempts=0,
        )
        is None
    )


@pytest.mark.asyncio
async def test_concurrent_compare_and_set_has_exactly_one_winner(tmp_path: Path) -> None:
    _, store = await _store(tmp_path)
    session = _session()
    run = _run(session)
    await store.create_session(session)
    await store.create_run(run)

    updates = await asyncio.gather(
        *(
            store.compare_and_set_stage(
                session_id=session.session_id,
                workflow_run_id=run.workflow_run_id,
                owner_user_id=session.owner_user_id,
                expected_stage=run.stage,
                expected_stage_version=run.stage_version,
                next_stage=WorkflowStage.EXTRACTING,
                next_status=WorkflowRunStatus.ACTIVE,
                sandbox_attempts=0,
            )
            for _ in range(2)
        )
    )

    assert sum(item is not None for item in updates) == 1
    persisted = await store.get_run(
        workflow_run_id=run.workflow_run_id,
        session_id=session.session_id,
        owner_user_id=session.owner_user_id,
    )
    assert persisted is not None
    assert persisted.stage_version == 1


@pytest.mark.asyncio
async def test_database_rejects_a_second_nonterminal_run(
    tmp_path: Path,
) -> None:
    _, store = await _store(tmp_path)
    session = _session()
    older = _run(session)
    newer = _run(
        session,
        stage=WorkflowStage.DRAFTING,
        status=WorkflowRunStatus.ACTIVE,
        created_at=_CREATED_AT + timedelta(minutes=2),
    )
    await store.create_session(session)
    await store.create_run(older)
    with pytest.raises(WorkflowRunAlreadyExistsError):
        await store.create_run(newer)


@pytest.mark.asyncio
async def test_invalid_next_state_is_rejected_without_mutation(tmp_path: Path) -> None:
    _, store = await _store(tmp_path)
    session = _session()
    run = _run(session)
    await store.create_session(session)
    await store.create_run(run)

    with pytest.raises(ValidationError):
        await store.compare_and_set_stage(
            session_id=session.session_id,
            workflow_run_id=run.workflow_run_id,
            owner_user_id=session.owner_user_id,
            expected_stage=run.stage,
            expected_stage_version=run.stage_version,
            next_stage=WorkflowStage.AWAITING_APPROVAL,
            next_status=WorkflowRunStatus.ACTIVE,
            sandbox_attempts=0,
        )
    with pytest.raises(ValidationError):
        await store.compare_and_set_stage(
            session_id=session.session_id,
            workflow_run_id=run.workflow_run_id,
            owner_user_id=session.owner_user_id,
            expected_stage=run.stage,
            expected_stage_version=run.stage_version,
            next_stage=WorkflowStage.EXTRACTING,
            next_status=WorkflowRunStatus.ACTIVE,
            sandbox_attempts=-1,
        )

    persisted = await store.get_run(
        workflow_run_id=run.workflow_run_id,
        session_id=session.session_id,
        owner_user_id=session.owner_user_id,
    )
    assert persisted == run


@pytest.mark.asyncio
async def test_code_run_sandbox_attempt_rules_are_enforced(tmp_path: Path) -> None:
    _, store = await _store(tmp_path)
    session = _session(workflow_type=WorkflowType.CODE_REPAIR)
    run = _run(
        session,
        stage=WorkflowStage.PLANNING,
        status=WorkflowRunStatus.ACTIVE,
    )
    await store.create_session(session)
    await store.create_run(run)

    with pytest.raises(ValidationError, match="sandbox execution requires"):
        await store.compare_and_set_stage(
            session_id=session.session_id,
            workflow_run_id=run.workflow_run_id,
            owner_user_id=session.owner_user_id,
            expected_stage=run.stage,
            expected_stage_version=run.stage_version,
            next_stage=WorkflowStage.SANDBOX_EXECUTING,
            next_status=WorkflowRunStatus.ACTIVE,
            sandbox_attempts=0,
        )


@pytest.mark.asyncio
async def test_create_run_rolls_back_when_projection_update_fails(tmp_path: Path) -> None:
    database, store = await _store(tmp_path)
    session = _session()
    run = _run(session)
    await store.create_session(session)
    async with database.open() as connection:
        await connection.execute(
            """CREATE TRIGGER reject_run_projection BEFORE UPDATE ON workflow_sessions
            BEGIN SELECT RAISE(ABORT, 'projection rejected'); END"""
        )

    with pytest.raises(WorkflowRunContextMismatchError):
        await store.create_run(run)

    assert (
        await store.get_run(
            workflow_run_id=run.workflow_run_id,
            session_id=session.session_id,
            owner_user_id=session.owner_user_id,
        )
        is None
    )


@pytest.mark.asyncio
async def test_compare_and_set_rolls_back_when_projection_update_fails(
    tmp_path: Path,
) -> None:
    database, store = await _store(tmp_path)
    session = _session()
    run = _run(session)
    await store.create_session(session)
    await store.create_run(run)
    async with database.open() as connection:
        await connection.execute(
            """CREATE TRIGGER reject_run_projection BEFORE UPDATE ON workflow_sessions
            BEGIN SELECT RAISE(ABORT, 'projection rejected'); END"""
        )

    with pytest.raises(aiosqlite.IntegrityError):
        await store.compare_and_set_stage(
            session_id=session.session_id,
            workflow_run_id=run.workflow_run_id,
            owner_user_id=session.owner_user_id,
            expected_stage=run.stage,
            expected_stage_version=run.stage_version,
            next_stage=WorkflowStage.EXTRACTING,
            next_status=WorkflowRunStatus.ACTIVE,
            sandbox_attempts=0,
        )

    persisted = await store.get_run(
        workflow_run_id=run.workflow_run_id,
        session_id=session.session_id,
        owner_user_id=session.owner_user_id,
    )
    assert persisted == run


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("column", "value"),
    [("stage_version", -1), ("sandbox_attempts", -1), ("status", "unknown")],
)
async def test_sqlite_constraints_reject_invalid_run_values(
    tmp_path: Path,
    column: str,
    value: int | str,
) -> None:
    database, store = await _store(tmp_path)
    session = _session()
    run = _run(session)
    await store.create_session(session)
    values: dict[str, object] = {
        "stage_version": run.stage_version,
        "sandbox_attempts": run.sandbox_attempts,
        "status": run.status.value,
    }
    values[column] = value

    with pytest.raises(aiosqlite.IntegrityError):
        async with database.open() as connection:
            await connection.execute(
                """INSERT INTO workflow_runs (
                    workflow_run_id, session_id, owner_user_id, workflow_type,
                    stage, stage_version, status, sandbox_attempts,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(run.workflow_run_id),
                    str(run.session_id),
                    str(run.owner_user_id),
                    run.workflow_type.value,
                    run.stage.value,
                    values["stage_version"],
                    values["status"],
                    values["sandbox_attempts"],
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                ),
            )


@pytest.mark.asyncio
async def test_initialize_reconciles_legacy_multiple_nonterminal_runs(
    tmp_path: Path,
) -> None:
    """Regression: multiple nonterminal runs for one session must not break index creation."""

    db_path = tmp_path / "workbench.db"
    database = LocalSQLiteDatabase(db_path)
    await database.initialize()
    store = SQLiteWorkflowStore(database)

    item = _session()
    await store.create_session(item)

    run1 = _run(item, status=WorkflowRunStatus.ACTIVE, created_at=_CREATED_AT)
    later = _CREATED_AT + timedelta(seconds=1)
    run2 = _run(item, status=WorkflowRunStatus.QUEUED, created_at=later)

    async with database.open() as connection:
        await connection.execute(
            "DROP INDEX IF EXISTS workflow_runs_one_nonterminal"
        )
        for run in (run1, run2):
            await connection.execute(
                """INSERT INTO workflow_runs (
                    workflow_run_id, session_id, owner_user_id, workflow_type,
                    stage, stage_version, status, sandbox_attempts,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(run.workflow_run_id),
                    str(run.session_id),
                    str(run.owner_user_id),
                    run.workflow_type.value,
                    run.stage.value,
                    run.stage_version,
                    run.status.value,
                    run.sandbox_attempts,
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                ),
            )

    fresh = LocalSQLiteDatabase(db_path)
    await fresh.initialize()

    async with fresh.open() as connection:
        cursor = await connection.execute(
            "SELECT status FROM workflow_runs WHERE session_id = ? ORDER BY sequence",
            (str(item.session_id),),
        )
        statuses = [row["status"] for row in await cursor.fetchall()]

    assert statuses.count("failed") == 1
    assert statuses.count("active") + statuses.count("queued") == 1
