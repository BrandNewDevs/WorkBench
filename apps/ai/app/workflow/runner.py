"""Checkpoint-aware workflow runner boundary owned by Backend 1."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.ai.engine import AIEngine
from app.ai.schemas import (
    DraftRequest,
    Finding,
    KnowledgeQuery,
    ProposedToolCall,
    VisualAnalysisRequest,
)
from app.ports.local_backend import (
    DraftStore,
    StoredDraft,
    StoredExtractionFindings,
    StoredRetrievedEvidence,
    WorkflowRunAdmission,
    WorkflowStore,
)
from app.tools.registry import ToolRegistry
from app.workflow.contracts import (
    Approval,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowStage,
    WorkflowType,
)
from app.workflow.transitions import assert_transition


class CheckpointApprovalReader(Protocol):
    async def get_for_run(
        self, *, session_id: UUID, workflow_run_id: UUID, owner_user_id: UUID
    ) -> Approval | None: ...


class WorkflowResumePoint(StrEnum):
    EXTRACTION = "extraction"
    RETRIEVAL = "retrieval"
    DRAFTING = "drafting"
    PROPOSAL = "proposal"
    WAITING_FOR_APPROVAL = "waitingForApproval"


class WorkflowRunner(Protocol):
    """Run expensive policy-selected operations after durable admission."""

    async def run(self, admission: WorkflowRunAdmission) -> None: ...


class InspectionWorkflowInputPolicy(Protocol):
    """Build product-specific AI inputs without moving policy into persistence."""

    def visual_analysis_request(self, admission: WorkflowRunAdmission) -> VisualAnalysisRequest: ...

    def knowledge_query(
        self, admission: WorkflowRunAdmission, findings: tuple[Finding, ...]
    ) -> KnowledgeQuery: ...

    def draft_request(
        self,
        admission: WorkflowRunAdmission,
        findings: tuple[Finding, ...],
        evidence: StoredRetrievedEvidence,
    ) -> DraftRequest: ...

    def export_proposal(
        self, admission: WorkflowRunAdmission, draft: StoredDraft
    ) -> ProposedToolCall: ...


@dataclass(frozen=True, slots=True)
class WorkflowCheckpoint:
    resume_at: WorkflowResumePoint
    draft_id: UUID | None = None


class WorkflowCheckpointResolver:
    """Derive restart position exclusively from durable typed checkpoints."""

    def __init__(
        self, workflows: WorkflowStore, drafts: DraftStore, approvals: CheckpointApprovalReader
    ) -> None:
        self._workflows = workflows
        self._drafts = drafts
        self._approvals = approvals

    async def resolve(self, admission: WorkflowRunAdmission) -> WorkflowCheckpoint:
        run = admission.run
        approval = await self._approvals.get_for_run(
            session_id=run.session_id,
            workflow_run_id=run.workflow_run_id,
            owner_user_id=run.owner_user_id,
        )
        if approval is not None:
            return WorkflowCheckpoint(WorkflowResumePoint.WAITING_FOR_APPROVAL)
        draft = await self._drafts.get_for_run(
            session_id=run.session_id,
            workflow_run_id=run.workflow_run_id,
            owner_user_id=run.owner_user_id,
        )
        if draft is not None:
            return WorkflowCheckpoint(WorkflowResumePoint.PROPOSAL, draft.draft_id)
        evidence = await self._workflows.get_evidence(
            session_id=run.session_id,
            workflow_run_id=run.workflow_run_id,
            owner_user_id=run.owner_user_id,
        )
        if evidence is not None:
            return WorkflowCheckpoint(WorkflowResumePoint.DRAFTING)
        findings = await self._workflows.get_findings(
            session_id=run.session_id,
            workflow_run_id=run.workflow_run_id,
            owner_user_id=run.owner_user_id,
        )
        return WorkflowCheckpoint(
            WorkflowResumePoint.RETRIEVAL
            if findings is not None
            else WorkflowResumePoint.EXTRACTION
        )


class WorkflowRunnerStateError(RuntimeError):
    """Durable checkpoints and workflow stage cannot be reconciled safely."""


class CheckpointAwareWorkflowRunner:
    """Run one inspection workflow while persisting every expensive boundary."""

    def __init__(
        self,
        *,
        workflows: WorkflowStore,
        drafts: DraftStore,
        approvals: CheckpointApprovalReader,
        ai_engine: AIEngine,
        tool_registry: ToolRegistry,
        input_policy: InspectionWorkflowInputPolicy,
        lease_seconds: int = 120,
    ) -> None:
        self._workflows = workflows
        self._drafts = drafts
        self._ai_engine = ai_engine
        self._tool_registry = tool_registry
        self._input_policy = input_policy
        self._lease_seconds = lease_seconds
        self._checkpoints = WorkflowCheckpointResolver(workflows, drafts, approvals)

    async def run(self, admission: WorkflowRunAdmission) -> None:
        """Resume from durable output and stop after preparing approval."""

        if admission.run.workflow_type is not WorkflowType.INSPECTION_ANALYSIS:
            raise WorkflowRunnerStateError("no checkpoint runner exists for this workflow type")
        checkpoint = await self._checkpoints.resolve(admission)
        if checkpoint.resume_at is WorkflowResumePoint.WAITING_FOR_APPROVAL:
            return

        findings = await self._workflows.get_findings(
            session_id=admission.run.session_id,
            workflow_run_id=admission.run.workflow_run_id,
            owner_user_id=admission.run.owner_user_id,
        )
        if findings is None:
            run = await self._advance_to(admission, WorkflowStage.EXTRACTING)
            analysis = await self._ai_engine.analyze_visual(
                self._input_policy.visual_analysis_request(admission)
            )
            findings = await self._workflows.save_findings(
                StoredExtractionFindings(
                    session_id=run.session_id,
                    workflow_run_id=run.workflow_run_id,
                    owner_user_id=run.owner_user_id,
                    source_upload_ids=tuple(item.upload_id for item in admission.selected_uploads),
                    findings=analysis.findings,
                    created_at=datetime.now(UTC),
                )
            )

        evidence = await self._workflows.get_evidence(
            session_id=admission.run.session_id,
            workflow_run_id=admission.run.workflow_run_id,
            owner_user_id=admission.run.owner_user_id,
        )
        if evidence is None:
            run = await self._advance_to(admission, WorkflowStage.RETRIEVING)
            retrieved = await self._ai_engine.search_knowledge(
                self._input_policy.knowledge_query(admission, findings.findings)
            )
            evidence = await self._workflows.save_evidence(
                StoredRetrievedEvidence(
                    session_id=run.session_id,
                    workflow_run_id=run.workflow_run_id,
                    owner_user_id=run.owner_user_id,
                    evidence=tuple(retrieved),
                    created_at=datetime.now(UTC),
                )
            )

        draft = await self._drafts.get_for_run(
            session_id=admission.run.session_id,
            workflow_run_id=admission.run.workflow_run_id,
            owner_user_id=admission.run.owner_user_id,
        )
        if draft is None:
            run = await self._advance_to(admission, WorkflowStage.DRAFTING)
            generated = await self._ai_engine.create_grounded_draft(
                self._input_policy.draft_request(admission, findings.findings, evidence)
            )
            draft = await self._drafts.save(
                workflow_run=run,
                draft=generated,
                created_at=datetime.now(UTC),
            )

        run = await self._advance_to(admission, WorkflowStage.VALIDATING)
        output = self._tool_registry.validate_proposal(
            self._input_policy.export_proposal(admission, draft),
            workflow_type=run.workflow_type,
            stage=run.stage,
        )
        awaiting = run.model_copy(
            update={
                "stage": WorkflowStage.AWAITING_APPROVAL,
                "stage_version": run.stage_version + 1,
                "status": WorkflowRunStatus.WAITING_FOR_APPROVAL,
            }
        )
        approval = self._tool_registry.create_pending_approval(output, workflow_run=awaiting)
        await self._workflows.prepare_pending_approval(run=run, approval=approval, output=output)

    async def _advance_to(
        self, admission: WorkflowRunAdmission, target: WorkflowStage
    ) -> WorkflowRun:
        run = await self._current_run(admission)
        if run.stage is target:
            return run
        assert_transition(run.workflow_type, run.stage, target)
        advanced = await self._workflows.compare_and_set_stage(
            session_id=run.session_id,
            workflow_run_id=run.workflow_run_id,
            owner_user_id=run.owner_user_id,
            expected_stage=run.stage,
            expected_stage_version=run.stage_version,
            next_stage=target,
            next_status=WorkflowRunStatus.ACTIVE,
            sandbox_attempts=run.sandbox_attempts,
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=self._lease_seconds),
        )
        if advanced is None:
            raise WorkflowRunnerStateError("workflow stage changed concurrently")
        return advanced

    async def _current_run(self, admission: WorkflowRunAdmission) -> WorkflowRun:
        run = await self._workflows.get_run(
            workflow_run_id=admission.run.workflow_run_id,
            session_id=admission.run.session_id,
            owner_user_id=admission.run.owner_user_id,
        )
        if run is None:
            raise WorkflowRunnerStateError("workflow run is unavailable")
        if run.status is WorkflowRunStatus.WAITING_FOR_APPROVAL:
            raise WorkflowRunnerStateError("workflow is already waiting for approval")
        if run.status not in {WorkflowRunStatus.QUEUED, WorkflowRunStatus.ACTIVE}:
            raise WorkflowRunnerStateError("workflow run is not resumable")
        return run
