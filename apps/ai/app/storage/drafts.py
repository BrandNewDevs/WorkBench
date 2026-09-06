"""Durable validated draft storage and claim-bound export resolution."""

import json
from uuid import UUID, uuid4

import aiosqlite
from pydantic import ValidationError

from app.ai.schemas import GroundedDraft
from app.ports.local_backend import StoredDraft
from app.storage.sqlite import LocalSQLiteDatabase
from app.tools.contracts import DocumentExportArguments, DocumentExportExecutionRequest, ToolName
from app.workflow.contracts import UtcTimestamp, WorkflowRun, WorkflowType

_MAX_DRAFT_BYTES = 1024 * 1024
_COLUMNS = "draft_id, session_id, workflow_run_id, owner_user_id, draft_json, created_at"


class DraftConflictError(RuntimeError):
    """A workflow run already has different final draft content."""


class DraftContextMismatchError(PermissionError):
    """Draft persistence or resolution context does not match durable state."""


class DraftIntegrityError(RuntimeError):
    """Stored draft JSON is malformed or no longer satisfies its schema."""


class SQLiteDraftStore:
    """Assign and persist one immutable final draft per inspection run."""

    def __init__(self, database: LocalSQLiteDatabase) -> None:
        self._database = database

    async def save(
        self, *, workflow_run: WorkflowRun, draft: GroundedDraft, created_at: UtcTimestamp
    ) -> StoredDraft:
        serialized = self._serialize(draft)
        async with self._database.open() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            await self._require_run(connection, workflow_run)
            cursor = await connection.execute(
                f"SELECT {_COLUMNS} FROM grounded_drafts WHERE workflow_run_id = ?",
                (str(workflow_run.workflow_run_id),),
            )
            row = await cursor.fetchone()
            if row is not None:
                stored = self._from_row(row)
                if stored.draft != draft or stored.created_at != created_at:
                    raise DraftConflictError("workflow run already has a different final draft")
                return stored
            stored = StoredDraft(
                draft_id=uuid4(),
                session_id=workflow_run.session_id,
                workflow_run_id=workflow_run.workflow_run_id,
                owner_user_id=workflow_run.owner_user_id,
                draft=draft,
                created_at=created_at,
            )
            await connection.execute(
                """INSERT INTO grounded_drafts
                (draft_id, session_id, workflow_run_id, owner_user_id, draft_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    str(stored.draft_id),
                    str(stored.session_id),
                    str(stored.workflow_run_id),
                    str(stored.owner_user_id),
                    serialized,
                    stored.created_at.isoformat(),
                ),
            )
        return stored

    async def get(
        self, *, draft_id: UUID, session_id: UUID, workflow_run_id: UUID, owner_user_id: UUID
    ) -> StoredDraft | None:
        async with self._database.open() as connection:
            row = await (
                await connection.execute(
                    f"""SELECT {_COLUMNS} FROM grounded_drafts
                    WHERE draft_id = ? AND session_id = ? AND workflow_run_id = ?
                      AND owner_user_id = ?""",
                    (str(draft_id), str(session_id), str(workflow_run_id), str(owner_user_id)),
                )
            ).fetchone()
        return self._from_row(row) if row is not None else None

    async def get_for_run(
        self, *, session_id: UUID, workflow_run_id: UUID, owner_user_id: UUID
    ) -> StoredDraft | None:
        """Restore the one application-owned draft for an owned workflow run."""

        async with self._database.open() as connection:
            row = await (
                await connection.execute(
                    f"""SELECT {_COLUMNS} FROM grounded_drafts
                    WHERE session_id = ? AND workflow_run_id = ? AND owner_user_id = ?""",
                    (str(session_id), str(workflow_run_id), str(owner_user_id)),
                )
            ).fetchone()
        return self._from_row(row) if row is not None else None

    async def resolve_for_export(
        self, request: DocumentExportExecutionRequest
    ) -> StoredDraft | None:
        async with self._database.open() as connection:
            row = await (
                await connection.execute(
                    f"""SELECT {", ".join(f"d.{name.strip()}" for name in _COLUMNS.split(","))},
                    a.normalized_arguments
                FROM grounded_drafts AS d
                JOIN approvals AS a ON a.approval_id = ?
                JOIN workflow_sessions AS s ON s.session_id = d.session_id
                WHERE d.draft_id = ? AND d.session_id = ? AND d.workflow_run_id = ?
                  AND a.session_id = d.session_id AND a.workflow_run_id = d.workflow_run_id
                  AND a.owner_user_id = d.owner_user_id AND s.owner_user_id = d.owner_user_id
                  AND a.execution_claim_token = ? AND a.status = 'approved'
                  AND a.decision = 'approved' AND a.execution_status = 'queued'
                  AND a.tool_name = ?""",
                    (
                        str(request.approval_id),
                        str(request.arguments.draft_id),
                        str(request.session_id),
                        str(request.workflow_run_id),
                        str(request.execution_claim_token),
                        ToolName.REQUEST_DOCUMENT_EXPORT.value,
                    ),
                )
            ).fetchone()
        if row is None:
            return None
        try:
            arguments = DocumentExportArguments.model_validate_json(row["normalized_arguments"])
        except ValidationError as error:
            raise DraftIntegrityError("approved export arguments are invalid") from error
        if arguments != request.arguments:
            return None
        return self._from_row(row, prefix="d.")

    @staticmethod
    async def _require_run(connection: aiosqlite.Connection, run: WorkflowRun) -> None:
        row = await (
            await connection.execute(
                """SELECT 1 FROM workflow_runs AS r JOIN workflow_sessions AS s USING (session_id)
            WHERE r.workflow_run_id = ? AND r.session_id = ? AND r.owner_user_id = ?
              AND r.workflow_type = ? AND s.owner_user_id = r.owner_user_id""",
                (
                    str(run.workflow_run_id),
                    str(run.session_id),
                    str(run.owner_user_id),
                    WorkflowType.INSPECTION_ANALYSIS.value,
                ),
            )
        ).fetchone()
        if row is None or run.workflow_type is not WorkflowType.INSPECTION_ANALYSIS:
            raise DraftContextMismatchError("draft does not match an inspection workflow run")

    @staticmethod
    def _serialize(draft: GroundedDraft) -> str:
        value = json.dumps(
            draft.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        if len(value.encode()) > _MAX_DRAFT_BYTES:
            raise ValueError("grounded draft exceeds the storage limit")
        return value

    @staticmethod
    def _from_row(row: aiosqlite.Row, prefix: str = "") -> StoredDraft:
        del prefix
        try:
            draft = GroundedDraft.model_validate_json(row["draft_json"], strict=True)
            return StoredDraft(
                draft_id=row["draft_id"],
                session_id=row["session_id"],
                workflow_run_id=row["workflow_run_id"],
                owner_user_id=row["owner_user_id"],
                draft=draft,
                created_at=row["created_at"],
            )
        except (ValidationError, ValueError, TypeError) as error:
            raise DraftIntegrityError("stored grounded draft is invalid") from error
