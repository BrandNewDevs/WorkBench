"""Offline draft persistence and approved document-export integration tests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from docx import Document

from app.ai.evaluation.samples import sample_grounded_draft
from app.artifacts import LocalDocumentArtifactExecutor, PdfConversionError
from app.storage import (
    DraftConflictError,
    LocalSessionWorkspaceStore,
    LocalSQLiteDatabase,
    SQLiteApprovalStore,
    SQLiteArtifactStore,
    SQLiteDraftStore,
    SQLiteWorkflowStore,
)
from app.tools.contracts import (
    ArtifactFormat,
    DocumentExportArguments,
    DocumentExportExecutionRequest,
)
from app.tools.registry import argument_hash
from app.workflow.contracts import (
    Approval,
    ApprovalDecision,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowSession,
    WorkflowStage,
    WorkflowStatus,
    WorkflowType,
)

NOW = datetime(2026, 9, 7, tzinfo=UTC)


async def _context(
    tmp_path: Path, formats: tuple[ArtifactFormat, ...]
) -> tuple[
    LocalSQLiteDatabase,
    SQLiteDraftStore,
    SQLiteArtifactStore,
    LocalSessionWorkspaceStore,
    DocumentExportExecutionRequest,
]:
    database = LocalSQLiteDatabase(tmp_path / "workbench.db")
    await database.initialize()
    workflows = SQLiteWorkflowStore(database)
    owner, session_id, run_id = uuid4(), uuid4(), uuid4()
    session = WorkflowSession(
        session_id=session_id,
        owner_user_id=owner,
        workflow_type=WorkflowType.INSPECTION_ANALYSIS,
        title="Inspection",
        stage=WorkflowStage.AWAITING_APPROVAL,
        status=WorkflowStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )
    await workflows.create_session(session)
    run = WorkflowRun(
        workflow_run_id=run_id,
        session_id=session_id,
        owner_user_id=owner,
        workflow_type=WorkflowType.INSPECTION_ANALYSIS,
        stage=WorkflowStage.AWAITING_APPROVAL,
        stage_version=2,
        status=WorkflowRunStatus.WAITING_FOR_APPROVAL,
        created_at=NOW,
        updated_at=NOW,
    )
    await workflows.create_run(run)
    drafts = SQLiteDraftStore(database)
    stored = await drafts.save(workflow_run=run, draft=sample_grounded_draft(), created_at=NOW)
    assert (
        await drafts.save(workflow_run=run, draft=sample_grounded_draft(), created_at=NOW) == stored
    )
    arguments = DocumentExportArguments(draft_id=stored.draft_id, formats=formats)
    approval = Approval(
        approval_id=uuid4(),
        session_id=session_id,
        workflow_run_id=run_id,
        owner_user_id=owner,
        workflow_type=WorkflowType.INSPECTION_ANALYSIS,
        stage=WorkflowStage.AWAITING_APPROVAL,
        stage_version=2,
        tool_name="request_document_export",
        normalized_arguments=arguments.model_dump(mode="json", by_alias=True),
        arguments_hash=argument_hash(arguments),
        requested_at=NOW,
    )
    approvals = SQLiteApprovalStore(database)
    await approvals.create_pending(approval)
    await approvals.resolve_pending_approval(
        approval_id=approval.approval_id,
        session_id=session_id,
        workflow_run_id=run_id,
        owner_user_id=owner,
        expected_stage=approval.stage,
        expected_stage_version=approval.stage_version,
        decision=ApprovalDecision.APPROVED,
        resolved_at=NOW + timedelta(seconds=1),
        comment=None,
    )
    claim = await approvals.claim_execution(
        approval_id=approval.approval_id,
        session_id=session_id,
        workflow_run_id=run_id,
        owner_user_id=owner,
        workflow_type=approval.workflow_type,
        expected_stage=approval.stage,
        expected_stage_version=approval.stage_version,
        tool_name=approval.tool_name,
        arguments_hash=approval.arguments_hash,
    )
    assert claim is not None and claim.execution_claim_token is not None
    request = DocumentExportExecutionRequest(
        approval_id=approval.approval_id,
        session_id=session_id,
        workflow_run_id=run_id,
        execution_claim_token=claim.execution_claim_token,
        arguments=arguments,
    )
    workspaces = LocalSessionWorkspaceStore(tmp_path / "sessions")
    workspaces.create_session_workspace(str(session_id))
    return database, drafts, SQLiteArtifactStore(database), workspaces, request


class FakePdfConverter:
    async def convert(self, source: Path, output_directory: Path) -> Path:
        assert source.is_file()
        output = output_directory / f"{source.stem}.pdf"
        output.write_bytes(b"%PDF-1.7\nlocal")
        return output


class FailingPdfConverter:
    async def convert(self, source: Path, output_directory: Path) -> Path:
        del source, output_directory
        raise PdfConversionError("failed")


@pytest.mark.asyncio
async def test_draft_restart_resolver_and_docx_pdf_pipeline(tmp_path: Path) -> None:
    database, _, artifacts, workspaces, request = await _context(
        tmp_path, (ArtifactFormat.DOCX, ArtifactFormat.PDF)
    )
    restarted = SQLiteDraftStore(LocalSQLiteDatabase(database.database_path))
    resolved = await restarted.resolve_for_export(request)
    assert resolved is not None
    result = await LocalDocumentArtifactExecutor(
        restarted, artifacts, workspaces, FakePdfConverter()
    ).create_artifacts(request)
    assert result.status.value == "completed"
    assert [item.format for item in result.artifacts] == [ArtifactFormat.DOCX, ArtifactFormat.PDF]
    paths = [
        workspaces.get_session_workspace(str(resolved.session_id)).artifacts / item.file_name
        for item in result.artifacts
    ]
    assert all(path.is_file() for path in paths)
    document = Document(str(paths[0]))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert resolved.draft.subject in text
    assert "Executive Summary" in text and "Uncertainties / Limitations" in text
    assert (
        len(
            await artifacts.list_for_run(
                session_id=resolved.session_id,
                workflow_run_id=resolved.workflow_run_id,
                owner_user_id=resolved.owner_user_id,
            )
        )
        == 2
    )


@pytest.mark.asyncio
async def test_draft_conflict_and_claim_mismatch_fail_closed(tmp_path: Path) -> None:
    _, drafts, _, _, request = await _context(tmp_path, (ArtifactFormat.DOCX,))
    stored = await drafts.resolve_for_export(request)
    assert stored is not None
    altered = stored.draft.model_copy(update={"summary": "Different"})
    run = WorkflowRun(
        workflow_run_id=stored.workflow_run_id,
        session_id=stored.session_id,
        owner_user_id=stored.owner_user_id,
        workflow_type=WorkflowType.INSPECTION_ANALYSIS,
        stage=WorkflowStage.AWAITING_APPROVAL,
        stage_version=2,
        status=WorkflowRunStatus.WAITING_FOR_APPROVAL,
        created_at=NOW,
        updated_at=NOW,
    )
    with pytest.raises(DraftConflictError):
        await drafts.save(workflow_run=run, draft=altered, created_at=NOW)
    assert (
        await drafts.resolve_for_export(
            request.model_copy(update={"execution_claim_token": uuid4()})
        )
        is None
    )


@pytest.mark.asyncio
async def test_pdf_failure_keeps_valid_docx_and_registers_nothing(tmp_path: Path) -> None:
    _, drafts, artifacts, workspaces, request = await _context(
        tmp_path, (ArtifactFormat.DOCX, ArtifactFormat.PDF)
    )
    resolved = await drafts.resolve_for_export(request)
    assert resolved is not None
    result = await LocalDocumentArtifactExecutor(
        drafts, artifacts, workspaces, FailingPdfConverter()
    ).create_artifacts(request)
    assert result.status.value == "failed"
    docx = (
        workspaces.get_session_workspace(str(resolved.session_id)).artifacts
        / f"approval-note-{resolved.draft_id}.docx"
    )
    assert docx.is_file()
    assert (
        await artifacts.list_for_run(
            session_id=resolved.session_id,
            workflow_run_id=resolved.workflow_run_id,
            owner_user_id=resolved.owner_user_id,
        )
        == []
    )
