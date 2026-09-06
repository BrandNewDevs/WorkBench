"""Focused tests for claim-authorized SQLite artifact metadata persistence."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import aiosqlite
import pytest
from pydantic import ValidationError

from app.ports.local_backend import StoredArtifact
from app.storage import (
    ArtifactAlreadyExistsError,
    ArtifactContextMismatchError,
    LocalSQLiteDatabase,
    SQLiteApprovalStore,
    SQLiteArtifactStore,
    SQLiteWorkflowStore,
)
from app.tools.contracts import ArtifactFormat, DocumentExportArguments, ToolName
from app.tools.registry import argument_hash
from app.workflow.contracts import (
    Approval,
    ApprovalDecision,
    WorkflowSession,
    WorkflowStage,
    WorkflowStatus,
    WorkflowType,
)

_CREATED_AT = datetime(2026, 9, 6, 9, 0, tzinfo=UTC)


@dataclass(frozen=True)
class _ArtifactContext:
    database_path: Path
    database: LocalSQLiteDatabase
    approval_store: SQLiteApprovalStore
    artifact_store: SQLiteArtifactStore
    approval: Approval
    artifact: StoredArtifact
    execution_claim_token: UUID | None


async def _context(
    tmp_path: Path,
    *,
    formats: tuple[ArtifactFormat, ...] = (ArtifactFormat.DOCX,),
    decision: ApprovalDecision | None = ApprovalDecision.APPROVED,
    claim: bool = True,
) -> _ArtifactContext:
    database_path = tmp_path / "state" / "workbench.db"
    database = LocalSQLiteDatabase(database_path)
    await database.initialize()
    workflow_store = SQLiteWorkflowStore(database)
    approval_store = SQLiteApprovalStore(database)
    artifact_store = SQLiteArtifactStore(database)
    session_id = uuid4()
    workflow_run_id = uuid4()
    owner_user_id = uuid4()
    draft_id = uuid4()
    arguments = DocumentExportArguments(draft_id=draft_id, formats=formats)
    await workflow_store.create_session(
        WorkflowSession(
            session_id=session_id,
            owner_user_id=owner_user_id,
            workflow_type=WorkflowType.INSPECTION_ANALYSIS,
            title="Inspection draft",
            stage=WorkflowStage.AWAITING_APPROVAL,
            status=WorkflowStatus.ACTIVE,
            created_at=_CREATED_AT,
            updated_at=_CREATED_AT,
        )
    )
    approval = Approval(
        approval_id=uuid4(),
        session_id=session_id,
        workflow_run_id=workflow_run_id,
        owner_user_id=owner_user_id,
        workflow_type=WorkflowType.INSPECTION_ANALYSIS,
        stage=WorkflowStage.AWAITING_APPROVAL,
        stage_version=2,
        tool_name=ToolName.REQUEST_DOCUMENT_EXPORT.value,
        normalized_arguments=arguments.model_dump(mode="json", by_alias=True),
        arguments_hash=argument_hash(arguments),
        requested_at=_CREATED_AT,
    )
    await approval_store.create_pending(approval)
    claim_token: UUID | None = None
    if decision is not None:
        resolution = await approval_store.resolve_pending_approval(
            approval_id=approval.approval_id,
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            owner_user_id=owner_user_id,
            expected_stage=approval.stage,
            expected_stage_version=approval.stage_version,
            decision=decision,
            resolved_at=_CREATED_AT + timedelta(minutes=1),
            comment=None,
        )
        assert resolution is not None
        approval = resolution.approval
        if decision is ApprovalDecision.APPROVED and claim:
            execution_claim = await approval_store.claim_execution(
                approval_id=approval.approval_id,
                session_id=session_id,
                workflow_run_id=workflow_run_id,
                owner_user_id=owner_user_id,
                workflow_type=approval.workflow_type,
                expected_stage=approval.stage,
                expected_stage_version=approval.stage_version,
                tool_name=approval.tool_name,
                arguments_hash=approval.arguments_hash,
            )
            assert execution_claim is not None
            claim_token = execution_claim.execution_claim_token
    artifact = StoredArtifact(
        artifact_id=uuid4(),
        session_id=session_id,
        workflow_run_id=workflow_run_id,
        owner_user_id=owner_user_id,
        approval_id=approval.approval_id,
        draft_id=draft_id,
        format=formats[0],
        file_name=f"inspection-draft.{formats[0].value}",
        size_bytes=512,
        sha256="a" * 64,
        created_at=_CREATED_AT + timedelta(minutes=2),
    )
    return _ArtifactContext(
        database_path=database_path,
        database=database,
        approval_store=approval_store,
        artifact_store=artifact_store,
        approval=approval,
        artifact=artifact,
        execution_claim_token=claim_token,
    )


@pytest.mark.asyncio
async def test_initialize_is_idempotent_and_creates_metadata_only_schema(
    tmp_path: Path,
) -> None:
    database = LocalSQLiteDatabase(tmp_path / "workbench.db")

    await database.initialize()
    await database.initialize()

    async with database.open() as connection:
        columns = await (await connection.execute("PRAGMA table_info(artifacts)")).fetchall()
        indexes = await (await connection.execute("PRAGMA index_list(artifacts)")).fetchall()

    column_names = {row["name"] for row in columns}
    assert column_names == {
        "artifact_id",
        "session_id",
        "workflow_run_id",
        "owner_user_id",
        "approval_id",
        "draft_id",
        "format",
        "file_name",
        "size_bytes",
        "sha256",
        "created_at",
    }
    assert not column_names.intersection({"bytes", "blob", "path", "url", "content", "data"})
    assert "artifacts_run_created" in {row["name"] for row in indexes}


@pytest.mark.asyncio
async def test_exact_round_trip_restart_persistence_and_owner_scoping(tmp_path: Path) -> None:
    context = await _context(tmp_path)
    assert context.execution_claim_token is not None

    created = await context.artifact_store.create(
        context.artifact,
        execution_claim_token=context.execution_claim_token,
    )
    restarted = SQLiteArtifactStore(LocalSQLiteDatabase(context.database_path))
    retrieved = await restarted.get(
        artifact_id=context.artifact.artifact_id,
        session_id=context.artifact.session_id,
        workflow_run_id=context.artifact.workflow_run_id,
        owner_user_id=context.artifact.owner_user_id,
    )

    assert created == context.artifact
    assert retrieved == context.artifact
    for field_name in ("session_id", "workflow_run_id", "owner_user_id"):
        query = {
            "artifact_id": context.artifact.artifact_id,
            "session_id": context.artifact.session_id,
            "workflow_run_id": context.artifact.workflow_run_id,
            "owner_user_id": context.artifact.owner_user_id,
        }
        query[field_name] = uuid4()
        assert await restarted.get(**query) is None
    assert not list(tmp_path.rglob("*.docx"))


@pytest.mark.asyncio
async def test_list_for_run_is_stable_and_scoped(tmp_path: Path) -> None:
    context = await _context(
        tmp_path,
        formats=(ArtifactFormat.DOCX, ArtifactFormat.PDF),
    )
    assert context.execution_claim_token is not None
    later = context.artifact.model_copy(
        update={
            "artifact_id": UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
            "format": ArtifactFormat.PDF,
            "file_name": "inspection-draft.pdf",
            "created_at": context.artifact.created_at + timedelta(seconds=1),
        }
    )
    await context.artifact_store.create(
        later,
        execution_claim_token=context.execution_claim_token,
    )
    await context.artifact_store.create(
        context.artifact,
        execution_claim_token=context.execution_claim_token,
    )

    listed = await context.artifact_store.list_for_run(
        session_id=context.artifact.session_id,
        workflow_run_id=context.artifact.workflow_run_id,
        owner_user_id=context.artifact.owner_user_id,
    )

    assert listed == [context.artifact, later]
    assert (
        await context.artifact_store.list_for_run(
            session_id=context.artifact.session_id,
            workflow_run_id=context.artifact.workflow_run_id,
            owner_user_id=uuid4(),
        )
        == []
    )


@pytest.mark.asyncio
async def test_duplicate_id_and_case_insensitive_session_filename_are_rejected(
    tmp_path: Path,
) -> None:
    context = await _context(tmp_path)
    assert context.execution_claim_token is not None
    await context.artifact_store.create(
        context.artifact,
        execution_claim_token=context.execution_claim_token,
    )

    with pytest.raises(ArtifactAlreadyExistsError):
        await context.artifact_store.create(
            context.artifact,
            execution_claim_token=context.execution_claim_token,
        )
    duplicate_name = context.artifact.model_copy(
        update={
            "artifact_id": uuid4(),
            "file_name": context.artifact.file_name.upper(),
        }
    )
    with pytest.raises(ArtifactAlreadyExistsError):
        await context.artifact_store.create(
            duplicate_name,
            execution_claim_token=context.execution_claim_token,
        )


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"file_name": "../draft.docx"}, "safe local path component"),
        ({"file_name": "C:\\draft.docx"}, "safe local path component"),
        ({"file_name": "draft.pdf"}, "extension must match"),
        ({"size_bytes": 0}, "greater than or equal to 1"),
        ({"sha256": "A" * 64}, "String should match pattern"),
        ({"format": "zip"}, "Input should be 'docx' or 'pdf'"),
        ({"created_at": datetime(2026, 9, 6, 9, 0)}, "timezone-aware UTC"),
    ],
)
def test_artifact_contract_rejects_unsafe_or_invalid_metadata(
    update: dict[str, object],
    message: str,
) -> None:
    base = StoredArtifact(
        artifact_id=uuid4(),
        session_id=uuid4(),
        workflow_run_id=uuid4(),
        owner_user_id=uuid4(),
        approval_id=uuid4(),
        draft_id=uuid4(),
        format=ArtifactFormat.DOCX,
        file_name="draft.docx",
        size_bytes=1,
        sha256="0" * 64,
        created_at=_CREATED_AT,
    ).model_dump()

    with pytest.raises(ValidationError, match=message):
        StoredArtifact.model_validate({**base, **update})


@pytest.mark.asyncio
async def test_context_and_winning_token_must_match_exactly(tmp_path: Path) -> None:
    context = await _context(tmp_path)
    assert context.execution_claim_token is not None

    with pytest.raises(ArtifactContextMismatchError):
        await context.artifact_store.create(
            context.artifact,
            execution_claim_token=uuid4(),
        )
    for field_name in ("approval_id", "session_id", "workflow_run_id", "owner_user_id"):
        mismatched = context.artifact.model_copy(update={field_name: uuid4()})
        with pytest.raises(ArtifactContextMismatchError):
            await context.artifact_store.create(
                mismatched,
                execution_claim_token=context.execution_claim_token,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "claim"),
    [
        (None, False),
        (ApprovalDecision.REJECTED, False),
        (ApprovalDecision.APPROVED, False),
    ],
)
async def test_pending_rejected_and_unclaimed_approvals_cannot_create_artifacts(
    tmp_path: Path,
    decision: ApprovalDecision | None,
    claim: bool,
) -> None:
    context = await _context(tmp_path, decision=decision, claim=claim)

    with pytest.raises(ArtifactContextMismatchError):
        await context.artifact_store.create(
            context.artifact,
            execution_claim_token=uuid4(),
        )


@pytest.mark.asyncio
async def test_tool_draft_format_and_canonical_arguments_are_enforced(tmp_path: Path) -> None:
    context = await _context(tmp_path)
    assert context.execution_claim_token is not None

    wrong_draft = context.artifact.model_copy(update={"draft_id": uuid4()})
    wrong_format = context.artifact.model_copy(
        update={"format": ArtifactFormat.PDF, "file_name": "draft.pdf"}
    )
    for artifact in (wrong_draft, wrong_format):
        with pytest.raises(ArtifactContextMismatchError):
            await context.artifact_store.create(
                artifact,
                execution_claim_token=context.execution_claim_token,
            )

    async with context.database.open() as connection:
        await connection.execute(
            "UPDATE approvals SET tool_name = ? WHERE approval_id = ?",
            (ToolName.RUN_SANDBOX.value, str(context.approval.approval_id)),
        )
    with pytest.raises(ArtifactContextMismatchError):
        await context.artifact_store.create(
            context.artifact,
            execution_claim_token=context.execution_claim_token,
        )

    async with context.database.open() as connection:
        await connection.execute(
            """UPDATE approvals SET tool_name = ?, normalized_arguments = ?
            WHERE approval_id = ?""",
            (
                ToolName.REQUEST_DOCUMENT_EXPORT.value,
                '{"draftId":"not-a-uuid","formats":["docx"]}',
                str(context.approval.approval_id),
            ),
        )
    with pytest.raises(ArtifactContextMismatchError, match="arguments are invalid"):
        await context.artifact_store.create(
            context.artifact,
            execution_claim_token=context.execution_claim_token,
        )


@pytest.mark.asyncio
async def test_concurrent_duplicate_creation_inserts_exactly_once(tmp_path: Path) -> None:
    context = await _context(tmp_path)
    assert context.execution_claim_token is not None

    outcomes = await asyncio.gather(
        context.artifact_store.create(
            context.artifact,
            execution_claim_token=context.execution_claim_token,
        ),
        context.artifact_store.create(
            context.artifact,
            execution_claim_token=context.execution_claim_token,
        ),
        return_exceptions=True,
    )

    assert sum(item == context.artifact for item in outcomes) == 1
    assert sum(isinstance(item, ArtifactAlreadyExistsError) for item in outcomes) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("format_value", "size_bytes"), [("zip", 1), ("docx", 0)])
async def test_sqlite_constraints_reject_invalid_metadata(
    tmp_path: Path,
    format_value: str,
    size_bytes: int,
) -> None:
    context = await _context(tmp_path)

    with pytest.raises(aiosqlite.IntegrityError):
        async with context.database.open() as connection:
            await connection.execute(
                """INSERT INTO artifacts (
                    artifact_id, session_id, workflow_run_id, owner_user_id,
                    approval_id, draft_id, format, file_name, size_bytes,
                    sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid4()),
                    str(context.artifact.session_id),
                    str(context.artifact.workflow_run_id),
                    str(context.artifact.owner_user_id),
                    str(context.artifact.approval_id),
                    str(context.artifact.draft_id),
                    format_value,
                    "invalid.docx",
                    size_bytes,
                    "a" * 64,
                    _CREATED_AT.isoformat(),
                ),
            )
