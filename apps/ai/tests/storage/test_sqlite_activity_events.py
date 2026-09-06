"""Focused tests for secure durable SQLite activity-event persistence."""

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import aiosqlite
import pytest
from pydantic import JsonValue, ValidationError

from app.storage import (
    ActivityEventContextMismatchError,
    LocalSQLiteDatabase,
    SQLiteActivityEventStore,
    SQLiteWorkflowStore,
)
from app.workflow.contracts import (
    ActivityEvent,
    ActivityEventType,
    ApprovalDecision,
    ExecutionStatus,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowSession,
    WorkflowStage,
    WorkflowStatus,
    WorkflowType,
)

_OCCURRED_AT = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


def _session(*, owner_user_id: UUID | None = None) -> WorkflowSession:
    return WorkflowSession(
        session_id=uuid4(),
        owner_user_id=owner_user_id or uuid4(),
        workflow_type=WorkflowType.INSPECTION_ANALYSIS,
        title="Inspection activity",
        stage=WorkflowStage.COLLECTING_INPUTS,
        status=WorkflowStatus.ACTIVE,
        created_at=_OCCURRED_AT,
        updated_at=_OCCURRED_AT,
    )


def _run(session: WorkflowSession) -> WorkflowRun:
    return WorkflowRun(
        workflow_run_id=uuid4(),
        session_id=session.session_id,
        owner_user_id=session.owner_user_id,
        workflow_type=session.workflow_type,
        stage=WorkflowStage.DRAFTING,
        stage_version=1,
        status=WorkflowRunStatus.ACTIVE,
        created_at=_OCCURRED_AT,
        updated_at=_OCCURRED_AT,
    )


def _event(
    session: WorkflowSession,
    *,
    workflow_run_id: UUID | None = None,
    event_type: ActivityEventType = ActivityEventType.PROGRESS,
    payload: dict[str, JsonValue] | None = None,
    event_id: int = 0,
) -> ActivityEvent:
    return ActivityEvent(
        event_id=event_id,
        session_id=session.session_id,
        workflow_run_id=workflow_run_id,
        event_type=event_type,
        occurred_at=_OCCURRED_AT,
        payload=payload
        if payload is not None
        else {
            "stage": WorkflowStage.DRAFTING,
            "completedUnits": 1,
            "totalUnits": 4,
        },
    )


async def _stores(
    tmp_path: Path,
) -> tuple[
    LocalSQLiteDatabase,
    SQLiteWorkflowStore,
    SQLiteActivityEventStore,
    WorkflowSession,
    WorkflowRun,
]:
    database = LocalSQLiteDatabase(tmp_path / "state" / "workbench.db")
    await database.initialize()
    workflows = SQLiteWorkflowStore(database)
    events = SQLiteActivityEventStore(database, poll_interval_seconds=0.01)
    session = _session()
    run = _run(session)
    await workflows.create_session(session)
    await workflows.create_run(run)
    return database, workflows, events, session, run


@pytest.mark.asyncio
async def test_initialize_creates_bounded_text_activity_event_schema(
    tmp_path: Path,
) -> None:
    database, _, store, session, _ = await _stores(tmp_path)

    await database.initialize()
    stored = await store.append(
        _event(
            session,
            event_type=ActivityEventType.SESSION_CREATED,
            payload={},
        ),
        owner_user_id=session.owner_user_id,
    )

    async with database.open() as connection:
        columns = await (
            await connection.execute("PRAGMA table_info(activity_events)")
        ).fetchall()
        indexes = await (
            await connection.execute("PRAGMA index_list(activity_events)")
        ).fetchall()
        foreign_keys = await (
            await connection.execute("PRAGMA foreign_key_list(activity_events)")
        ).fetchall()
        row = await (
            await connection.execute(
                """SELECT typeof(payload_json) AS payload_type, payload_json
                FROM activity_events WHERE session_id = ? AND event_id = ?""",
                (str(session.session_id), stored.event_id),
            )
        ).fetchone()

    assert [column["name"] for column in columns] == [
        "session_id",
        "event_id",
        "owner_user_id",
        "workflow_run_id",
        "event_type",
        "occurred_at",
        "payload_json",
    ]
    assert "activity_events_owner_replay" in {index["name"] for index in indexes}
    assert {foreign_key["table"] for foreign_key in foreign_keys} == {
        "workflow_sessions",
        "workflow_runs",
    }
    assert row is not None
    assert row["payload_type"] == "text"
    assert row["payload_json"] == "{}"


@pytest.mark.asyncio
async def test_sqlite_rejects_invalid_event_types_and_oversized_payloads(
    tmp_path: Path,
) -> None:
    database, _, _, session, _ = await _stores(tmp_path)

    async with database.open() as connection:
        with pytest.raises(aiosqlite.IntegrityError):
            await connection.execute(
                """INSERT INTO activity_events
                (session_id, event_id, owner_user_id, workflow_run_id,
                 event_type, occurred_at, payload_json)
                VALUES (?, ?, ?, NULL, ?, ?, ?)""",
                (
                    str(session.session_id),
                    1,
                    str(session.owner_user_id),
                    "model.reasoning",
                    _OCCURRED_AT.isoformat(),
                    "{}",
                ),
            )

    async with database.open() as connection:
        with pytest.raises(aiosqlite.IntegrityError):
            await connection.execute(
                """INSERT INTO activity_events
                (session_id, event_id, owner_user_id, workflow_run_id,
                 event_type, occurred_at, payload_json)
                VALUES (?, ?, ?, NULL, ?, ?, ?)""",
                (
                    str(session.session_id),
                    1,
                    str(session.owner_user_id),
                    ActivityEventType.SESSION_CREATED.value,
                    _OCCURRED_AT.isoformat(),
                    "é" * 4097,
                ),
            )


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        (ActivityEventType.SESSION_CREATED, {}),
        (
            ActivityEventType.UPLOAD_ACCEPTED,
            {
                "uploadId": str(uuid4()),
                "sourceId": str(uuid4()),
                "fileName": "inspection.jpg",
                "sizeBytes": 12,
            },
        ),
        (ActivityEventType.MESSAGE_ACCEPTED, {"messageId": str(uuid4())}),
        (
            ActivityEventType.STAGE_CHANGED,
            {
                "previousStage": WorkflowStage.RETRIEVING,
                "stage": WorkflowStage.DRAFTING,
                "stageVersion": 2,
                "status": WorkflowRunStatus.ACTIVE,
            },
        ),
        (
            ActivityEventType.PROGRESS,
            {"stage": WorkflowStage.DRAFTING, "completedUnits": 1, "totalUnits": 2},
        ),
        (ActivityEventType.MESSAGE_COMPLETED, {"messageId": str(uuid4())}),
        (ActivityEventType.APPROVAL_REQUIRED, {"approvalId": str(uuid4())}),
        (
            ActivityEventType.APPROVAL_RESOLVED,
            {"approvalId": str(uuid4()), "decision": ApprovalDecision.APPROVED},
        ),
        (ActivityEventType.ARTIFACT_CREATED, {"artifactId": str(uuid4())}),
        (
            ActivityEventType.SANDBOX_COMPLETED,
            {"status": ExecutionStatus.COMPLETED, "exitCode": 0, "passed": True},
        ),
        (
            ActivityEventType.WORKFLOW_FAILED,
            {"stage": WorkflowStage.FAILED, "failureCode": "validation_failed"},
        ),
    ],
)
def test_every_activity_type_accepts_only_its_sanitized_payload(
    event_type: ActivityEventType,
    payload: dict[str, JsonValue],
) -> None:
    session = _session()

    event = _event(session, event_type=event_type, payload=payload)

    assert event.event_type is event_type


def test_payload_validation_rejects_unapproved_or_sensitive_metadata() -> None:
    session = _session()

    for forbidden_key in (
        "prompt",
        "reasoning",
        "messageBody",
        "documentContents",
        "password",
        "token",
        "cookie",
        "secret",
        "stdout",
        "stderr",
        "bytes",
    ):
        with pytest.raises(ValidationError):
            _event(
                session,
                event_type=ActivityEventType.MESSAGE_ACCEPTED,
                payload={"messageId": str(uuid4()), forbidden_key: "confidential"},
            )


@pytest.mark.asyncio
async def test_append_round_trips_canonical_payload_after_restart(tmp_path: Path) -> None:
    database, _, store, session, run = await _stores(tmp_path)
    upload_id = uuid4()
    source_id = uuid4()
    event = _event(
        session,
        workflow_run_id=run.workflow_run_id,
        event_type=ActivityEventType.UPLOAD_ACCEPTED,
        payload={
            "size_bytes": 64,
            "file_name": "inspection.jpg",
            "source_id": str(source_id),
            "upload_id": str(upload_id),
        },
    )

    stored = await store.append(event, owner_user_id=session.owner_user_id)
    restarted = SQLiteActivityEventStore(LocalSQLiteDatabase(database.database_path))
    restored = await restarted.replay(
        session_id=session.session_id,
        owner_user_id=session.owner_user_id,
        after_event_id=0,
    )

    assert stored.event_id == 1
    assert restored == [stored]
    assert stored.payload == {
        "uploadId": str(upload_id),
        "sourceId": str(source_id),
        "fileName": "inspection.jpg",
        "sizeBytes": 64,
    }
    async with database.open() as connection:
        row = await (
            await connection.execute(
                "SELECT payload_json FROM activity_events WHERE session_id = ?",
                (str(session.session_id),),
            )
        ).fetchone()
    assert row is not None
    assert row["payload_json"] == json.dumps(
        stored.payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


@pytest.mark.asyncio
async def test_append_enforces_owner_and_workflow_run_context(tmp_path: Path) -> None:
    _, workflows, store, session, run = await _stores(tmp_path)
    other = _session(owner_user_id=session.owner_user_id)
    other_run = _run(other)
    await workflows.create_session(other)
    await workflows.create_run(other_run)

    with pytest.raises(ActivityEventContextMismatchError):
        await store.append(_event(session), owner_user_id=uuid4())
    with pytest.raises(ActivityEventContextMismatchError):
        await store.append(
            _event(session, workflow_run_id=other_run.workflow_run_id),
            owner_user_id=session.owner_user_id,
        )
    with pytest.raises(ActivityEventContextMismatchError):
        await store.replay(
            session_id=session.session_id,
            owner_user_id=uuid4(),
            after_event_id=0,
        )

    created = await store.append(
        _event(session, workflow_run_id=run.workflow_run_id),
        owner_user_id=session.owner_user_id,
    )
    assert created.workflow_run_id == run.workflow_run_id


@pytest.mark.asyncio
async def test_append_rejects_persisted_input_id_and_invalid_cursors(tmp_path: Path) -> None:
    database, _, store, session, _ = await _stores(tmp_path)

    with pytest.raises(ValueError, match="event_id=0"):
        await store.append(
            _event(session, event_id=1),
            owner_user_id=session.owner_user_id,
        )
    with pytest.raises(ValueError, match="nonnegative"):
        await store.replay(
            session_id=session.session_id,
            owner_user_id=session.owner_user_id,
            after_event_id=-1,
        )
    with pytest.raises(ValueError, match="poll interval"):
        SQLiteActivityEventStore(database, poll_interval_seconds=0)


@pytest.mark.asyncio
async def test_concurrent_appends_allocate_contiguous_per_session_ids(
    tmp_path: Path,
) -> None:
    database, workflows, _, first, _ = await _stores(tmp_path)
    second = _session()
    await workflows.create_session(second)
    writers = [
        SQLiteActivityEventStore(database, poll_interval_seconds=0.01)
        for _ in range(20)
    ]

    async def append(index: int, session: WorkflowSession) -> ActivityEvent:
        return await writers[index].append(
            _event(
                session,
                event_type=ActivityEventType.SESSION_CREATED,
                payload={},
            ),
            owner_user_id=session.owner_user_id,
        )

    first_results, second_results = await asyncio.gather(
        asyncio.gather(*(append(index, first) for index in range(10))),
        asyncio.gather(*(append(index + 10, second) for index in range(10))),
    )

    assert sorted(event.event_id for event in first_results) == list(range(1, 11))
    assert sorted(event.event_id for event in second_results) == list(range(1, 11))


@pytest.mark.asyncio
async def test_replay_is_ordered_exclusive_and_does_not_duplicate_events(
    tmp_path: Path,
) -> None:
    _, _, store, session, _ = await _stores(tmp_path)
    created = [
        await store.append(
            _event(
                session,
                event_type=ActivityEventType.SESSION_CREATED,
                payload={},
            ),
            owner_user_id=session.owner_user_id,
        )
        for _ in range(3)
    ]

    replayed = await store.replay(
        session_id=session.session_id,
        owner_user_id=session.owner_user_id,
        after_event_id=1,
    )

    assert replayed == created[1:]
    assert [event.event_id for event in replayed] == [2, 3]


@pytest.mark.asyncio
async def test_subscription_detects_events_from_another_store_instance(
    tmp_path: Path,
) -> None:
    database, _, writer, session, _ = await _stores(tmp_path)
    reader = SQLiteActivityEventStore(
        LocalSQLiteDatabase(database.database_path),
        poll_interval_seconds=0.01,
    )
    subscription = cast(
        AsyncGenerator[ActivityEvent, None],
        reader.subscribe(
            session_id=session.session_id,
            owner_user_id=session.owner_user_id,
            after_event_id=0,
        ),
    )
    pending = asyncio.ensure_future(anext(subscription))
    await asyncio.sleep(0.03)

    created = await writer.append(
        _event(
            session,
            event_type=ActivityEventType.SESSION_CREATED,
            payload={},
        ),
        owner_user_id=session.owner_user_id,
    )
    received = await asyncio.wait_for(pending, timeout=1)
    await subscription.aclose()

    assert received == created


@pytest.mark.asyncio
async def test_subscription_rejects_missing_or_foreign_session_at_startup(
    tmp_path: Path,
) -> None:
    _, _, store, session, _ = await _stores(tmp_path)

    for session_id, owner_user_id in (
        (uuid4(), session.owner_user_id),
        (session.session_id, uuid4()),
    ):
        subscription = cast(
            AsyncGenerator[ActivityEvent, None],
            store.subscribe(
                session_id=session_id,
                owner_user_id=owner_user_id,
                after_event_id=0,
            ),
        )
        with pytest.raises(ActivityEventContextMismatchError):
            await anext(subscription)
        await subscription.aclose()


@pytest.mark.asyncio
async def test_subscription_cancellation_releases_database_resources(
    tmp_path: Path,
) -> None:
    _, _, store, session, _ = await _stores(tmp_path)
    subscription = cast(
        AsyncGenerator[ActivityEvent, None],
        store.subscribe(
            session_id=session.session_id,
            owner_user_id=session.owner_user_id,
            after_event_id=0,
        ),
    )
    pending = asyncio.ensure_future(anext(subscription))
    await asyncio.sleep(0.03)
    pending.cancel()

    with pytest.raises(asyncio.CancelledError):
        await pending
    await subscription.aclose()

    created = await store.append(
        _event(
            session,
            event_type=ActivityEventType.SESSION_CREATED,
            payload={},
        ),
        owner_user_id=session.owner_user_id,
    )
    assert created.event_id == 1
