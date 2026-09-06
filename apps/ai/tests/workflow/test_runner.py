"""Checkpoint-aware workflow runner tests."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest

from app.ai.evaluation.samples import sample_evidence_chunk, sample_grounded_draft
from app.ai.fakes import FakeAIEngine
from app.ai.schemas import (
    DraftRequest,
    Finding,
    KnowledgeQuery,
    ProposedToolCall,
    VisualAnalysisRequest,
)
from app.ports.local_backend import (
    StoredDraft,
    StoredExtractionFindings,
    StoredRetrievedEvidence,
    WorkflowMessage,
    WorkflowRunAdmission,
    WorkflowRunAdmissionRequest,
)
from app.storage import (
    LocalSQLiteDatabase,
    SQLiteApprovalStore,
    SQLiteDraftStore,
    SQLiteWorkflowStore,
)
from app.tools.contracts import ArtifactFormat
from app.tools.registry import ToolRegistry
from app.workflow.contracts import (
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowSession,
    WorkflowStage,
    WorkflowType,
)
from app.workflow.runner import CheckpointAwareWorkflowRunner

NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)


class DraftOnlyPolicy:
    def visual_analysis_request(self, admission: WorkflowRunAdmission) -> VisualAnalysisRequest:
        raise AssertionError("persisted findings must skip extraction")

    def knowledge_query(
        self, admission: WorkflowRunAdmission, findings: tuple[Finding, ...]
    ) -> KnowledgeQuery:
        raise AssertionError("persisted evidence must skip retrieval")

    def draft_request(
        self,
        admission: WorkflowRunAdmission,
        findings: tuple[Finding, ...],
        evidence: StoredRetrievedEvidence,
    ) -> DraftRequest:
        raise AssertionError("persisted draft must skip drafting")

    def export_proposal(
        self, admission: WorkflowRunAdmission, draft: StoredDraft
    ) -> ProposedToolCall:
        return ProposedToolCall(
            tool_name="request_document_export",
            arguments={"draftId": str(draft.draft_id), "formats": [ArtifactFormat.DOCX]},
            explanation="Export the persisted validated draft.",
        )


@pytest.mark.asyncio
async def test_runner_reuses_durable_draft_and_only_prepares_approval(
    tmp_path: Path,
) -> None:
    database = LocalSQLiteDatabase(tmp_path / "workbench.db")
    await database.initialize()
    workflows = SQLiteWorkflowStore(database)
    drafts = SQLiteDraftStore(database)
    approvals = SQLiteApprovalStore(database)
    owner_id = uuid4()
    session_id = uuid4()
    session = WorkflowSession(
        session_id=session_id,
        owner_user_id=owner_id,
        workflow_type=WorkflowType.INSPECTION_ANALYSIS,
        title="Inspection",
        created_at=NOW,
        updated_at=NOW,
    )
    await workflows.create_session(session)
    request = WorkflowRunAdmissionRequest(
        run=WorkflowRun(
            workflow_run_id=uuid4(),
            session_id=session_id,
            owner_user_id=owner_id,
            workflow_type=session.workflow_type,
            stage=WorkflowStage.COLLECTING_INPUTS,
            stage_version=0,
            status=WorkflowRunStatus.QUEUED,
            created_at=NOW,
            updated_at=NOW,
        ),
        message=WorkflowMessage(
            message_id=uuid4(),
            session_id=session_id,
            author_user_id=owner_id,
            role="user",
            content="Prepare an inspection draft.",
            client_message_id=uuid4(),
            created_at=NOW,
        ),
    )
    admission = await workflows.admit_run(request)
    run = admission.run
    for target in (
        WorkflowStage.EXTRACTING,
        WorkflowStage.RETRIEVING,
        WorkflowStage.DRAFTING,
    ):
        advanced = await workflows.compare_and_set_stage(
            session_id=session_id,
            workflow_run_id=run.workflow_run_id,
            owner_user_id=owner_id,
            expected_stage=run.stage,
            expected_stage_version=run.stage_version,
            next_stage=target,
            next_status=WorkflowRunStatus.ACTIVE,
            sandbox_attempts=0,
        )
        assert advanced is not None
        run = advanced
    draft_value = sample_grounded_draft()
    await workflows.save_findings(
        StoredExtractionFindings(
            session_id=session_id,
            workflow_run_id=run.workflow_run_id,
            owner_user_id=owner_id,
            source_upload_ids=(),
            findings=draft_value.findings,
            created_at=NOW,
        )
    )
    await workflows.save_evidence(
        StoredRetrievedEvidence(
            session_id=session_id,
            workflow_run_id=run.workflow_run_id,
            owner_user_id=owner_id,
            evidence=(sample_evidence_chunk(),),
            created_at=NOW,
        )
    )
    stored_draft = await drafts.save(workflow_run=run, draft=draft_value, created_at=NOW)
    fake_ai = FakeAIEngine()
    registry = ToolRegistry(cast(Any, approvals), cast(Any, object()), cast(Any, object()))
    runner = CheckpointAwareWorkflowRunner(
        workflows=SQLiteWorkflowStore(LocalSQLiteDatabase(database.database_path)),
        drafts=SQLiteDraftStore(LocalSQLiteDatabase(database.database_path)),
        approvals=SQLiteApprovalStore(LocalSQLiteDatabase(database.database_path)),
        ai_engine=fake_ai,
        tool_registry=registry,
        input_policy=DraftOnlyPolicy(),
    )

    await runner.run(admission)

    assert fake_ai.calls == []
    recovered = await drafts.get_for_run(
        session_id=session_id,
        workflow_run_id=run.workflow_run_id,
        owner_user_id=owner_id,
    )
    assert recovered is not None and recovered.draft_id == stored_draft.draft_id
    approval = await approvals.get_for_run(
        session_id=session_id,
        workflow_run_id=run.workflow_run_id,
        owner_user_id=owner_id,
    )
    assert approval is not None
    assert approval.normalized_arguments["draftId"] == str(stored_draft.draft_id)
    current = await workflows.get_run(
        workflow_run_id=run.workflow_run_id,
        session_id=session_id,
        owner_user_id=owner_id,
    )
    assert current is not None
    assert current.stage is WorkflowStage.AWAITING_APPROVAL
    assert current.status is WorkflowRunStatus.WAITING_FOR_APPROVAL
