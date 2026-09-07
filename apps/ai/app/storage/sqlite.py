"""Local SQLite foundation and Backend 2 metadata persistence."""

import asyncio
import hashlib
import json
import math
import os
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import aiosqlite
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError, field_validator

from app.auth.contracts import UserRole
from app.ports.local_backend import (
    AuditRecord,
    AuthSessionRecord,
    PendingApprovalPreparation,
    SelectedUploadSnapshot,
    StoredArtifact,
    StoredExtractionFindings,
    StoredIdentity,
    StoredRetrievedEvidence,
    WorkflowAdmissionStatus,
    WorkflowMessage,
    WorkflowRunAdmission,
    WorkflowRunAdmissionRequest,
)
from app.tools.contracts import DocumentExportArguments, ToolExecutionResult, ValidatedToolCall
from app.workflow.contracts import (
    ActivityEvent,
    ActivityEventType,
    Approval,
    ApprovalDecision,
    ApprovalExecutionClaim,
    ApprovalResolution,
    ApprovalStatus,
    ExecutionStatus,
    UtcTimestamp,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowSession,
    WorkflowStage,
    WorkflowStatus,
    WorkflowType,
)

_TOOL_EXECUTION_RESULT_ADAPTER: TypeAdapter[ToolExecutionResult] = TypeAdapter(ToolExecutionResult)

_CREATE_IDENTITIES_TABLE = """
CREATE TABLE IF NOT EXISTS identities (
    user_id TEXT PRIMARY KEY NOT NULL,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('employee', 'operator')),
    password_hash TEXT NOT NULL,
    disabled INTEGER NOT NULL DEFAULT 0 CHECK (disabled IN (0, 1))
)
"""

_CREATE_AUTH_SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS auth_sessions (
    auth_session_id TEXT PRIMARY KEY NOT NULL,
    user_id TEXT NOT NULL REFERENCES identities(user_id),
    token_id TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
)
"""

_CREATE_AUDIT_RECORDS_TABLE = """
CREATE TABLE IF NOT EXISTS audit_records (
    audit_id TEXT PRIMARY KEY NOT NULL,
    action TEXT NOT NULL,
    actor_user_id TEXT,
    session_id TEXT,
    workflow_run_id TEXT,
    outcome TEXT NOT NULL,
    occurred_at TEXT NOT NULL
)
"""

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

_CREATE_APPROVALS_TABLE = """
CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY NOT NULL,
    session_id TEXT NOT NULL,
    workflow_run_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    workflow_type TEXT NOT NULL CHECK (
        workflow_type IN ('inspectionAnalysis', 'codeRepair')
    ),
    stage TEXT NOT NULL CHECK (
        stage IN (
            'collectingInputs', 'extracting', 'retrieving', 'drafting',
            'validating', 'planning', 'awaitingApproval', 'exporting',
            'sandboxExecuting', 'repairing', 'approvalRejected',
            'completed', 'failed'
        )
    ),
    stage_version INTEGER NOT NULL CHECK (stage_version >= 0),
    tool_name TEXT NOT NULL CHECK (
        length(tool_name) BETWEEN 1 AND 100
        AND substr(tool_name, 1, 1) GLOB '[a-z]'
        AND tool_name NOT GLOB '*[^a-z0-9_]*'
    ),
    normalized_arguments TEXT NOT NULL,
    arguments_hash TEXT NOT NULL CHECK (
        length(arguments_hash) = 64
        AND arguments_hash NOT GLOB '*[^0-9a-f]*'
    ),
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
    requested_at TEXT NOT NULL,
    resolved_at TEXT,
    resolved_by_user_id TEXT,
    decision TEXT CHECK (decision IS NULL OR decision IN ('approved', 'rejected')),
    comment TEXT CHECK (
        comment IS NULL OR length(trim(comment)) BETWEEN 1 AND 1000
    ),
    execution_status TEXT NOT NULL CHECK (
        execution_status IN (
            'notApplicable', 'notStarted', 'queued', 'completed', 'failed'
        )
    ),
    execution_claim_token TEXT UNIQUE,
    execution_result TEXT,
    CHECK (
        (
            status = 'pending'
            AND resolved_at IS NULL
            AND resolved_by_user_id IS NULL
            AND decision IS NULL
            AND execution_status = 'notStarted'
            AND execution_claim_token IS NULL
            AND execution_result IS NULL
        )
        OR (
            status = 'approved'
            AND resolved_at IS NOT NULL
            AND resolved_by_user_id IS NOT NULL
            AND decision = 'approved'
            AND execution_status IN ('notStarted', 'queued', 'completed', 'failed')
            AND (
                (execution_status = 'notStarted'
                    AND execution_claim_token IS NULL AND execution_result IS NULL)
                OR (execution_status = 'queued'
                    AND execution_claim_token IS NOT NULL AND execution_result IS NULL)
                OR (execution_status IN ('completed', 'failed')
                    AND execution_claim_token IS NOT NULL AND execution_result IS NOT NULL)
            )
        )
        OR (
            status = 'rejected'
            AND resolved_at IS NOT NULL
            AND resolved_by_user_id IS NOT NULL
            AND decision = 'rejected'
            AND execution_status = 'notApplicable'
            AND execution_claim_token IS NULL
            AND execution_result IS NULL
        )
    )
)
"""

_APPROVAL_COLUMNS = """approval_id, session_id, workflow_run_id, owner_user_id,
workflow_type, stage, stage_version, tool_name, normalized_arguments,
arguments_hash, status, requested_at, resolved_at, resolved_by_user_id,
decision, comment, execution_status, execution_result"""
_CREATE_WORKFLOW_SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS workflow_sessions (
    session_id TEXT PRIMARY KEY NOT NULL,
    owner_user_id TEXT NOT NULL,
    workflow_type TEXT NOT NULL CHECK (workflow_type IN ('inspectionAnalysis', 'codeRepair')),
    title TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('active', 'completed', 'failed', 'approvalRejected')
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    client_session_id TEXT
)
"""

_CREATE_WORKFLOW_SESSIONS_OWNER_INDEX = """
CREATE INDEX IF NOT EXISTS workflow_sessions_owner_updated
ON workflow_sessions (owner_user_id, updated_at DESC)
"""

_CREATE_WORKFLOW_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    sequence INTEGER PRIMARY KEY,
    workflow_run_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL REFERENCES workflow_sessions(session_id),
    owner_user_id TEXT NOT NULL,
    workflow_type TEXT NOT NULL CHECK (
        workflow_type IN ('inspectionAnalysis', 'codeRepair')
    ),
    stage TEXT NOT NULL CHECK (
        stage IN (
            'collectingInputs', 'extracting', 'retrieving', 'drafting',
            'validating', 'planning', 'awaitingApproval', 'exporting',
            'sandboxExecuting', 'repairing', 'approvalRejected',
            'completed', 'failed'
        )
    ),
    stage_version INTEGER NOT NULL CHECK (stage_version >= 0),
    status TEXT NOT NULL CHECK (
        status IN (
            'queued', 'active', 'waitingForApproval', 'completed',
            'failed', 'approvalRejected'
        )
    ),
    sandbox_attempts INTEGER NOT NULL CHECK (sandbox_attempts >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
    ,execution_lease_expires_at TEXT
    ,interrupted_at TEXT
    ,retryable INTEGER NOT NULL DEFAULT 0 CHECK(retryable IN (0, 1))
)
"""

_CREATE_WORKFLOW_RUNS_CURRENT_INDEX = """
CREATE INDEX IF NOT EXISTS workflow_runs_session_current
ON workflow_runs (session_id, sequence DESC)
"""

_NONTERMINAL_RUN_STATUSES = tuple(
    status.value
    for status in WorkflowRunStatus
    if status
    not in {
        WorkflowRunStatus.COMPLETED,
        WorkflowRunStatus.FAILED,
        WorkflowRunStatus.APPROVAL_REJECTED,
    }
)
_CREATE_WORKFLOW_RUNS_ONE_NONTERMINAL_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS workflow_runs_one_nonterminal
ON workflow_runs(session_id)
WHERE status IN ('queued', 'active', 'waitingForApproval')
"""

_CREATE_ACTIVITY_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS activity_events (
    session_id TEXT NOT NULL REFERENCES workflow_sessions(session_id),
    event_id INTEGER NOT NULL CHECK (event_id > 0),
    owner_user_id TEXT NOT NULL,
    workflow_run_id TEXT REFERENCES workflow_runs(workflow_run_id),
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'session.created', 'upload.accepted', 'message.accepted',
            'workflow.stageChanged', 'workflow.progress', 'message.completed',
            'approval.required', 'approval.resolved', 'artifact.created',
            'sandbox.completed', 'workflow.failed'
        )
    ),
    occurred_at TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (
        typeof(payload_json) = 'text'
        AND length(CAST(payload_json AS BLOB)) <= 8192
    ),
    PRIMARY KEY (session_id, event_id)
)
"""

_CREATE_ACTIVITY_EVENTS_REPLAY_INDEX = """
CREATE INDEX IF NOT EXISTS activity_events_owner_replay
ON activity_events (session_id, owner_user_id, event_id)
"""

_ACTIVITY_EVENT_COLUMNS = """session_id, event_id, owner_user_id,
workflow_run_id, event_type, occurred_at, payload_json"""

_WORKFLOW_RUN_COLUMNS = """workflow_run_id, session_id, owner_user_id,
workflow_type, stage, stage_version, status, sandbox_attempts, created_at,
updated_at, execution_lease_expires_at, interrupted_at, retryable"""

_CREATE_WORKFLOW_SESSIONS_CLIENT_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS workflow_sessions_client
ON workflow_sessions (client_session_id)
"""

_CREATE_WORKFLOW_MESSAGES_TABLE = """
CREATE TABLE IF NOT EXISTS workflow_messages (
    sequence INTEGER PRIMARY KEY,
    message_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL REFERENCES workflow_sessions(session_id),
    author_user_id TEXT,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    client_message_id TEXT
)
"""

_CREATE_WORKFLOW_MESSAGES_SESSION_INDEX = """
CREATE INDEX IF NOT EXISTS workflow_messages_session_order
ON workflow_messages (session_id, sequence)
"""

_CREATE_ARTIFACTS_TABLE = """
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY NOT NULL,
    session_id TEXT NOT NULL REFERENCES workflow_sessions(session_id) ON DELETE CASCADE,
    workflow_run_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    approval_id TEXT NOT NULL REFERENCES approvals(approval_id),
    draft_id TEXT NOT NULL,
    format TEXT NOT NULL CHECK (format IN ('docx', 'pdf')),
    file_name TEXT NOT NULL COLLATE NOCASE CHECK (
        length(file_name) BETWEEN 1 AND 255
        AND file_name NOT IN ('.', '..')
        AND instr(file_name, '/') = 0
        AND instr(file_name, '\\') = 0
        AND instr(file_name, ':') = 0
        AND instr(file_name, '*') = 0
        AND instr(file_name, '?') = 0
        AND instr(file_name, '"') = 0
        AND instr(file_name, '<') = 0
        AND instr(file_name, '>') = 0
        AND instr(file_name, '|') = 0
        AND file_name = rtrim(file_name, ' .')
    ),
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
    sha256 TEXT NOT NULL CHECK (
        length(sha256) = 64
        AND sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    created_at TEXT NOT NULL,
    UNIQUE (session_id, file_name),
    CHECK (
        (format = 'docx' AND lower(file_name) GLOB '*.docx')
        OR (format = 'pdf' AND lower(file_name) GLOB '*.pdf')
    )
)
"""

_CREATE_ARTIFACTS_RUN_INDEX = """
CREATE INDEX IF NOT EXISTS artifacts_run_created
ON artifacts (session_id, workflow_run_id, owner_user_id, created_at, artifact_id)
"""

_CREATE_WORKFLOW_MESSAGES_CLIENT_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS workflow_messages_session_client
ON workflow_messages (session_id, client_message_id)
"""

_CREATE_WORKFLOW_UPLOADS_TABLE = """
CREATE TABLE IF NOT EXISTS workflow_uploads (
    upload_id TEXT PRIMARY KEY NOT NULL,
    session_id TEXT NOT NULL REFERENCES workflow_sessions(session_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL UNIQUE,
    stored_file_name TEXT NOT NULL CHECK (
        length(stored_file_name) BETWEEN 1 AND 255
        AND instr(stored_file_name, '/') = 0
        AND instr(stored_file_name, '\\') = 0
    ),
    file_name TEXT NOT NULL CHECK (
        length(file_name) BETWEEN 1 AND 255
        AND file_name NOT IN ('.', '..')
        AND instr(file_name, '/') = 0
        AND instr(file_name, '\\') = 0
        AND instr(file_name, ':') = 0
        AND instr(file_name, '*') = 0
        AND instr(file_name, '?') = 0
        AND instr(file_name, '"') = 0
        AND instr(file_name, '<') = 0
        AND instr(file_name, '>') = 0
        AND instr(file_name, '|') = 0
        AND file_name = rtrim(file_name, ' .')
    ),
    mime_type TEXT NOT NULL CHECK (length(mime_type) BETWEEN 1 AND 255),
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
    sha256 TEXT NOT NULL CHECK (
        length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    created_at TEXT NOT NULL,
    UNIQUE (session_id, stored_file_name)
)
"""

_CREATE_WORKFLOW_RUN_ADMISSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS workflow_run_admissions (
    session_id TEXT NOT NULL REFERENCES workflow_sessions(session_id) ON DELETE CASCADE,
    client_message_id TEXT NOT NULL,
    workflow_run_id TEXT NOT NULL UNIQUE REFERENCES workflow_runs(workflow_run_id),
    message_id TEXT NOT NULL UNIQUE REFERENCES workflow_messages(message_id),
    owner_user_id TEXT NOT NULL,
    semantic_hash TEXT NOT NULL CHECK(length(semantic_hash) = 64),
    created_at TEXT NOT NULL,
    PRIMARY KEY(session_id, client_message_id)
)
"""

_CREATE_WORKFLOW_RUN_INPUTS_TABLE = """
CREATE TABLE IF NOT EXISTS workflow_run_inputs (
    workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(workflow_run_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    upload_id TEXT NOT NULL REFERENCES workflow_uploads(upload_id),
    source_id TEXT NOT NULL,
    file_name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK(size_bytes > 0),
    sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
    PRIMARY KEY(workflow_run_id, ordinal),
    UNIQUE(workflow_run_id, upload_id)
)
"""

_CREATE_EXTRACTION_FINDINGS_TABLE = """
CREATE TABLE IF NOT EXISTS extraction_findings (
    workflow_run_id TEXT PRIMARY KEY REFERENCES workflow_runs(workflow_run_id) ON DELETE CASCADE,
    session_id TEXT NOT NULL, owner_user_id TEXT NOT NULL,
    source_upload_ids_json TEXT NOT NULL, findings_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK(length(CAST(findings_json AS BLOB)) <= 1048576)
)
"""

_CREATE_RETRIEVED_EVIDENCE_TABLE = """
CREATE TABLE IF NOT EXISTS retrieved_evidence (
    workflow_run_id TEXT PRIMARY KEY REFERENCES workflow_runs(workflow_run_id) ON DELETE CASCADE,
    session_id TEXT NOT NULL, owner_user_id TEXT NOT NULL,
    evidence_json TEXT NOT NULL, created_at TEXT NOT NULL,
    CHECK(length(CAST(evidence_json AS BLOB)) <= 1048576)
)
"""

_CREATE_WORKFLOW_OUTPUTS_TABLE = """
CREATE TABLE IF NOT EXISTS workflow_outputs (
    workflow_run_id TEXT PRIMARY KEY REFERENCES workflow_runs(workflow_run_id) ON DELETE CASCADE,
    session_id TEXT NOT NULL, owner_user_id TEXT NOT NULL,
    approval_id TEXT NOT NULL UNIQUE REFERENCES approvals(approval_id),
    tool_name TEXT NOT NULL, normalized_arguments TEXT NOT NULL,
    arguments_hash TEXT NOT NULL CHECK(length(arguments_hash) = 64),
    created_at TEXT NOT NULL
)
"""

_CREATE_WORKFLOW_UPLOADS_SESSION_INDEX = """
CREATE INDEX IF NOT EXISTS workflow_uploads_session_created
ON workflow_uploads (session_id, created_at, upload_id)
"""

_CREATE_KNOWLEDGE_SOURCES_TABLE = """
CREATE TABLE IF NOT EXISTS knowledge_sources (
    knowledge_source_id TEXT PRIMARY KEY NOT NULL,
    document_id TEXT NOT NULL UNIQUE CHECK (length(document_id) BETWEEN 1 AND 200),
    source_id TEXT NOT NULL UNIQUE CHECK (length(source_id) BETWEEN 1 AND 200),
    approved_by_user_id TEXT NOT NULL REFERENCES identities(user_id),
    file_name TEXT NOT NULL CHECK (length(file_name) BETWEEN 1 AND 255),
    mime_type TEXT NOT NULL CHECK (length(mime_type) BETWEEN 1 AND 255),
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
    sha256 TEXT NOT NULL CHECK (
        length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    created_at TEXT NOT NULL
)
"""

_CREATE_GROUNDED_DRAFTS_TABLE = """
CREATE TABLE IF NOT EXISTS grounded_drafts (
    draft_id TEXT PRIMARY KEY NOT NULL,
    session_id TEXT NOT NULL REFERENCES workflow_sessions(session_id) ON DELETE CASCADE,
    workflow_run_id TEXT NOT NULL UNIQUE REFERENCES workflow_runs(workflow_run_id),
    owner_user_id TEXT NOT NULL,
    draft_json TEXT NOT NULL CHECK (
        typeof(draft_json) = 'text' AND length(CAST(draft_json AS BLOB)) <= 1048576
    ),
    created_at TEXT NOT NULL
)
"""

_CREATE_GROUNDED_DRAFTS_OWNER_INDEX = """
CREATE INDEX IF NOT EXISTS grounded_drafts_owner
ON grounded_drafts (session_id, workflow_run_id, owner_user_id, draft_id)
"""


class SessionAlreadyExistsError(RuntimeError):
    """Raised when session metadata already exists for a session identifier."""


class InvalidSessionStatusError(ValueError):
    """Raised when a caller supplies a status outside the canonical session states."""


class ArtifactAlreadyExistsError(RuntimeError):
    """Raised when an artifact ID or session-local filename is already persisted."""


class ArtifactContextMismatchError(PermissionError):
    """Raised when artifact metadata is not authorized by the winning approval claim."""


class ActivityEventContextMismatchError(PermissionError):
    """Raised when activity access does not match an existing owned workflow context."""


async def _insert_activity_event(
    connection: aiosqlite.Connection,
    event: ActivityEvent,
    *,
    owner_user_id: UUID,
) -> ActivityEvent:
    """Insert an event on the caller's transaction without nested connections."""

    if event.event_id != 0:
        raise ValueError("new activity events must use event_id=0")
    payload_json = SQLiteActivityEventStore._serialize_payload(event.payload)
    row = await (
        await connection.execute(
            """SELECT COALESCE(MAX(event_id), 0) + 1 AS next_event_id
            FROM activity_events WHERE session_id = ?""",
            (str(event.session_id),),
        )
    ).fetchone()
    if row is None:
        raise RuntimeError("next activity event ID could not be allocated")
    persisted = event.model_copy(update={"event_id": int(row["next_event_id"])})
    await connection.execute(
        """INSERT INTO activity_events (
            session_id, event_id, owner_user_id, workflow_run_id,
            event_type, occurred_at, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            str(persisted.session_id),
            persisted.event_id,
            str(owner_user_id),
            str(persisted.workflow_run_id) if persisted.workflow_run_id else None,
            persisted.event_type.value,
            persisted.occurred_at.isoformat(),
            payload_json,
        ),
    )
    return persisted


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

    _DIRECTORY_MODE = 0o700
    _DATABASE_MODE = 0o600

    def __init__(self, database_path: Path) -> None:
        candidate = database_path.expanduser()
        if candidate.is_symlink():
            raise ValueError("database path must not be a symbolic link")
        self._database_path = candidate.resolve(strict=False)

    @property
    def database_path(self) -> Path:
        """Return the resolved path of the local database file."""

        return self._database_path

    async def initialize(self) -> None:
        """Create the current schema safely when it does not already exist."""

        async with self.open() as connection:
            await connection.execute(_CREATE_IDENTITIES_TABLE)
            await connection.execute(_CREATE_AUTH_SESSIONS_TABLE)
            await connection.execute(_CREATE_AUDIT_RECORDS_TABLE)
            await connection.execute(_CREATE_SESSIONS_TABLE)
            await connection.execute(_CREATE_WORKFLOW_SESSIONS_TABLE)
            await connection.execute(_CREATE_WORKFLOW_SESSIONS_OWNER_INDEX)
            # Older local databases predate the client session key; add the
            # column in place so an existing development install keeps its data.
            cursor = await connection.execute("PRAGMA table_info(workflow_sessions)")
            session_columns = {row[1] for row in await cursor.fetchall()}
            if session_columns and "client_session_id" not in session_columns:
                await connection.execute(
                    "ALTER TABLE workflow_sessions ADD COLUMN client_session_id TEXT"
                )
            await connection.execute(_CREATE_WORKFLOW_SESSIONS_CLIENT_INDEX)
            await connection.execute(_CREATE_WORKFLOW_RUNS_TABLE)
            cursor = await connection.execute("PRAGMA table_info(workflow_runs)")
            run_columns = {row[1] for row in await cursor.fetchall()}
            for name, definition in (
                ("execution_lease_expires_at", "TEXT"),
                ("interrupted_at", "TEXT"),
                ("retryable", "INTEGER NOT NULL DEFAULT 0 CHECK(retryable IN (0,1))"),
            ):
                if name not in run_columns:
                    await connection.execute(
                        f"ALTER TABLE workflow_runs ADD COLUMN {name} {definition}"
                    )
            await connection.execute(_CREATE_WORKFLOW_RUNS_CURRENT_INDEX)
            await self._reconcile_legacy_nonterminal_runs(connection)
            await connection.execute(_CREATE_WORKFLOW_RUNS_ONE_NONTERMINAL_INDEX)
            await self._migrate_legacy_activity_events(connection)
            await connection.execute(_CREATE_ACTIVITY_EVENTS_TABLE)
            await connection.execute(_CREATE_ACTIVITY_EVENTS_REPLAY_INDEX)
            await connection.execute(_CREATE_WORKFLOW_MESSAGES_TABLE)
            # Older local databases predate the client idempotency key; add the
            # column in place so an existing development install keeps its data.
            cursor = await connection.execute("PRAGMA table_info(workflow_messages)")
            message_columns = {row[1] for row in await cursor.fetchall()}
            if message_columns and "client_message_id" not in message_columns:
                await connection.execute(
                    "ALTER TABLE workflow_messages ADD COLUMN client_message_id TEXT"
                )
            await connection.execute(_CREATE_WORKFLOW_MESSAGES_SESSION_INDEX)
            await connection.execute(_CREATE_WORKFLOW_MESSAGES_CLIENT_INDEX)
            await connection.execute(_CREATE_WORKFLOW_UPLOADS_TABLE)
            await self._migrate_legacy_workflow_uploads(connection)
            await connection.execute(_CREATE_WORKFLOW_UPLOADS_SESSION_INDEX)
            await connection.execute(_CREATE_WORKFLOW_RUN_ADMISSIONS_TABLE)
            await connection.execute(_CREATE_WORKFLOW_RUN_INPUTS_TABLE)
            await connection.execute(_CREATE_EXTRACTION_FINDINGS_TABLE)
            await connection.execute(_CREATE_RETRIEVED_EVIDENCE_TABLE)
            await connection.execute(_CREATE_KNOWLEDGE_SOURCES_TABLE)
            await connection.execute(_CREATE_GROUNDED_DRAFTS_TABLE)
            await connection.execute(_CREATE_GROUNDED_DRAFTS_OWNER_INDEX)
            await connection.execute(_CREATE_APPROVALS_TABLE)
            await connection.execute(_CREATE_WORKFLOW_OUTPUTS_TABLE)
            await connection.execute(_CREATE_ARTIFACTS_TABLE)
            await connection.execute(_CREATE_ARTIFACTS_RUN_INDEX)

    @staticmethod
    async def _migrate_legacy_activity_events(
        connection: aiosqlite.Connection,
    ) -> None:
        """Upgrade the temporary Phase 3 event table to Backend 2's owned schema."""

        cursor = await connection.execute("PRAGMA table_info(activity_events)")
        columns = {row[1] for row in await cursor.fetchall()}
        if not columns or {"owner_user_id", "payload_json"}.issubset(columns):
            return
        legacy_columns = {
            "session_id",
            "event_id",
            "workflow_run_id",
            "event_type",
            "occurred_at",
            "payload",
        }
        if not legacy_columns.issubset(columns):
            raise RuntimeError("unsupported activity event schema")

        await connection.execute(
            "ALTER TABLE activity_events RENAME TO activity_events_phase3_legacy"
        )
        await connection.execute(_CREATE_ACTIVITY_EVENTS_TABLE)
        cursor = await connection.execute(
            """SELECT legacy.session_id, legacy.event_id, sessions.owner_user_id,
            legacy.workflow_run_id, legacy.event_type, legacy.occurred_at, legacy.payload
            FROM activity_events_phase3_legacy AS legacy
            JOIN workflow_sessions AS sessions ON sessions.session_id = legacy.session_id
            ORDER BY legacy.session_id, legacy.event_id"""
        )
        rows = await cursor.fetchall()
        offset = 1 if any(int(row["event_id"]) == 0 for row in rows) else 0
        for row in rows:
            event_type = ActivityEventType(row["event_type"])
            raw_payload = json.loads(row["payload"])
            if not isinstance(raw_payload, dict):
                raise RuntimeError("legacy activity payload must be a JSON object")
            if event_type is ActivityEventType.SESSION_CREATED:
                payload = {}
            elif event_type is ActivityEventType.UPLOAD_ACCEPTED:
                payload = {
                    key: raw_payload[key]
                    for key in ("uploadId", "sourceId", "fileName", "sizeBytes")
                }
            else:
                payload = raw_payload
            event = ActivityEvent(
                event_id=int(row["event_id"]) + offset,
                session_id=row["session_id"],
                workflow_run_id=row["workflow_run_id"],
                event_type=event_type,
                occurred_at=row["occurred_at"],
                payload=payload,
            )
            await connection.execute(
                """INSERT INTO activity_events (
                    session_id, event_id, owner_user_id, workflow_run_id,
                    event_type, occurred_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(event.session_id),
                    event.event_id,
                    row["owner_user_id"],
                    str(event.workflow_run_id) if event.workflow_run_id is not None else None,
                    event.event_type.value,
                    event.occurred_at.isoformat(),
                    json.dumps(
                        event.payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
            )
        await connection.execute("DROP TABLE activity_events_phase3_legacy")

    @staticmethod
    async def _reconcile_legacy_nonterminal_runs(
        connection: aiosqlite.Connection,
    ) -> None:
        """Mark excess nonterminal runs terminal before installing the unique index."""

        nonterminal = "('queued', 'active', 'waitingForApproval')"
        await connection.execute(
            f"""UPDATE workflow_runs SET status = 'failed', stage = 'failed',
                stage_version = stage_version + 1, updated_at = ?
            WHERE workflow_run_id IN (
                SELECT r.workflow_run_id FROM workflow_runs r
                JOIN (
                    SELECT session_id, MAX(sequence) AS max_seq
                    FROM workflow_runs
                    WHERE status IN {nonterminal}
                    GROUP BY session_id
                    HAVING COUNT(*) > 1
                ) dupes ON r.session_id = dupes.session_id
                AND r.status IN {nonterminal}
                AND r.sequence < dupes.max_seq
            )""",
            (datetime.now(UTC).isoformat(),),
        )

    @staticmethod
    async def _migrate_legacy_workflow_uploads(
        connection: aiosqlite.Connection,
    ) -> None:
        """Rebuild pre-validation upload metadata without dropping local records."""

        cursor = await connection.execute(
            """SELECT sql FROM sqlite_master
            WHERE type = 'table' AND name = 'workflow_uploads'"""
        )
        row = await cursor.fetchone()
        schema = row["sql"] if row is not None else ""
        if (
            "stored_file_name TEXT NOT NULL CHECK" in schema
            and "mime_type TEXT NOT NULL CHECK" in schema
        ):
            return

        await connection.execute("ALTER TABLE workflow_uploads RENAME TO workflow_uploads_legacy")
        await connection.execute("DROP INDEX IF EXISTS workflow_uploads_session_created")
        await connection.execute(_CREATE_WORKFLOW_UPLOADS_TABLE)
        await connection.execute(
            """INSERT INTO workflow_uploads
            (upload_id, session_id, source_id, stored_file_name, file_name,
             mime_type, size_bytes, sha256, created_at)
            SELECT upload_id, session_id, source_id, stored_file_name, file_name,
                   mime_type, size_bytes, sha256, created_at
            FROM workflow_uploads_legacy"""
        )
        await connection.execute("DROP TABLE workflow_uploads_legacy")

    def _prepare_secure_paths(self) -> None:
        """Create and restrict the database directory and file before opening SQLite."""

        parent = self._database_path.parent
        parent.mkdir(parents=True, exist_ok=True, mode=self._DIRECTORY_MODE)
        os.chmod(parent, self._DIRECTORY_MODE)

        if self._database_path.is_symlink():
            raise ValueError("database path must not be a symbolic link")
        if self._database_path.exists() and not self._database_path.is_file():
            raise ValueError("database path must name a file")
        try:
            descriptor = os.open(
                self._database_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                self._DATABASE_MODE,
            )
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        os.chmod(self._database_path, self._DATABASE_MODE)

    @asynccontextmanager
    async def open(self) -> AsyncIterator[aiosqlite.Connection]:
        """Open a configured local connection with transaction handling."""

        self._prepare_secure_paths()
        connection = await aiosqlite.connect(self._database_path)
        os.chmod(self._database_path, self._DATABASE_MODE)
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


class SQLiteIdentityStore:
    """Read local pre-seeded identities without exposing write operations to auth."""

    def __init__(self, database: LocalSQLiteDatabase) -> None:
        self._database = database

    async def get_by_username(self, username: str) -> StoredIdentity | None:
        async with self._database.open() as connection:
            cursor = await connection.execute(
                """SELECT user_id, username, display_name, role, password_hash, disabled
                FROM identities WHERE username = ?""",
                (username,),
            )
            row = await cursor.fetchone()
        return self._identity_from_row(row) if row is not None else None

    async def get_by_id(self, user_id: UUID) -> StoredIdentity | None:
        async with self._database.open() as connection:
            cursor = await connection.execute(
                """SELECT user_id, username, display_name, role, password_hash, disabled
                FROM identities WHERE user_id = ?""",
                (str(user_id),),
            )
            row = await cursor.fetchone()
        return self._identity_from_row(row) if row is not None else None

    @staticmethod
    def _identity_from_row(row: aiosqlite.Row) -> StoredIdentity:
        return StoredIdentity(
            user_id=row["user_id"],
            username=row["username"],
            display_name=row["display_name"],
            role=UserRole(row["role"]),
            password_hash=row["password_hash"],
            disabled=bool(row["disabled"]),
        )


class SQLiteAuthSessionStore:
    """Persist revocable JWT metadata with atomic active-session revocation."""

    def __init__(self, database: LocalSQLiteDatabase) -> None:
        self._database = database

    async def create(self, record: AuthSessionRecord) -> None:
        async with self._database.open() as connection:
            await connection.execute(
                """INSERT INTO auth_sessions
                (auth_session_id, user_id, token_id, expires_at, revoked_at)
                VALUES (?, ?, ?, ?, ?)""",
                (
                    str(record.auth_session_id),
                    str(record.user_id),
                    str(record.token_id),
                    record.expires_at.isoformat(),
                    record.revoked_at.isoformat() if record.revoked_at is not None else None,
                ),
            )

    async def get_active(self, token_id: UUID, now: datetime) -> AuthSessionRecord | None:
        async with self._database.open() as connection:
            cursor = await connection.execute(
                """SELECT auth_session_id, user_id, token_id, expires_at, revoked_at
                FROM auth_sessions
                WHERE token_id = ? AND revoked_at IS NULL AND expires_at > ?""",
                (str(token_id), now.astimezone(UTC).isoformat()),
            )
            row = await cursor.fetchone()
        return self._record_from_row(row) if row is not None else None

    async def revoke(self, token_id: UUID, revoked_at: datetime) -> bool:
        async with self._database.open() as connection:
            cursor = await connection.execute(
                """UPDATE auth_sessions SET revoked_at = ?
                WHERE token_id = ? AND revoked_at IS NULL AND expires_at > ?""",
                (
                    revoked_at.astimezone(UTC).isoformat(),
                    str(token_id),
                    revoked_at.astimezone(UTC).isoformat(),
                ),
            )
            return cursor.rowcount == 1

    @staticmethod
    def _record_from_row(row: aiosqlite.Row) -> AuthSessionRecord:
        return AuthSessionRecord(
            auth_session_id=row["auth_session_id"],
            user_id=row["user_id"],
            token_id=row["token_id"],
            expires_at=row["expires_at"],
            revoked_at=row["revoked_at"],
        )


class SQLiteAuditStore:
    """Append sanitized audit metadata without retaining credentials or tokens."""

    def __init__(self, database: LocalSQLiteDatabase) -> None:
        self._database = database

    async def append(self, record: AuditRecord) -> None:
        async with self._database.open() as connection:
            await connection.execute(
                """INSERT INTO audit_records
                (audit_id, action, actor_user_id, session_id, workflow_run_id, outcome, occurred_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(record.audit_id),
                    record.action.value,
                    str(record.actor_user_id) if record.actor_user_id is not None else None,
                    str(record.session_id) if record.session_id is not None else None,
                    str(record.workflow_run_id) if record.workflow_run_id is not None else None,
                    record.outcome,
                    record.occurred_at.isoformat(),
                ),
            )


class SQLiteApprovalStore:
    """Persist approval intent, resolution, and sanitized execution results."""

    def __init__(self, database: LocalSQLiteDatabase) -> None:
        self._database = database

    async def get_for_run(
        self, *, session_id: UUID, workflow_run_id: UUID, owner_user_id: UUID
    ) -> Approval | None:
        async with self._database.open() as connection:
            row = await (
                await connection.execute(
                    f"""SELECT {_APPROVAL_COLUMNS} FROM approvals
                    WHERE session_id = ? AND workflow_run_id = ? AND owner_user_id = ?
                    ORDER BY requested_at DESC LIMIT 1""",
                    (str(session_id), str(workflow_run_id), str(owner_user_id)),
                )
            ).fetchone()
        return self._approval_from_row(row) if row is not None else None

    async def create_pending(self, approval: Approval) -> Approval:
        """Persist one validated pending approval without changing its intent."""

        if (
            approval.status is not ApprovalStatus.PENDING
            or approval.execution_status is not ExecutionStatus.NOT_STARTED
        ):
            raise ValueError("create_pending requires a pending, unclaimed approval")

        normalized_arguments = json.dumps(
            approval.normalized_arguments,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        async with self._database.open() as connection:
            await connection.execute(
                """INSERT INTO approvals (
                    approval_id, session_id, workflow_run_id, owner_user_id,
                    workflow_type, stage, stage_version, tool_name,
                    normalized_arguments, arguments_hash, status, requested_at,
                    resolved_at, resolved_by_user_id, decision, comment,
                    execution_status, execution_claim_token, execution_result
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(approval.approval_id),
                    str(approval.session_id),
                    str(approval.workflow_run_id),
                    str(approval.owner_user_id),
                    approval.workflow_type.value,
                    approval.stage.value,
                    approval.stage_version,
                    approval.tool_name,
                    normalized_arguments,
                    approval.arguments_hash,
                    approval.status.value,
                    approval.requested_at.isoformat(),
                    None,
                    None,
                    None,
                    None,
                    approval.execution_status.value,
                    None,
                    None,
                ),
            )
        return approval

    async def resolve_pending_approval(
        self,
        *,
        approval_id: UUID,
        session_id: UUID,
        workflow_run_id: UUID,
        owner_user_id: UUID,
        expected_stage: WorkflowStage,
        expected_stage_version: int,
        decision: ApprovalDecision,
        resolved_at: UtcTimestamp,
        comment: str | None,
    ) -> ApprovalResolution | None:
        """Atomically resolve a matching pending approval once."""

        resolved_timestamp = self._utc_isoformat(resolved_at, "resolved_at")
        normalized_comment = self._normalize_comment(comment)
        status = (
            ApprovalStatus.APPROVED
            if decision is ApprovalDecision.APPROVED
            else ApprovalStatus.REJECTED
        )
        execution_status = (
            ExecutionStatus.NOT_STARTED
            if decision is ApprovalDecision.APPROVED
            else ExecutionStatus.NOT_APPLICABLE
        )
        identity = (
            str(approval_id),
            str(session_id),
            str(workflow_run_id),
            str(owner_user_id),
            expected_stage.value,
            expected_stage_version,
        )

        async with self._database.open() as connection:
            cursor = await connection.execute(
                """UPDATE approvals
                SET status = ?, resolved_at = ?, resolved_by_user_id = ?,
                    decision = ?, comment = ?, execution_status = ?
                WHERE approval_id = ? AND session_id = ? AND workflow_run_id = ?
                    AND owner_user_id = ? AND stage = ? AND stage_version = ?
                    AND status = 'pending'""",
                (
                    status.value,
                    resolved_timestamp,
                    str(owner_user_id),
                    decision.value,
                    normalized_comment,
                    execution_status.value,
                    *identity,
                ),
            )
            resolved_now = cursor.rowcount == 1
            row = await self._select_matching_approval(connection, identity)
            approval = self._approval_from_row(row) if row is not None else None

        if approval is None:
            return None
        return ApprovalResolution(approval=approval, resolved_now=resolved_now)

    async def claim_execution(
        self,
        *,
        approval_id: UUID,
        session_id: UUID,
        workflow_run_id: UUID,
        owner_user_id: UUID,
        workflow_type: WorkflowType,
        expected_stage: WorkflowStage,
        expected_stage_version: int,
        tool_name: str,
        arguments_hash: str,
    ) -> ApprovalExecutionClaim | None:
        """Atomically reserve one matching approved intent for execution."""

        identity = (
            str(approval_id),
            str(session_id),
            str(workflow_run_id),
            str(owner_user_id),
            workflow_type.value,
            expected_stage.value,
            expected_stage_version,
            tool_name,
            arguments_hash,
        )
        claim_token = str(uuid4())
        async with self._database.open() as connection:
            cursor = await connection.execute(
                """UPDATE approvals
                SET execution_status = 'queued', execution_claim_token = ?
                WHERE approval_id = ? AND session_id = ? AND workflow_run_id = ?
                    AND owner_user_id = ? AND workflow_type = ? AND stage = ?
                    AND stage_version = ? AND tool_name = ? AND arguments_hash = ?
                    AND status = 'approved' AND decision = 'approved'
                    AND execution_status = 'notStarted'
                    AND execution_result IS NULL""",
                (claim_token, *identity),
            )
            claimed_now = cursor.rowcount == 1
            select_cursor = await connection.execute(
                f"""SELECT {_APPROVAL_COLUMNS} FROM approvals
                WHERE approval_id = ? AND session_id = ? AND workflow_run_id = ?
                    AND owner_user_id = ? AND workflow_type = ? AND stage = ?
                    AND stage_version = ? AND tool_name = ? AND arguments_hash = ?
                    AND status = 'approved' AND decision = 'approved'""",
                identity,
            )
            row = await select_cursor.fetchone()
            approval = self._approval_from_row(row) if row is not None else None

        if approval is None:
            return None
        return ApprovalExecutionClaim(
            approval=approval,
            claimed_now=claimed_now,
            execution_claim_token=UUID(claim_token) if claimed_now else None,
        )

    async def record_execution_result(
        self,
        *,
        approval_id: UUID,
        execution_claim_token: UUID,
        result: ToolExecutionResult,
    ) -> Approval | None:
        """Attach one typed result only to a matching queued approval."""

        serialized_result = json.dumps(
            result.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        async with self._database.open() as connection:
            cursor = await connection.execute(
                """UPDATE approvals
                SET execution_status = ?, execution_result = ?
                WHERE approval_id = ? AND execution_claim_token = ? AND tool_name = ?
                    AND status = 'approved' AND decision = 'approved'
                    AND execution_status = 'queued' AND execution_result IS NULL""",
                (
                    result.status.value,
                    serialized_result,
                    str(approval_id),
                    str(execution_claim_token),
                    result.tool_name.value,
                ),
            )
            if cursor.rowcount != 1:
                return None
            select_cursor = await connection.execute(
                f"SELECT {_APPROVAL_COLUMNS} FROM approvals WHERE approval_id = ?",
                (str(approval_id),),
            )
            row = await select_cursor.fetchone()
            approval = self._approval_from_row(row) if row is not None else None

        return approval

    async def get_execution_result(
        self,
        *,
        approval_id: UUID,
        session_id: UUID,
        workflow_run_id: UUID,
        owner_user_id: UUID,
    ) -> ToolExecutionResult | None:
        """Return a durable typed result only for the matching approval owner."""

        async with self._database.open() as connection:
            cursor = await connection.execute(
                """SELECT execution_result FROM approvals
                WHERE approval_id = ? AND session_id = ? AND workflow_run_id = ?
                    AND owner_user_id = ? AND execution_result IS NOT NULL""",
                (
                    str(approval_id),
                    str(session_id),
                    str(workflow_run_id),
                    str(owner_user_id),
                ),
            )
            row = await cursor.fetchone()

        if row is None:
            return None
        return _TOOL_EXECUTION_RESULT_ADAPTER.validate_json(row["execution_result"])

    @staticmethod
    async def _select_matching_approval(
        connection: aiosqlite.Connection,
        identity: tuple[str, str, str, str, str, int],
    ) -> aiosqlite.Row | None:
        cursor = await connection.execute(
            f"""SELECT {_APPROVAL_COLUMNS} FROM approvals
            WHERE approval_id = ? AND session_id = ? AND workflow_run_id = ?
                AND owner_user_id = ? AND stage = ? AND stage_version = ?""",
            identity,
        )
        return await cursor.fetchone()

    @staticmethod
    def _approval_from_row(row: aiosqlite.Row) -> Approval:
        return Approval(
            approval_id=row["approval_id"],
            session_id=row["session_id"],
            workflow_run_id=row["workflow_run_id"],
            owner_user_id=row["owner_user_id"],
            workflow_type=row["workflow_type"],
            stage=row["stage"],
            stage_version=row["stage_version"],
            tool_name=row["tool_name"],
            normalized_arguments=json.loads(row["normalized_arguments"]),
            arguments_hash=row["arguments_hash"],
            status=row["status"],
            requested_at=row["requested_at"],
            resolved_at=row["resolved_at"],
            resolved_by_user_id=row["resolved_by_user_id"],
            decision=row["decision"],
            comment=row["comment"],
            execution_status=row["execution_status"],
        )

    @staticmethod
    def _normalize_comment(comment: str | None) -> str | None:
        if comment is None:
            return None
        normalized = comment.strip()
        if not normalized or len(normalized) > 1000:
            raise ValueError("comment must contain between 1 and 1000 characters")
        return normalized

    @staticmethod
    def _utc_isoformat(value: datetime, field_name: str) -> str:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError(f"{field_name} must be timezone-aware UTC")
        return value.astimezone(UTC).isoformat()


class SQLiteArtifactStore:
    """Persist local artifact metadata authorized by the winning export claim."""

    def __init__(self, database: LocalSQLiteDatabase) -> None:
        self._database = database

    async def create(
        self,
        artifact: StoredArtifact,
        *,
        execution_claim_token: UUID,
    ) -> StoredArtifact:
        """Insert metadata only when the exact approved export claim authorizes it."""

        await self.create_many((artifact,), execution_claim_token=execution_claim_token)
        return artifact

    async def create_many(
        self,
        artifacts: tuple[StoredArtifact, ...],
        *,
        execution_claim_token: UUID,
    ) -> tuple[StoredArtifact, ...]:
        """Insert one export's artifact metadata in a single transaction."""

        if not artifacts:
            raise ValueError("at least one artifact is required")
        first = artifacts[0]
        if any(
            (
                artifact.approval_id,
                artifact.session_id,
                artifact.workflow_run_id,
                artifact.owner_user_id,
                artifact.draft_id,
            )
            != (
                first.approval_id,
                first.session_id,
                first.workflow_run_id,
                first.owner_user_id,
                first.draft_id,
            )
            for artifact in artifacts[1:]
        ):
            raise ArtifactContextMismatchError("artifact batch has mixed provenance")

        approval_identity = (
            str(first.approval_id),
            str(first.session_id),
            str(first.workflow_run_id),
            str(first.owner_user_id),
            str(execution_claim_token),
        )
        try:
            async with self._database.open() as connection:
                arguments_cursor = await connection.execute(
                    """SELECT approval.normalized_arguments
                    FROM approvals AS approval
                    JOIN workflow_sessions AS workflow_session
                      ON workflow_session.session_id = approval.session_id
                    WHERE approval.approval_id = ?
                      AND approval.session_id = ?
                      AND approval.workflow_run_id = ?
                      AND approval.owner_user_id = ?
                      AND approval.execution_claim_token = ?
                      AND workflow_session.owner_user_id = approval.owner_user_id
                      AND approval.status = 'approved'
                      AND approval.decision = 'approved'
                      AND approval.execution_status = 'queued'
                      AND approval.tool_name = 'request_document_export'""",
                    approval_identity,
                )
                arguments_row = await arguments_cursor.fetchone()
                if arguments_row is None:
                    raise ArtifactContextMismatchError(
                        "artifact is not authorized by the matching execution claim"
                    )

                try:
                    arguments = DocumentExportArguments.model_validate_json(
                        arguments_row["normalized_arguments"]
                    )
                except ValidationError as error:
                    raise ArtifactContextMismatchError(
                        "approved document-export arguments are invalid"
                    ) from error
                for artifact in artifacts:
                    if (
                        arguments.draft_id != artifact.draft_id
                        or artifact.format not in arguments.formats
                    ):
                        raise ArtifactContextMismatchError(
                            "artifact draft or format does not match the approved arguments"
                        )

                    cursor = await connection.execute(
                        """INSERT INTO artifacts (
                        artifact_id, session_id, workflow_run_id, owner_user_id,
                        approval_id, draft_id, format, file_name, size_bytes,
                        sha256, created_at
                    )
                    SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    FROM approvals AS approval
                    JOIN workflow_sessions AS workflow_session
                      ON workflow_session.session_id = approval.session_id
                    WHERE approval.approval_id = ?
                      AND approval.session_id = ?
                      AND approval.workflow_run_id = ?
                      AND approval.owner_user_id = ?
                      AND approval.execution_claim_token = ?
                      AND workflow_session.owner_user_id = approval.owner_user_id
                      AND approval.status = 'approved'
                      AND approval.decision = 'approved'
                      AND approval.execution_status = 'queued'
                      AND approval.tool_name = 'request_document_export'
                      AND approval.normalized_arguments = ?""",
                        (
                            str(artifact.artifact_id),
                            str(artifact.session_id),
                            str(artifact.workflow_run_id),
                            str(artifact.owner_user_id),
                            str(artifact.approval_id),
                            str(artifact.draft_id),
                            artifact.format.value,
                            artifact.file_name,
                            artifact.size_bytes,
                            artifact.sha256,
                            artifact.created_at.isoformat(),
                            *approval_identity,
                            arguments_row["normalized_arguments"],
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise ArtifactContextMismatchError(
                            "artifact authorization changed before metadata was persisted"
                        )
        except aiosqlite.IntegrityError as error:
            if "UNIQUE constraint failed" in str(error):
                raise ArtifactAlreadyExistsError(
                    "artifact ID or session-local filename already exists"
                ) from error
            raise ArtifactContextMismatchError(
                "artifact metadata violates the local persistence constraints"
            ) from error
        return artifacts

    async def get(
        self,
        *,
        artifact_id: UUID,
        session_id: UUID,
        workflow_run_id: UUID,
        owner_user_id: UUID,
    ) -> StoredArtifact | None:
        """Return metadata only for its exact artifact and ownership context."""

        async with self._database.open() as connection:
            cursor = await connection.execute(
                """SELECT artifact_id, session_id, workflow_run_id, owner_user_id,
                          approval_id, draft_id, format, file_name, size_bytes,
                          sha256, created_at
                FROM artifacts
                WHERE artifact_id = ? AND session_id = ? AND workflow_run_id = ?
                  AND owner_user_id = ?""",
                (
                    str(artifact_id),
                    str(session_id),
                    str(workflow_run_id),
                    str(owner_user_id),
                ),
            )
            row = await cursor.fetchone()
        return self._artifact_from_row(row) if row is not None else None

    async def list_for_run(
        self,
        *,
        session_id: UUID,
        workflow_run_id: UUID,
        owner_user_id: UUID,
    ) -> list[StoredArtifact]:
        """List one owner's run artifacts in deterministic creation order."""

        async with self._database.open() as connection:
            cursor = await connection.execute(
                """SELECT artifact_id, session_id, workflow_run_id, owner_user_id,
                          approval_id, draft_id, format, file_name, size_bytes,
                          sha256, created_at
                FROM artifacts
                WHERE session_id = ? AND workflow_run_id = ? AND owner_user_id = ?
                ORDER BY created_at ASC, artifact_id ASC""",
                (str(session_id), str(workflow_run_id), str(owner_user_id)),
            )
            rows = await cursor.fetchall()
        return [self._artifact_from_row(row) for row in rows]

    @staticmethod
    def _artifact_from_row(row: aiosqlite.Row) -> StoredArtifact:
        return StoredArtifact(
            artifact_id=row["artifact_id"],
            session_id=row["session_id"],
            workflow_run_id=row["workflow_run_id"],
            owner_user_id=row["owner_user_id"],
            approval_id=row["approval_id"],
            draft_id=row["draft_id"],
            format=row["format"],
            file_name=row["file_name"],
            size_bytes=row["size_bytes"],
            sha256=row["sha256"],
            created_at=row["created_at"],
        )


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
            raise InvalidSessionStatusError(f"Session status must be one of: {allowed}") from error

    @staticmethod
    def _session_from_row(row: aiosqlite.Row) -> SessionMetadata:
        return SessionMetadata(
            session_id=row["session_id"],
            user_id=row["user_id"],
            created_at=row["created_at"],
            status=row["status"],
        )


class WorkflowSessionNotFoundError(LookupError):
    """Raised when no workflow session matches the identifier for its owner."""


class WorkflowRunAlreadyExistsError(RuntimeError):
    """Raised when a workflow run identifier has already been persisted."""


class WorkflowRunContextMismatchError(PermissionError):
    """Raised when a workflow run does not match its parent session context."""


class WorkflowAdmissionConflictError(RuntimeError):
    """An admission key or active-run slot conflicts with different work."""


class WorkflowResultConflictError(RuntimeError):
    """An immutable workflow checkpoint already contains different content."""


class WorkflowApprovalConflictError(RuntimeError):
    """Pending approval preparation conflicts with durable output state."""


class SQLiteWorkflowStore:
    """Durable workflow sessions, runs, and messages for the local service."""

    _MAX_LISTED_SESSIONS = 100
    _MAX_LISTED_MESSAGES = 200

    def __init__(self, database: LocalSQLiteDatabase) -> None:
        self._database = database

    async def admit_run(self, request: WorkflowRunAdmissionRequest) -> WorkflowRunAdmission:
        """Atomically create or replay a run, message, inputs, and accepted event."""

        self._validate_admission_request(request)
        semantic_hash = self._admission_hash(request)
        try:
            async with self._database.open() as connection:
                await connection.execute("BEGIN IMMEDIATE")
                replay = await self._load_admission(
                    connection,
                    session_id=request.run.session_id,
                    client_message_id=request.message.client_message_id,
                    semantic_hash=semantic_hash,
                    status=WorkflowAdmissionStatus.REPLAYED,
                )
                if replay is not None:
                    return replay
                await self._require_admission_context(connection, request)
                await self._insert_run(connection, request.run)
                await self._insert_message(connection, request.message)
                for ordinal, upload in enumerate(request.selected_uploads):
                    await connection.execute(
                        """INSERT INTO workflow_run_inputs (
                            workflow_run_id, ordinal, upload_id, source_id,
                            file_name, mime_type, size_bytes, sha256
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            str(request.run.workflow_run_id),
                            ordinal,
                            str(upload.upload_id),
                            str(upload.source_id),
                            upload.file_name,
                            upload.mime_type,
                            upload.size_bytes,
                            upload.sha256,
                        ),
                    )
                await connection.execute(
                    """INSERT INTO workflow_run_admissions (
                        session_id, client_message_id, workflow_run_id, message_id,
                        owner_user_id, semantic_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(request.run.session_id),
                        str(request.message.client_message_id),
                        str(request.run.workflow_run_id),
                        str(request.message.message_id),
                        str(request.run.owner_user_id),
                        semantic_hash,
                        request.run.created_at.isoformat(),
                    ),
                )
                event = await _insert_activity_event(
                    connection,
                    ActivityEvent(
                        event_id=0,
                        session_id=request.run.session_id,
                        workflow_run_id=request.run.workflow_run_id,
                        event_type=ActivityEventType.MESSAGE_ACCEPTED,
                        occurred_at=request.message.created_at,
                        payload={"messageId": str(request.message.message_id)},
                    ),
                    owner_user_id=request.run.owner_user_id,
                )
                await self._update_projection(connection, request.run)
                return WorkflowRunAdmission(
                    status=WorkflowAdmissionStatus.CREATED,
                    run=request.run,
                    message=request.message,
                    selected_uploads=request.selected_uploads,
                    accepted_event=event,
                )
        except aiosqlite.IntegrityError as error:
            async with self._database.open() as connection:
                replay = await self._load_admission(
                    connection,
                    session_id=request.run.session_id,
                    client_message_id=request.message.client_message_id,
                    semantic_hash=semantic_hash,
                    status=WorkflowAdmissionStatus.REPLAYED,
                )
            if replay is not None:
                return replay
            raise WorkflowAdmissionConflictError(
                "session already has different nonterminal work"
            ) from error

    async def get_admission(
        self, *, workflow_run_id: UUID
    ) -> WorkflowRunAdmission | None:
        """Restore one committed admission with its original user message."""

        async with self._database.open() as connection:
            row = await (
                await connection.execute(
                    """SELECT session_id, client_message_id, semantic_hash
                    FROM workflow_run_admissions WHERE workflow_run_id = ?""",
                    (str(workflow_run_id),),
                )
            ).fetchone()
            if row is None:
                return None
            return await self._load_admission(
                connection,
                session_id=UUID(row["session_id"]),
                client_message_id=UUID(row["client_message_id"]),
                semantic_hash=row["semantic_hash"],
                status=WorkflowAdmissionStatus.CREATED,
            )

    @staticmethod
    def _validate_admission_request(request: WorkflowRunAdmissionRequest) -> None:
        run, message = request.run, request.message
        if message.client_message_id is None or message.role != "user":
            raise ValueError("admission requires an idempotent user message")
        if (
            message.session_id != run.session_id
            or message.author_user_id != run.owner_user_id
            or run.stage is not WorkflowStage.COLLECTING_INPUTS
            or run.status is not WorkflowRunStatus.QUEUED
            or any(
                upload.session_id != run.session_id or upload.owner_user_id != run.owner_user_id
                for upload in request.selected_uploads
            )
        ):
            raise WorkflowRunContextMismatchError("admission context is inconsistent")
        upload_ids = tuple(item.upload_id for item in request.selected_uploads)
        if len(upload_ids) != len(set(upload_ids)):
            raise ValueError("selected uploads must be unique")

    @staticmethod
    def _admission_hash(request: WorkflowRunAdmissionRequest) -> str:
        semantic = {
            "sessionId": str(request.run.session_id),
            "ownerUserId": str(request.run.owner_user_id),
            "workflowType": request.run.workflow_type.value,
            "message": request.message.content.strip(),
            "uploads": [
                upload.model_dump(mode="json", by_alias=True) for upload in request.selected_uploads
            ],
        }
        encoded = json.dumps(
            semantic, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    async def _require_admission_context(
        connection: aiosqlite.Connection, request: WorkflowRunAdmissionRequest
    ) -> None:
        row = await (
            await connection.execute(
                """SELECT workflow_type, status FROM workflow_sessions
                WHERE session_id = ? AND owner_user_id = ?""",
                (str(request.run.session_id), str(request.run.owner_user_id)),
            )
        ).fetchone()
        if (
            row is None
            or row["workflow_type"] != request.run.workflow_type.value
            or row["status"] != WorkflowStatus.ACTIVE.value
        ):
            raise WorkflowRunContextMismatchError("admission requires an active owned session")
        for upload in request.selected_uploads:
            stored = await (
                await connection.execute(
                    """SELECT u.source_id, u.file_name, u.mime_type, u.size_bytes,
                              u.sha256, s.owner_user_id
                    FROM workflow_uploads AS u
                    JOIN workflow_sessions AS s ON s.session_id = u.session_id
                    WHERE u.upload_id = ? AND u.session_id = ?""",
                    (str(upload.upload_id), str(upload.session_id)),
                )
            ).fetchone()
            expected = (
                str(upload.source_id),
                upload.file_name,
                upload.mime_type,
                upload.size_bytes,
                upload.sha256,
                str(upload.owner_user_id),
            )
            if stored is None or tuple(stored) != expected:
                raise WorkflowRunContextMismatchError("selected upload metadata changed")

    @staticmethod
    async def _insert_run(connection: aiosqlite.Connection, run: WorkflowRun) -> None:
        await connection.execute(
            """INSERT INTO workflow_runs (
                workflow_run_id, session_id, owner_user_id, workflow_type,
                stage, stage_version, status, sandbox_attempts, created_at,
                updated_at, execution_lease_expires_at, interrupted_at, retryable
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                run.execution_lease_expires_at.isoformat()
                if run.execution_lease_expires_at
                else None,
                run.interrupted_at.isoformat() if run.interrupted_at else None,
                int(run.retryable),
            ),
        )

    @staticmethod
    async def _insert_message(connection: aiosqlite.Connection, message: WorkflowMessage) -> None:
        await connection.execute(
            """INSERT INTO workflow_messages (
                message_id, session_id, author_user_id, role, content,
                created_at, client_message_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                str(message.message_id),
                str(message.session_id),
                str(message.author_user_id) if message.author_user_id else None,
                message.role,
                message.content.strip(),
                message.created_at.isoformat(),
                str(message.client_message_id),
            ),
        )

    async def _load_admission(
        self,
        connection: aiosqlite.Connection,
        *,
        session_id: UUID,
        client_message_id: UUID | None,
        semantic_hash: str,
        status: WorkflowAdmissionStatus,
    ) -> WorkflowRunAdmission | None:
        if client_message_id is None:
            return None
        row = await (
            await connection.execute(
                f"""SELECT r.{_WORKFLOW_RUN_COLUMNS.replace(", ", ", r.")},
                    a.semantic_hash, m.message_id, m.author_user_id, m.role,
                    m.content, m.created_at AS message_created_at,
                    m.client_message_id
                FROM workflow_run_admissions AS a
                JOIN workflow_runs AS r ON r.workflow_run_id = a.workflow_run_id
                JOIN workflow_messages AS m ON m.message_id = a.message_id
                WHERE a.session_id = ? AND a.client_message_id = ?""",
                (str(session_id), str(client_message_id)),
            )
        ).fetchone()
        if row is None:
            return None
        if row["semantic_hash"] != semantic_hash:
            raise WorkflowAdmissionConflictError("admission idempotency key conflicts")
        run = self._workflow_run_from_row(row)
        message = WorkflowMessage(
            message_id=row["message_id"],
            session_id=run.session_id,
            author_user_id=row["author_user_id"],
            role=row["role"],
            content=row["content"],
            created_at=row["message_created_at"],
            client_message_id=row["client_message_id"],
        )
        input_rows = await (
            await connection.execute(
                """SELECT i.*, r.session_id, r.owner_user_id
                FROM workflow_run_inputs AS i JOIN workflow_runs AS r USING(workflow_run_id)
                WHERE i.workflow_run_id = ? ORDER BY i.ordinal""",
                (str(run.workflow_run_id),),
            )
        ).fetchall()
        uploads = tuple(
            SelectedUploadSnapshot(
                upload_id=item["upload_id"],
                session_id=item["session_id"],
                owner_user_id=item["owner_user_id"],
                source_id=item["source_id"],
                file_name=item["file_name"],
                mime_type=item["mime_type"],
                size_bytes=item["size_bytes"],
                sha256=item["sha256"],
            )
            for item in input_rows
        )
        event_row = await (
            await connection.execute(
                """SELECT * FROM activity_events WHERE session_id = ?
                AND workflow_run_id = ? AND event_type = 'message.accepted'""",
                (str(run.session_id), str(run.workflow_run_id)),
            )
        ).fetchone()
        if event_row is None:
            raise RuntimeError("durable admission is missing its accepted event")
        return WorkflowRunAdmission(
            status=status,
            run=run,
            message=message,
            selected_uploads=uploads,
            accepted_event=SQLiteActivityEventStore._event_from_row(event_row),
        )

    @staticmethod
    async def _update_projection(connection: aiosqlite.Connection, run: WorkflowRun) -> None:
        cursor = await connection.execute(
            """UPDATE workflow_sessions SET stage = ?, status = ?, updated_at = ?
            WHERE session_id = ? AND owner_user_id = ? AND workflow_type = ?""",
            (
                run.stage.value,
                SQLiteWorkflowStore._session_status(run.status).value,
                run.updated_at.isoformat(),
                str(run.session_id),
                str(run.owner_user_id),
                run.workflow_type.value,
            ),
        )
        if cursor.rowcount != 1:
            raise WorkflowRunContextMismatchError("session projection update failed")

    async def save_findings(self, result: StoredExtractionFindings) -> StoredExtractionFindings:
        payload = self._canonical_models(result.findings)
        sources = json.dumps(
            [str(item) for item in result.source_upload_ids], separators=(",", ":")
        )
        await self._save_result(
            "extraction_findings",
            result.session_id,
            result.workflow_run_id,
            result.owner_user_id,
            payload,
            result.created_at,
            sources,
        )
        return result

    async def save_evidence(self, result: StoredRetrievedEvidence) -> StoredRetrievedEvidence:
        payload = self._canonical_models(result.evidence)
        await self._save_result(
            "retrieved_evidence",
            result.session_id,
            result.workflow_run_id,
            result.owner_user_id,
            payload,
            result.created_at,
        )
        return result

    async def _save_result(
        self,
        table: str,
        session_id: UUID,
        workflow_run_id: UUID,
        owner_user_id: UUID,
        payload: str,
        created_at: UtcTimestamp,
        sources: str | None = None,
    ) -> None:
        if len(payload.encode()) > 1024 * 1024:
            raise ValueError("workflow result exceeds 1 MiB")
        async with self._database.open() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            row = await (
                await connection.execute(
                    f"SELECT * FROM {table} WHERE workflow_run_id = ?",
                    (str(workflow_run_id),),
                )
            ).fetchone()
            if row is not None:
                field = "findings_json" if table == "extraction_findings" else "evidence_json"
                same = (
                    row["session_id"] == str(session_id)
                    and row["owner_user_id"] == str(owner_user_id)
                    and row[field] == payload
                    and row["created_at"] == created_at.isoformat()
                    and (sources is None or row["source_upload_ids_json"] == sources)
                )
                if not same:
                    raise WorkflowResultConflictError("immutable workflow result conflicts")
                return
            owned = await (
                await connection.execute(
                    """SELECT 1 FROM workflow_runs WHERE workflow_run_id = ?
                    AND session_id = ? AND owner_user_id = ?""",
                    (str(workflow_run_id), str(session_id), str(owner_user_id)),
                )
            ).fetchone()
            if owned is None:
                raise WorkflowRunContextMismatchError("result does not match an owned run")
            if table == "extraction_findings":
                await connection.execute(
                    "INSERT INTO extraction_findings VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(workflow_run_id),
                        str(session_id),
                        str(owner_user_id),
                        sources,
                        payload,
                        created_at.isoformat(),
                    ),
                )
            else:
                await connection.execute(
                    "INSERT INTO retrieved_evidence VALUES (?, ?, ?, ?, ?)",
                    (
                        str(workflow_run_id),
                        str(session_id),
                        str(owner_user_id),
                        payload,
                        created_at.isoformat(),
                    ),
                )

    async def get_findings(
        self, *, session_id: UUID, workflow_run_id: UUID, owner_user_id: UUID
    ) -> StoredExtractionFindings | None:
        row = await self._get_result(
            "extraction_findings", session_id, workflow_run_id, owner_user_id
        )
        if row is None:
            return None
        from app.ai.schemas import Finding

        return StoredExtractionFindings(
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            owner_user_id=owner_user_id,
            source_upload_ids=tuple(
                UUID(item) for item in json.loads(row["source_upload_ids_json"])
            ),
            findings=tuple(
                Finding.model_validate_json(json.dumps(item), strict=True)
                for item in json.loads(row["findings_json"])
            ),
            created_at=row["created_at"],
        )

    async def get_evidence(
        self, *, session_id: UUID, workflow_run_id: UUID, owner_user_id: UUID
    ) -> StoredRetrievedEvidence | None:
        row = await self._get_result(
            "retrieved_evidence", session_id, workflow_run_id, owner_user_id
        )
        if row is None:
            return None
        from app.ai.schemas import EvidenceChunk

        return StoredRetrievedEvidence(
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            owner_user_id=owner_user_id,
            evidence=tuple(
                EvidenceChunk.model_validate_json(json.dumps(item), strict=True)
                for item in json.loads(row["evidence_json"])
            ),
            created_at=row["created_at"],
        )

    async def _get_result(
        self, table: str, session_id: UUID, workflow_run_id: UUID, owner_user_id: UUID
    ) -> aiosqlite.Row | None:
        async with self._database.open() as connection:
            return await (
                await connection.execute(
                    f"""SELECT * FROM {table} WHERE workflow_run_id = ?
                    AND session_id = ? AND owner_user_id = ?""",
                    (str(workflow_run_id), str(session_id), str(owner_user_id)),
                )
            ).fetchone()

    @staticmethod
    def _canonical_models(values: tuple[BaseModel, ...]) -> str:
        return json.dumps(
            [item.model_dump(mode="json", by_alias=True) for item in values],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

    async def prepare_pending_approval(
        self,
        *,
        run: WorkflowRun,
        approval: Approval,
        output: ValidatedToolCall,
    ) -> PendingApprovalPreparation:
        """Persist output, approval, transition, projection, and event atomically."""

        normalized = output.arguments.model_dump(mode="json", by_alias=True)
        normalized_json = json.dumps(
            normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        computed_hash = hashlib.sha256(normalized_json.encode()).hexdigest()
        if (
            run.status is not WorkflowRunStatus.ACTIVE
            or approval.session_id != run.session_id
            or approval.workflow_run_id != run.workflow_run_id
            or approval.owner_user_id != run.owner_user_id
            or approval.workflow_type is not run.workflow_type
            or approval.stage is not WorkflowStage.AWAITING_APPROVAL
            or approval.stage_version != run.stage_version + 1
            or approval.tool_name != output.tool_name.value
            or approval.normalized_arguments != normalized
            or approval.arguments_hash != computed_hash
            or approval.status is not ApprovalStatus.PENDING
        ):
            raise WorkflowApprovalConflictError("pending approval context is inconsistent")
        try:
            async with self._database.open() as connection:
                await connection.execute("BEGIN IMMEDIATE")
                existing = await self._load_prepared_approval(
                    connection, run, approval, output, normalized_json, computed_hash
                )
                if existing is not None:
                    return existing
                current_row = await (
                    await connection.execute(
                        f"""SELECT {_WORKFLOW_RUN_COLUMNS} FROM workflow_runs
                        WHERE workflow_run_id = ? AND session_id = ? AND owner_user_id = ?
                        AND stage = ? AND stage_version = ? AND status = 'active'""",
                        (
                            str(run.workflow_run_id),
                            str(run.session_id),
                            str(run.owner_user_id),
                            run.stage.value,
                            run.stage_version,
                        ),
                    )
                ).fetchone()
                if current_row is None:
                    raise WorkflowApprovalConflictError("workflow state changed")
                if output.tool_name.value == "request_document_export":
                    draft_id = normalized.get("draftId")
                    draft = await (
                        await connection.execute(
                            """SELECT 1 FROM grounded_drafts WHERE draft_id = ?
                            AND workflow_run_id = ? AND session_id = ? AND owner_user_id = ?""",
                            (
                                draft_id,
                                str(run.workflow_run_id),
                                str(run.session_id),
                                str(run.owner_user_id),
                            ),
                        )
                    ).fetchone()
                    if draft is None:
                        raise WorkflowApprovalConflictError("approved draft is not durable")
                await connection.execute(
                    """INSERT INTO approvals (
                        approval_id, session_id, workflow_run_id, owner_user_id,
                        workflow_type, stage, stage_version, tool_name,
                        normalized_arguments, arguments_hash, status, requested_at,
                        execution_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, 'notStarted')""",
                    (
                        str(approval.approval_id),
                        str(approval.session_id),
                        str(approval.workflow_run_id),
                        str(approval.owner_user_id),
                        approval.workflow_type.value,
                        approval.stage.value,
                        approval.stage_version,
                        approval.tool_name,
                        normalized_json,
                        computed_hash,
                        approval.requested_at.isoformat(),
                    ),
                )
                await connection.execute(
                    """INSERT INTO workflow_outputs VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(run.workflow_run_id),
                        str(run.session_id),
                        str(run.owner_user_id),
                        str(approval.approval_id),
                        output.tool_name.value,
                        normalized_json,
                        computed_hash,
                        approval.requested_at.isoformat(),
                    ),
                )
                next_run = run.model_copy(
                    update={
                        "stage": WorkflowStage.AWAITING_APPROVAL,
                        "stage_version": run.stage_version + 1,
                        "status": WorkflowRunStatus.WAITING_FOR_APPROVAL,
                        "updated_at": approval.requested_at,
                        "execution_lease_expires_at": None,
                    }
                )
                cursor = await connection.execute(
                    """UPDATE workflow_runs SET stage = ?, stage_version = ?, status = ?,
                        updated_at = ?, execution_lease_expires_at = NULL
                    WHERE workflow_run_id = ? AND stage = ? AND stage_version = ?
                    AND status = 'active'""",
                    (
                        next_run.stage.value,
                        next_run.stage_version,
                        next_run.status.value,
                        next_run.updated_at.isoformat(),
                        str(run.workflow_run_id),
                        run.stage.value,
                        run.stage_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise WorkflowApprovalConflictError("workflow transition lost its race")
                await self._update_projection(connection, next_run)
                event = await _insert_activity_event(
                    connection,
                    ActivityEvent(
                        event_id=0,
                        session_id=run.session_id,
                        workflow_run_id=run.workflow_run_id,
                        event_type=ActivityEventType.APPROVAL_REQUIRED,
                        occurred_at=approval.requested_at,
                        payload={"approvalId": str(approval.approval_id)},
                    ),
                    owner_user_id=run.owner_user_id,
                )
                return PendingApprovalPreparation(
                    run=next_run,
                    approval=approval,
                    output=output,
                    required_event=event,
                    created_now=True,
                )
        except aiosqlite.IntegrityError as error:
            async with self._database.open() as connection:
                existing = await self._load_prepared_approval(
                    connection, run, approval, output, normalized_json, computed_hash
                )
            if existing is not None:
                return existing
            raise WorkflowApprovalConflictError("pending approval conflicts") from error

    async def _load_prepared_approval(
        self,
        connection: aiosqlite.Connection,
        run: WorkflowRun,
        approval: Approval,
        output: ValidatedToolCall,
        normalized_json: str,
        computed_hash: str,
    ) -> PendingApprovalPreparation | None:
        row = await (
            await connection.execute(
                """SELECT a.*, o.tool_name AS output_tool,
                    o.normalized_arguments AS output_arguments,
                    o.arguments_hash AS output_hash
                FROM workflow_outputs AS o JOIN approvals AS a USING(approval_id)
                WHERE o.workflow_run_id = ?""",
                (str(run.workflow_run_id),),
            )
        ).fetchone()
        if row is None:
            return None
        stored_approval = SQLiteApprovalStore._approval_from_row(row)
        same = (
            stored_approval.session_id == approval.session_id
            and stored_approval.workflow_run_id == approval.workflow_run_id
            and stored_approval.owner_user_id == approval.owner_user_id
            and stored_approval.workflow_type is approval.workflow_type
            and stored_approval.stage is approval.stage
            and stored_approval.stage_version == approval.stage_version
            and stored_approval.status is ApprovalStatus.PENDING
            and row["output_tool"] == output.tool_name.value
            and row["output_arguments"] == normalized_json
            and row["output_hash"] == computed_hash
        )
        if not same:
            raise WorkflowApprovalConflictError("durable approval output conflicts")
        current = await (
            await connection.execute(
                f"SELECT {_WORKFLOW_RUN_COLUMNS} FROM workflow_runs WHERE workflow_run_id = ?",
                (str(run.workflow_run_id),),
            )
        ).fetchone()
        event_row = await (
            await connection.execute(
                """SELECT * FROM activity_events WHERE workflow_run_id = ?
                AND event_type = 'approval.required'""",
                (str(run.workflow_run_id),),
            )
        ).fetchone()
        if current is None or event_row is None:
            raise WorkflowApprovalConflictError("prepared approval is incomplete")
        return PendingApprovalPreparation(
            run=self._workflow_run_from_row(current),
            approval=stored_approval,
            output=output,
            required_event=SQLiteActivityEventStore._event_from_row(event_row),
            created_now=False,
        )

    async def create_session(self, session: WorkflowSession) -> WorkflowSession:
        """Insert one owned workflow session.

        A client session id makes creation idempotent: a retry of the same
        request returns the already stored session and writes nothing. Two
        concurrent creates with one key resolve through the unique index, the
        losing insert replaying the winning row. Without the key the first
        insert wins and duplicates are rejected.
        """

        try:
            async with self._database.open() as connection:
                cursor = await connection.execute(
                    """
                    INSERT INTO workflow_sessions
                    (session_id, owner_user_id, workflow_type, title, stage,
                     status, created_at, updated_at, client_session_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (client_session_id) DO NOTHING
                    """,
                    (
                        str(session.session_id),
                        str(session.owner_user_id),
                        session.workflow_type.value,
                        session.title,
                        session.stage.value,
                        session.status.value,
                        session.created_at.isoformat(),
                        session.updated_at.isoformat(),
                        str(session.client_session_id)
                        if session.client_session_id is not None
                        else None,
                    ),
                )
                if cursor.rowcount == 0:
                    # Only a non-null client key can conflict with the index.
                    key = session.client_session_id
                    if key is None:
                        raise RuntimeError("Idempotent session create could not be resolved")
                    stored = await self._stored_client_session(
                        connection, key, session.owner_user_id
                    )
                    if stored is None:
                        raise SessionAlreadyExistsError(
                            f"Workflow session already exists: {session.session_id}"
                        )
                    return stored
        except aiosqlite.IntegrityError as error:
            raise SessionAlreadyExistsError(
                f"Workflow session already exists: {session.session_id}"
            ) from error
        return session

    async def create_run(self, run: WorkflowRun) -> WorkflowRun:
        """Persist a run and its current-session projection in one transaction."""

        try:
            async with self._database.open() as connection:
                cursor = await connection.execute(
                    """INSERT INTO workflow_runs (
                        workflow_run_id, session_id, owner_user_id, workflow_type,
                        stage, stage_version, status, sandbox_attempts,
                        created_at, updated_at, execution_lease_expires_at,
                        interrupted_at, retryable
                    )
                    SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    FROM workflow_sessions
                    WHERE session_id = ? AND owner_user_id = ? AND workflow_type = ?""",
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
                        run.execution_lease_expires_at.isoformat()
                        if run.execution_lease_expires_at is not None
                        else None,
                        run.interrupted_at.isoformat() if run.interrupted_at is not None else None,
                        int(run.retryable),
                        str(run.session_id),
                        str(run.owner_user_id),
                        run.workflow_type.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise WorkflowRunContextMismatchError(
                        "workflow run does not match an existing owned session"
                    )
                projection_cursor = await connection.execute(
                    """UPDATE workflow_sessions
                    SET stage = ?, status = ?, updated_at = ?
                    WHERE session_id = ? AND owner_user_id = ? AND workflow_type = ?""",
                    (
                        run.stage.value,
                        self._session_status(run.status).value,
                        run.updated_at.isoformat(),
                        str(run.session_id),
                        str(run.owner_user_id),
                        run.workflow_type.value,
                    ),
                )
                if projection_cursor.rowcount != 1:
                    raise WorkflowRunContextMismatchError(
                        "workflow session projection could not be updated"
                    )
        except aiosqlite.IntegrityError as error:
            if "UNIQUE constraint failed" in str(error):
                raise WorkflowRunAlreadyExistsError(
                    f"Workflow run already exists: {run.workflow_run_id}"
                ) from error
            raise WorkflowRunContextMismatchError(
                "workflow run violates the local persistence constraints"
            ) from error
        return run

    async def get_run(
        self,
        *,
        workflow_run_id: UUID,
        session_id: UUID,
        owner_user_id: UUID,
    ) -> WorkflowRun | None:
        """Restore a run only for its exact session and owner."""

        async with self._database.open() as connection:
            cursor = await connection.execute(
                f"""SELECT {_WORKFLOW_RUN_COLUMNS}
                FROM workflow_runs
                WHERE workflow_run_id = ? AND session_id = ? AND owner_user_id = ?""",
                (str(workflow_run_id), str(session_id), str(owner_user_id)),
            )
            row = await cursor.fetchone()
        return self._workflow_run_from_row(row) if row is not None else None

    async def get_current_run(
        self,
        *,
        session_id: UUID,
        owner_user_id: UUID,
    ) -> WorkflowRun | None:
        """Restore the newest run for an owned session without exposing sequence."""

        async with self._database.open() as connection:
            cursor = await connection.execute(
                f"""SELECT {_WORKFLOW_RUN_COLUMNS}
                FROM workflow_runs
                WHERE session_id = ? AND owner_user_id = ?
                  AND EXISTS (
                      SELECT 1 FROM workflow_sessions
                      WHERE workflow_sessions.session_id = workflow_runs.session_id
                        AND workflow_sessions.owner_user_id = workflow_runs.owner_user_id
                  )
                ORDER BY sequence DESC
                LIMIT 1""",
                (str(session_id), str(owner_user_id)),
            )
            row = await cursor.fetchone()
        return self._workflow_run_from_row(row) if row is not None else None

    async def compare_and_set_stage(
        self,
        *,
        session_id: UUID,
        workflow_run_id: UUID,
        owner_user_id: UUID,
        expected_stage: WorkflowStage,
        expected_stage_version: int,
        next_stage: WorkflowStage,
        next_status: WorkflowRunStatus,
        sandbox_attempts: int,
        lease_expires_at: UtcTimestamp | None = None,
    ) -> WorkflowRun | None:
        """Atomically apply one Backend 1-selected transition to a matching run."""

        async with self._database.open() as connection:
            select_cursor = await connection.execute(
                f"""SELECT {_WORKFLOW_RUN_COLUMNS}
                FROM workflow_runs
                WHERE workflow_run_id = ? AND session_id = ? AND owner_user_id = ?
                  AND stage = ? AND stage_version = ?""",
                (
                    str(workflow_run_id),
                    str(session_id),
                    str(owner_user_id),
                    expected_stage.value,
                    expected_stage_version,
                ),
            )
            row = await select_cursor.fetchone()
            if row is None:
                return None

            current = self._workflow_run_from_row(row)
            updated_at = self._next_updated_at(current.updated_at)
            updated = WorkflowRun.model_validate(
                {
                    **current.model_dump(),
                    "stage": next_stage,
                    "stage_version": expected_stage_version + 1,
                    "status": next_status,
                    "sandbox_attempts": sandbox_attempts,
                    "updated_at": updated_at,
                    "execution_lease_expires_at": lease_expires_at,
                    "interrupted_at": None,
                    "retryable": False,
                }
            )
            update_cursor = await connection.execute(
                """UPDATE workflow_runs
                SET stage = ?, stage_version = ?, status = ?, sandbox_attempts = ?,
                    updated_at = ?, execution_lease_expires_at = ?,
                    interrupted_at = NULL, retryable = 0
                WHERE workflow_run_id = ? AND session_id = ? AND owner_user_id = ?
                  AND stage = ? AND stage_version = ?""",
                (
                    updated.stage.value,
                    updated.stage_version,
                    updated.status.value,
                    updated.sandbox_attempts,
                    updated.updated_at.isoformat(),
                    updated.execution_lease_expires_at.isoformat()
                    if updated.execution_lease_expires_at
                    else None,
                    str(workflow_run_id),
                    str(session_id),
                    str(owner_user_id),
                    expected_stage.value,
                    expected_stage_version,
                ),
            )
            if update_cursor.rowcount != 1:
                return None

            await connection.execute(
                """UPDATE workflow_sessions
                SET stage = ?, status = ?, updated_at = ?
                WHERE session_id = ? AND owner_user_id = ?
                  AND EXISTS (
                      SELECT 1 FROM workflow_runs AS transitioned
                      WHERE transitioned.workflow_run_id = ?
                        AND transitioned.session_id = workflow_sessions.session_id
                        AND transitioned.owner_user_id = workflow_sessions.owner_user_id
                        AND transitioned.sequence = (
                            SELECT MAX(current.sequence)
                            FROM workflow_runs AS current
                            WHERE current.session_id = workflow_sessions.session_id
                        )
                  )""",
                (
                    updated.stage.value,
                    self._session_status(updated.status).value,
                    updated.updated_at.isoformat(),
                    str(session_id),
                    str(owner_user_id),
                    str(workflow_run_id),
                ),
            )
        return updated

    async def _stored_client_session(
        self,
        connection: aiosqlite.Connection,
        client_session_id: UUID,
        owner_user_id: UUID,
    ) -> WorkflowSession | None:
        """Return the session another request stored under this key for this owner."""

        cursor = await connection.execute(
            """
            SELECT session_id, owner_user_id, workflow_type, title, stage,
                   status, created_at, updated_at, client_session_id
            FROM workflow_sessions
            WHERE client_session_id = ? AND owner_user_id = ?
            """,
            (str(client_session_id), str(owner_user_id)),
        )
        row = await cursor.fetchone()
        return self._workflow_session_from_row(row) if row is not None else None

    async def append_message(self, message: WorkflowMessage) -> WorkflowMessage:
        """Append one sanitized message and refresh its session's updated_at.

        A client message id makes the append idempotent: a retry of the same
        request returns the already stored message and writes nothing. Two
        concurrent appends with one key resolve through the unique index: the
        losing insert replays the winning row instead of surfacing an
        integrity error.
        """

        async with self._database.open() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO workflow_messages
                (message_id, session_id, author_user_id, role, content,
                 created_at, client_message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (session_id, client_message_id) DO NOTHING
                """,
                (
                    str(message.message_id),
                    str(message.session_id),
                    str(message.author_user_id) if message.author_user_id is not None else None,
                    message.role,
                    message.content,
                    message.created_at.isoformat(),
                    str(message.client_message_id)
                    if message.client_message_id is not None
                    else None,
                ),
            )
            if cursor.rowcount == 0:
                stored = await self._stored_idempotent_message(connection, message)
                if stored is None:
                    raise RuntimeError("Idempotent message append could not be resolved")
                return stored
            await connection.execute(
                """
                UPDATE workflow_sessions SET updated_at = ?
                WHERE session_id = ?
                """,
                (message.created_at.isoformat(), str(message.session_id)),
            )
        return message

    async def _stored_idempotent_message(
        self, connection: aiosqlite.Connection, message: WorkflowMessage
    ) -> WorkflowMessage | None:
        """Return the message another request already stored under this key."""

        cursor = await connection.execute(
            """
            SELECT message_id, session_id, author_user_id, role, content,
                   created_at, client_message_id
            FROM workflow_messages
            WHERE session_id = ? AND client_message_id = ?
            """,
            (str(message.session_id), str(message.client_message_id)),
        )
        row = await cursor.fetchone()
        return self._message_from_row(row) if row is not None else None

    async def list_sessions(self, owner_user_id: UUID) -> list[WorkflowSession]:
        """Return the owner's sessions, most recently updated first."""

        async with self._database.open() as connection:
            cursor = await connection.execute(
                """
                SELECT session_id, owner_user_id, workflow_type, title, stage,
                       status, created_at, updated_at, client_session_id
                FROM workflow_sessions
                WHERE owner_user_id = ?
                ORDER BY updated_at DESC, session_id DESC
                LIMIT ?
                """,
                (str(owner_user_id), self._MAX_LISTED_SESSIONS),
            )
            rows = await cursor.fetchall()
        return [self._workflow_session_from_row(row) for row in rows]

    async def get_session(self, session_id: UUID, owner_user_id: UUID) -> WorkflowSession:
        """Return one owned session or raise without revealing other owners' data."""

        async with self._database.open() as connection:
            cursor = await connection.execute(
                """
                SELECT session_id, owner_user_id, workflow_type, title, stage,
                       status, created_at, updated_at, client_session_id
                FROM workflow_sessions
                WHERE session_id = ? AND owner_user_id = ?
                """,
                (str(session_id), str(owner_user_id)),
            )
            row = await cursor.fetchone()
        if row is None:
            raise WorkflowSessionNotFoundError(f"Workflow session not found: {session_id}")
        return self._workflow_session_from_row(row)

    async def list_messages(self, session_id: UUID, owner_user_id: UUID) -> list[WorkflowMessage]:
        """Return the latest owned messages in durable chronological order."""

        async with self._database.open() as connection:
            cursor = await connection.execute(
                """
                SELECT messages.message_id, messages.session_id,
                       messages.author_user_id, messages.role, messages.content,
                       messages.created_at, messages.client_message_id
                FROM workflow_messages AS messages
                JOIN workflow_sessions AS sessions
                  ON sessions.session_id = messages.session_id
                WHERE messages.session_id = ? AND sessions.owner_user_id = ?
                ORDER BY messages.sequence DESC
                LIMIT ?
                """,
                (str(session_id), str(owner_user_id), self._MAX_LISTED_MESSAGES),
            )
            rows = list(await cursor.fetchall())
        return [self._message_from_row(row) for row in rows[::-1]]

    @staticmethod
    def _workflow_session_from_row(row: aiosqlite.Row) -> WorkflowSession:
        return WorkflowSession(
            session_id=UUID(row["session_id"]),
            owner_user_id=UUID(row["owner_user_id"]),
            workflow_type=WorkflowType(row["workflow_type"]),
            title=row["title"],
            stage=WorkflowStage(row["stage"]),
            status=WorkflowStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            client_session_id=(
                UUID(row["client_session_id"]) if row["client_session_id"] is not None else None
            ),
        )

    async def list_unfinished_runs(self) -> list[WorkflowRun]:
        """Return internal recovery candidates without exposing them through HTTP."""

        placeholders = ",".join("?" for _ in _NONTERMINAL_RUN_STATUSES)
        async with self._database.open() as connection:
            rows = await (
                await connection.execute(
                    f"""SELECT {_WORKFLOW_RUN_COLUMNS} FROM workflow_runs
                    WHERE status IN ({placeholders}) ORDER BY sequence""",
                    _NONTERMINAL_RUN_STATUSES,
                )
            ).fetchall()
        return [self._workflow_run_from_row(row) for row in rows]

    async def get_run_inputs(
        self, *, workflow_run_id: UUID
    ) -> tuple[SelectedUploadSnapshot, ...]:
        """Return persisted selected uploads for a workflow run."""

        async with self._database.open() as connection:
            rows = await (
                await connection.execute(
                    """SELECT upload_id, session_id, owner_user_id, source_id,
                    file_name, mime_type, size_bytes, sha256
                    FROM workflow_run_inputs
                    WHERE workflow_run_id = ? ORDER BY ordinal""",
                    (str(workflow_run_id),),
                )
            ).fetchall()
        return tuple(
            SelectedUploadSnapshot(
                upload_id=UUID(row["upload_id"]),
                session_id=UUID(row["session_id"]),
                owner_user_id=UUID(row["owner_user_id"]),
                source_id=UUID(row["source_id"]),
                file_name=row["file_name"],
                mime_type=row["mime_type"],
                size_bytes=row["size_bytes"],
                sha256=row["sha256"],
            )
            for row in rows
        )

    async def mark_stale_runs_interrupted(
        self, *, stale_before: UtcTimestamp, interrupted_at: UtcTimestamp
    ) -> list[WorkflowRun]:
        """Idempotently mark expired active operations retryable."""

        async with self._database.open() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            changed = await connection.execute(
                """UPDATE workflow_runs SET interrupted_at = ?, retryable = 1,
                    execution_lease_expires_at = NULL
                WHERE status = 'active' AND retryable = 0
                  AND execution_lease_expires_at IS NOT NULL
                  AND execution_lease_expires_at <= ?
                RETURNING workflow_run_id""",
                (interrupted_at.isoformat(), stale_before.isoformat()),
            )
            identifiers = [row["workflow_run_id"] for row in await changed.fetchall()]
            if not identifiers:
                return []
            placeholders = ",".join("?" for _ in identifiers)
            rows = await (
                await connection.execute(
                    f"""SELECT {_WORKFLOW_RUN_COLUMNS} FROM workflow_runs
                    WHERE workflow_run_id IN ({placeholders}) ORDER BY sequence""",
                    identifiers,
                )
            ).fetchall()
        return [self._workflow_run_from_row(row) for row in rows]

    async def claim_retry(
        self, *, workflow_run_id: UUID, expected_stage_version: int, lease_expires_at: UtcTimestamp
    ) -> WorkflowRun | None:
        """Claim one interrupted run using its existing stage-version CAS."""

        async with self._database.open() as connection:
            cursor = await connection.execute(
                """UPDATE workflow_runs SET retryable = 0, interrupted_at = NULL,
                    execution_lease_expires_at = ?, stage_version = stage_version + 1,
                    updated_at = ?
                WHERE workflow_run_id = ? AND stage_version = ?
                  AND status = 'active' AND retryable = 1""",
                (
                    lease_expires_at.isoformat(),
                    datetime.now(UTC).isoformat(),
                    str(workflow_run_id),
                    expected_stage_version,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = await (
                await connection.execute(
                    f"SELECT {_WORKFLOW_RUN_COLUMNS} FROM workflow_runs WHERE workflow_run_id = ?",
                    (str(workflow_run_id),),
                )
            ).fetchone()
        return self._workflow_run_from_row(row) if row is not None else None

    @staticmethod
    def _workflow_run_from_row(row: aiosqlite.Row) -> WorkflowRun:
        return WorkflowRun(
            workflow_run_id=UUID(row["workflow_run_id"]),
            session_id=UUID(row["session_id"]),
            owner_user_id=UUID(row["owner_user_id"]),
            workflow_type=WorkflowType(row["workflow_type"]),
            stage=WorkflowStage(row["stage"]),
            stage_version=row["stage_version"],
            status=WorkflowRunStatus(row["status"]),
            sandbox_attempts=row["sandbox_attempts"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            execution_lease_expires_at=row["execution_lease_expires_at"],
            interrupted_at=row["interrupted_at"],
            retryable=bool(row["retryable"]),
        )

    @staticmethod
    def _session_status(status: WorkflowRunStatus) -> WorkflowStatus:
        return {
            WorkflowRunStatus.QUEUED: WorkflowStatus.ACTIVE,
            WorkflowRunStatus.ACTIVE: WorkflowStatus.ACTIVE,
            WorkflowRunStatus.WAITING_FOR_APPROVAL: WorkflowStatus.ACTIVE,
            WorkflowRunStatus.COMPLETED: WorkflowStatus.COMPLETED,
            WorkflowRunStatus.FAILED: WorkflowStatus.FAILED,
            WorkflowRunStatus.APPROVAL_REJECTED: WorkflowStatus.APPROVAL_REJECTED,
        }[status]

    @staticmethod
    def _next_updated_at(current: datetime) -> datetime:
        now = datetime.now(UTC)
        return now if now > current else current + timedelta(microseconds=1)

    @staticmethod
    def _message_from_row(row: aiosqlite.Row) -> WorkflowMessage:
        return WorkflowMessage(
            message_id=UUID(row["message_id"]),
            session_id=UUID(row["session_id"]),
            author_user_id=(
                UUID(row["author_user_id"]) if row["author_user_id"] is not None else None
            ),
            role=row["role"],
            content=row["content"],
            created_at=row["created_at"],
            client_message_id=(
                UUID(row["client_message_id"]) if row["client_message_id"] is not None else None
            ),
        )


class SQLiteActivityEventStore:
    """Persist sanitized activity events and poll SQLite for live delivery."""

    _DEFAULT_POLL_INTERVAL_SECONDS = 0.1
    _MIN_POLL_INTERVAL_SECONDS = 0.01
    _MAX_POLL_INTERVAL_SECONDS = 5.0
    _DEFAULT_BATCH_SIZE = 100
    _MAX_BATCH_SIZE = 1000

    def __init__(
        self,
        database: LocalSQLiteDatabase,
        *,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
        batch_size: int = _DEFAULT_BATCH_SIZE,
    ) -> None:
        if (
            isinstance(poll_interval_seconds, bool)
            or not isinstance(poll_interval_seconds, (int, float))
            or not math.isfinite(poll_interval_seconds)
            or not self._MIN_POLL_INTERVAL_SECONDS
            <= poll_interval_seconds
            <= self._MAX_POLL_INTERVAL_SECONDS
        ):
            raise ValueError("poll interval must be between 0.01 and 5 seconds")
        if (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or not 1 <= batch_size <= self._MAX_BATCH_SIZE
        ):
            raise ValueError("batch size must be between 1 and 1000")
        self._database = database
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._batch_size = batch_size

    async def append(
        self,
        event: ActivityEvent,
        *,
        owner_user_id: UUID,
    ) -> ActivityEvent:
        """Atomically assign and persist the next ID for an owned session."""

        if event.event_id != 0:
            raise ValueError("new activity events must use event_id=0")
        async with self._database.open() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            await self._require_owned_session(
                connection,
                session_id=event.session_id,
                owner_user_id=owner_user_id,
            )
            if event.workflow_run_id is not None:
                await self._require_owned_run(
                    connection,
                    workflow_run_id=event.workflow_run_id,
                    session_id=event.session_id,
                    owner_user_id=owner_user_id,
                )

            persisted = await _insert_activity_event(connection, event, owner_user_id=owner_user_id)
        return persisted

    async def replay(
        self,
        *,
        session_id: UUID,
        owner_user_id: UUID,
        after_event_id: int,
    ) -> list[ActivityEvent]:
        """Return one bounded page after an exclusive per-session cursor."""

        self._validate_after_event_id(after_event_id)
        async with self._database.open() as connection:
            await self._require_owned_session(
                connection,
                session_id=session_id,
                owner_user_id=owner_user_id,
            )
            rows = await self._read_rows_after(
                connection,
                session_id=session_id,
                owner_user_id=owner_user_id,
                after_event_id=after_event_id,
                batch_size=self._batch_size,
            )
        return [self._event_from_row(row) for row in rows]

    def subscribe(
        self,
        *,
        session_id: UUID,
        owner_user_id: UUID,
        after_event_id: int,
    ) -> AsyncIterator[ActivityEvent]:
        """Poll SQLite without retaining a connection while waiting or yielding."""

        self._validate_after_event_id(after_event_id)

        async def iterate() -> AsyncGenerator[ActivityEvent, None]:
            cursor = after_event_id
            async with self._database.open() as connection:
                await self._require_owned_session(
                    connection,
                    session_id=session_id,
                    owner_user_id=owner_user_id,
                )

            while True:
                events = await self._poll_after(
                    session_id=session_id,
                    owner_user_id=owner_user_id,
                    after_event_id=cursor,
                )
                if not events:
                    await asyncio.sleep(self._poll_interval_seconds)
                    continue
                for event in events:
                    yield event
                    cursor = event.event_id

        return iterate()

    async def _poll_after(
        self,
        *,
        session_id: UUID,
        owner_user_id: UUID,
        after_event_id: int,
    ) -> list[ActivityEvent]:
        async with self._database.open() as connection:
            rows = await self._read_rows_after(
                connection,
                session_id=session_id,
                owner_user_id=owner_user_id,
                after_event_id=after_event_id,
                batch_size=self._batch_size,
            )
        return [self._event_from_row(row) for row in rows]

    @staticmethod
    async def _read_rows_after(
        connection: aiosqlite.Connection,
        *,
        session_id: UUID,
        owner_user_id: UUID,
        after_event_id: int,
        batch_size: int,
    ) -> list[aiosqlite.Row]:
        cursor = await connection.execute(
            f"""SELECT {_ACTIVITY_EVENT_COLUMNS}
            FROM activity_events
            WHERE session_id = ? AND owner_user_id = ? AND event_id > ?
            ORDER BY event_id ASC
            LIMIT ?""",
            (str(session_id), str(owner_user_id), after_event_id, batch_size),
        )
        return list(await cursor.fetchall())

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
            raise ActivityEventContextMismatchError(
                "activity event does not match an existing owned workflow context"
            )

    @staticmethod
    async def _require_owned_run(
        connection: aiosqlite.Connection,
        *,
        workflow_run_id: UUID,
        session_id: UUID,
        owner_user_id: UUID,
    ) -> None:
        cursor = await connection.execute(
            """SELECT 1 FROM workflow_runs
            WHERE workflow_run_id = ? AND session_id = ? AND owner_user_id = ?""",
            (str(workflow_run_id), str(session_id), str(owner_user_id)),
        )
        if await cursor.fetchone() is None:
            raise ActivityEventContextMismatchError(
                "activity event does not match an existing owned workflow context"
            )

    @staticmethod
    def _serialize_payload(payload: object) -> str:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(serialized.encode("utf-8")) > 8 * 1024:
            raise ValueError("canonical activity payload must not exceed 8 KiB")
        return serialized

    @staticmethod
    def _validate_after_event_id(after_event_id: int) -> None:
        if isinstance(after_event_id, bool) or not isinstance(after_event_id, int):
            raise TypeError("after_event_id must be an integer")
        if after_event_id < 0:
            raise ValueError("after_event_id must be nonnegative")

    @staticmethod
    def _event_from_row(row: aiosqlite.Row) -> ActivityEvent:
        return ActivityEvent(
            event_id=row["event_id"],
            session_id=row["session_id"],
            workflow_run_id=row["workflow_run_id"],
            event_type=ActivityEventType(row["event_type"]),
            occurred_at=row["occurred_at"],
            payload=json.loads(row["payload_json"]),
        )
