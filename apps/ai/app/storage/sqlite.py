"""Local SQLite foundation and Backend 2 metadata persistence."""

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import aiosqlite
from pydantic import BaseModel, ConfigDict, TypeAdapter, field_validator

from app.auth.contracts import UserRole
from app.ports.backend2 import (
    AuditRecord,
    AuthSessionRecord,
    StoredIdentity,
    WorkflowMessage,
)
from app.tools.contracts import ToolExecutionResult
from app.workflow.contracts import (
    Approval,
    ApprovalDecision,
    ApprovalExecutionClaim,
    ApprovalResolution,
    ApprovalStatus,
    ExecutionStatus,
    UtcTimestamp,
    WorkflowSession,
    WorkflowStage,
    WorkflowStatus,
    WorkflowType,
)

_TOOL_EXECUTION_RESULT_ADAPTER: TypeAdapter[ToolExecutionResult] = TypeAdapter(
    ToolExecutionResult
)

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
    updated_at TEXT NOT NULL
)
"""

_CREATE_WORKFLOW_SESSIONS_OWNER_INDEX = """
CREATE INDEX IF NOT EXISTS workflow_sessions_owner_updated
ON workflow_sessions (owner_user_id, updated_at DESC)
"""

_CREATE_WORKFLOW_MESSAGES_TABLE = """
CREATE TABLE IF NOT EXISTS workflow_messages (
    sequence INTEGER PRIMARY KEY,
    message_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL REFERENCES workflow_sessions(session_id),
    author_user_id TEXT,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

_CREATE_WORKFLOW_MESSAGES_SESSION_INDEX = """
CREATE INDEX IF NOT EXISTS workflow_messages_session_order
ON workflow_messages (session_id, sequence)
"""


class SessionAlreadyExistsError(RuntimeError):
    """Raised when session metadata already exists for a session identifier."""


class InvalidSessionStatusError(ValueError):
    """Raised when a caller supplies a status outside the canonical session states."""


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
            await connection.execute(_CREATE_WORKFLOW_MESSAGES_TABLE)
            await connection.execute(_CREATE_WORKFLOW_MESSAGES_SESSION_INDEX)
            await connection.execute(_CREATE_APPROVALS_TABLE)

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
            raise InvalidSessionStatusError(
                f"Session status must be one of: {allowed}"
            ) from error

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


class SQLiteWorkflowStore:
    """Durable workflow sessions and messages for the local chat API.

    Implements the create-session and append-message portions of the agreed
    ``WorkflowStore`` port, plus the owned read queries the chat routes need.
    """

    _MAX_LISTED_SESSIONS = 100
    _MAX_LISTED_MESSAGES = 200

    def __init__(self, database: LocalSQLiteDatabase) -> None:
        self._database = database

    async def create_session(self, session: WorkflowSession) -> WorkflowSession:
        """Insert one owned workflow session and reject duplicate identifiers."""

        try:
            async with self._database.open() as connection:
                await connection.execute(
                    """
                    INSERT INTO workflow_sessions
                    (session_id, owner_user_id, workflow_type, title, stage,
                     status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                    ),
                )
        except aiosqlite.IntegrityError as error:
            raise SessionAlreadyExistsError(
                f"Workflow session already exists: {session.session_id}"
            ) from error
        return session

    async def append_message(self, message: WorkflowMessage) -> WorkflowMessage:
        """Append one sanitized message and refresh its session's updated_at."""

        async with self._database.open() as connection:
            await connection.execute(
                """
                INSERT INTO workflow_messages
                (message_id, session_id, author_user_id, role, content, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(message.message_id),
                    str(message.session_id),
                    str(message.author_user_id)
                    if message.author_user_id is not None
                    else None,
                    message.role,
                    message.content,
                    message.created_at.isoformat(),
                ),
            )
            await connection.execute(
                """
                UPDATE workflow_sessions SET updated_at = ?
                WHERE session_id = ?
                """,
                (message.created_at.isoformat(), str(message.session_id)),
            )
        return message

    async def list_sessions(self, owner_user_id: UUID) -> list[WorkflowSession]:
        """Return the owner's sessions, most recently updated first."""

        async with self._database.open() as connection:
            cursor = await connection.execute(
                """
                SELECT session_id, owner_user_id, workflow_type, title, stage,
                       status, created_at, updated_at
                FROM workflow_sessions
                WHERE owner_user_id = ?
                ORDER BY updated_at DESC, session_id DESC
                LIMIT ?
                """,
                (str(owner_user_id), self._MAX_LISTED_SESSIONS),
            )
            rows = await cursor.fetchall()
        return [self._workflow_session_from_row(row) for row in rows]

    async def get_session(
        self, session_id: UUID, owner_user_id: UUID
    ) -> WorkflowSession:
        """Return one owned session or raise without revealing other owners' data."""

        async with self._database.open() as connection:
            cursor = await connection.execute(
                """
                SELECT session_id, owner_user_id, workflow_type, title, stage,
                       status, created_at, updated_at
                FROM workflow_sessions
                WHERE session_id = ? AND owner_user_id = ?
                """,
                (str(session_id), str(owner_user_id)),
            )
            row = await cursor.fetchone()
        if row is None:
            raise WorkflowSessionNotFoundError(
                f"Workflow session not found: {session_id}"
            )
        return self._workflow_session_from_row(row)

    async def list_messages(
        self, session_id: UUID, owner_user_id: UUID
    ) -> list[WorkflowMessage]:
        """Return the latest owned messages in durable chronological order."""

        async with self._database.open() as connection:
            cursor = await connection.execute(
                """
                SELECT messages.message_id, messages.session_id,
                       messages.author_user_id, messages.role, messages.content,
                       messages.created_at
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
        )

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
        )
