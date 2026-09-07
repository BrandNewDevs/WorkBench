"""Checkpoint-aware workflow runner boundary owned by Backend 1."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid5

from app.ai.engine import AIEngine
from app.ai.errors import AIError
from app.ai.schemas import (
    AgentContext,
    ApprovedVisualInput,
    Capability,
    ConversationMessage,
    DraftRequest,
    Finding,
    InputModality,
    KnowledgeQuery,
    ProposedToolCall,
    SandboxProposalContext,
    TaskDescriptor,
    TaskKind,
    VisualAnalysisRequest,
    VisualMimeType,
)
from app.ports.local_backend import (
    ActivityEventStore,
    DraftStore,
    SessionFileStore,
    StoredDraft,
    StoredExtractionFindings,
    StoredRetrievedEvidence,
    WorkflowMessage,
    WorkflowRunAdmission,
    WorkflowStore,
)
from app.tools.contracts import SandboxArguments, ToolName
from app.tools.registry import ToolRegistry, ToolValidationError
from app.workflow.contracts import (
    ActivityEvent,
    ActivityEventType,
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

    def task_descriptor(self, admission: WorkflowRunAdmission) -> TaskDescriptor: ...

    async def visual_analysis_request(
        self, admission: WorkflowRunAdmission
    ) -> VisualAnalysisRequest: ...

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


_FAILURE_COMPLETION = "The workflow could not complete. Please review the activity trace."


def build_failure_message(run: WorkflowRun, *, created_at: datetime) -> WorkflowMessage:
    """Build the deterministic, user-safe completion stored for a failed run."""

    message_id = uuid5(run.workflow_run_id, "assistant-failure")
    return WorkflowMessage(
        message_id=message_id,
        session_id=run.session_id,
        author_user_id=None,
        role="assistant",
        content=_FAILURE_COMPLETION,
        created_at=created_at,
        client_message_id=message_id,
    )


class LocalInspectionWorkflowInputPolicy:
    """Resolve only selected uploads into AI inputs at the execution boundary."""

    def __init__(self, files: SessionFileStore) -> None:
        self._files = files

    def task_descriptor(self, admission: WorkflowRunAdmission) -> TaskDescriptor:
        modalities = tuple(
            InputModality.SCANNED_PDF
            if item.mime_type == VisualMimeType.PDF.value
            else InputModality.IMAGE
            for item in admission.selected_uploads
        )
        if not modalities:
            raise WorkflowRunnerStateError("inspection workflow has no selected visual input")
        return TaskDescriptor(
            task_id=str(admission.run.workflow_run_id),
            kind=TaskKind.VISUAL_ANALYSIS,
            summary=admission.message.content,
            modalities=modalities,
            file_types=tuple(item.mime_type for item in admission.selected_uploads),
            requested_capability=Capability.VISION,
        )

    async def visual_analysis_request(
        self, admission: WorkflowRunAdmission
    ) -> VisualAnalysisRequest:
        inputs: list[ApprovedVisualInput] = []
        for upload in admission.selected_uploads:
            try:
                mime_type = VisualMimeType(upload.mime_type)
            except ValueError as error:
                raise WorkflowRunnerStateError("inspection upload has unsupported media") from error
            approved = await self._files.resolve_approved_path(
                upload_id=upload.upload_id,
                session_id=admission.run.session_id,
                owner_user_id=admission.run.owner_user_id,
            )
            if approved is None or approved.source_id != str(upload.source_id):
                raise WorkflowRunnerStateError("inspection upload is unavailable")
            inputs.append(
                ApprovedVisualInput(
                    approved_path=approved,
                    mime_type=mime_type,
                    document_name=upload.file_name,
                )
            )
        return VisualAnalysisRequest(inputs=tuple(inputs), task=self.task_descriptor(admission))

    def knowledge_query(
        self, admission: WorkflowRunAdmission, findings: tuple[Finding, ...]
    ) -> KnowledgeQuery:
        descriptions = " ".join(finding.description for finding in findings)
        return KnowledgeQuery(text=f"{admission.message.content}\n{descriptions}")

    def draft_request(
        self,
        admission: WorkflowRunAdmission,
        findings: tuple[Finding, ...],
        evidence: StoredRetrievedEvidence,
    ) -> DraftRequest:
        return DraftRequest(
            subject="Inspection approval note",
            objective=admission.message.content,
            findings=findings,
            evidence=evidence.evidence,
        )

    def export_proposal(
        self, admission: WorkflowRunAdmission, draft: StoredDraft
    ) -> ProposedToolCall:
        return ProposedToolCall(
            tool_name=ToolName.REQUEST_DOCUMENT_EXPORT.value,
            arguments={"draftId": str(draft.draft_id), "formats": ["docx", "pdf"]},
            explanation="The validated draft is ready for your approval before local export.",
        )


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
        events: ActivityEventStore | None = None,
        lease_seconds: int = 120,
    ) -> None:
        self._workflows = workflows
        self._drafts = drafts
        self._ai_engine = ai_engine
        self._tool_registry = tool_registry
        self._input_policy = input_policy
        self._events = events
        self._lease_seconds = lease_seconds
        self._checkpoints = WorkflowCheckpointResolver(workflows, drafts, approvals)

    async def run(self, admission: WorkflowRunAdmission) -> None:
        """Resume from durable output and stop after preparing approval."""

        try:
            if admission.run.workflow_type is WorkflowType.INSPECTION_ANALYSIS:
                await self._run_inspection(admission)
            elif admission.run.workflow_type is WorkflowType.CODE_REPAIR:
                await self._run_code_repair(admission)
            else:
                # Only the two deterministic MVP workflows are runnable; any
                # other session kind must never reach a stage pipeline.
                await self._mark_interrupted(admission)
        except asyncio.CancelledError:
            await self._mark_interrupted(admission)
            raise
        except Exception as error:
            try:
                await self._fail(admission, self._failure_code(error))
            except asyncio.CancelledError:
                await self._mark_interrupted(admission)
                raise

    async def _run_inspection(self, admission: WorkflowRunAdmission) -> None:
        checkpoint = await self._checkpoints.resolve(admission)
        if checkpoint.resume_at is WorkflowResumePoint.WAITING_FOR_APPROVAL:
            return

        findings = await self._workflows.get_findings(
            session_id=admission.run.session_id,
            workflow_run_id=admission.run.workflow_run_id,
            owner_user_id=admission.run.owner_user_id,
        )
        if findings is None:
            task = self._input_policy.task_descriptor(admission)
            decision = await self._ai_engine.choose_capability(task)
            if decision.capability is not Capability.VISION:
                raise WorkflowRunnerStateError("inspection task was not routed to vision")
            visual_request = await self._input_policy.visual_analysis_request(admission)
            run = await self._advance_to(admission, WorkflowStage.EXTRACTING)
            analysis = await self._ai_engine.analyze_visual(visual_request)
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
        self._validate_draft(draft, findings.findings, evidence)
        await self._persist_assistant(
            admission,
            self._render_completion(draft, findings.findings, evidence),
        )
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
        prepared = await self._workflows.prepare_pending_approval(
            run=run, approval=approval, output=output
        )
        del prepared

    async def _run_code_repair(self, admission: WorkflowRunAdmission) -> None:
        """Plan one narrow sandbox proposal and stop before execution."""

        checkpoint = await self._checkpoints.resolve(admission)
        if checkpoint.resume_at is WorkflowResumePoint.WAITING_FOR_APPROVAL:
            return
        task = TaskDescriptor(
            task_id=str(admission.run.workflow_run_id),
            kind=TaskKind.CODE_REPAIR,
            summary=admission.message.content,
            modalities=(InputModality.TEXT,),
            file_types=tuple(item.mime_type for item in admission.selected_uploads),
            requested_capability=Capability.TEXT,
        )
        decision = await self._ai_engine.choose_capability(task)
        if decision.capability is not Capability.TEXT:
            raise WorkflowRunnerStateError("code task was not routed to text")
        run = await self._advance_to(admission, WorkflowStage.PLANNING)
        proposal = await self._ai_engine.propose_action(
            AgentContext(
                task=task,
                conversation=(ConversationMessage(role="user", content=admission.message.content),),
                allowed_tools=self._tool_registry.definitions_for(run.workflow_type, run.stage),
                sandbox_context=SandboxProposalContext(
                    workspace_id=admission.run.session_id,
                    source_file_ids=tuple(item.source_id for item in admission.selected_uploads),
                ),
            )
        )
        if proposal.response_text is not None:
            raise WorkflowRunnerStateError("code workflow did not produce an approval proposal")
        if proposal.tool_call is None:
            raise WorkflowRunnerStateError("code workflow returned an invalid proposal")
        output = self._tool_registry.validate_proposal(
            proposal.tool_call, workflow_type=run.workflow_type, stage=run.stage
        )
        self._validate_sandbox_inputs(output.arguments, admission)
        await self._persist_assistant(admission, proposal.tool_call.explanation)
        awaiting = run.model_copy(
            update={
                "stage": WorkflowStage.AWAITING_APPROVAL,
                "stage_version": run.stage_version + 1,
                "status": WorkflowRunStatus.WAITING_FOR_APPROVAL,
            }
        )
        approval = self._tool_registry.create_pending_approval(output, workflow_run=awaiting)
        prepared = await self._workflows.prepare_pending_approval(
            run=run, approval=approval, output=output
        )
        del prepared

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
        await self._emit_stage_changed(run, advanced)
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

    async def _mark_interrupted(self, admission: WorkflowRunAdmission) -> None:
        """Ensure a shutdown-cancelled active run is immediately recoverable."""

        try:
            current = await self._workflows.get_run(
                workflow_run_id=admission.run.workflow_run_id,
                session_id=admission.run.session_id,
                owner_user_id=admission.run.owner_user_id,
            )
            if current is None or current.status is not WorkflowRunStatus.ACTIVE:
                return
            await self._workflows.mark_run_interrupted(
                workflow_run_id=current.workflow_run_id,
                session_id=current.session_id,
                owner_user_id=current.owner_user_id,
                expected_stage=current.stage,
                expected_stage_version=current.stage_version,
                interrupted_at=datetime.now(UTC),
            )
        except Exception:
            return

    async def _emit_stage_changed(self, previous: WorkflowRun, current: WorkflowRun) -> None:
        if self._events is None or previous.stage is current.stage:
            return
        await self._events.append(
            ActivityEvent(
                event_id=0,
                session_id=current.session_id,
                workflow_run_id=current.workflow_run_id,
                event_type=ActivityEventType.STAGE_CHANGED,
                occurred_at=current.updated_at,
                payload={
                    "previousStage": previous.stage.value,
                    "stage": current.stage.value,
                    "stageVersion": current.stage_version,
                    "status": current.status.value,
                },
            ),
            owner_user_id=current.owner_user_id,
        )

    async def _persist_assistant(
        self,
        admission: WorkflowRunAdmission,
        content: str,
    ) -> None:
        """Store user-safe completion text with a deterministic retry key."""

        message_id = uuid5(admission.run.workflow_run_id, "assistant-completion")
        await self._workflows.append_assistant_completion(
            run=admission.run,
            message=WorkflowMessage(
                message_id=message_id,
                session_id=admission.run.session_id,
                author_user_id=None,
                role="assistant",
                content=content[:20_000],
                created_at=datetime.now(UTC),
                client_message_id=message_id,
            ),
        )
    @staticmethod
    def _validate_draft(
        draft: StoredDraft,
        findings: tuple[Finding, ...],
        evidence: StoredRetrievedEvidence,
    ) -> None:
        """Reject invented findings or citations before any approval is created."""

        if draft.draft.findings != findings:
            raise WorkflowRunnerStateError("draft findings do not match extraction")
        source_ids = {item.source_id for item in evidence.evidence}
        source_ids.update(
            reference.source_id for finding in findings for reference in finding.evidence
        )
        cited_ids = set(draft.draft.evidence_source_ids)
        if not cited_ids or not cited_ids.issubset(source_ids):
            raise WorkflowRunnerStateError("draft contains unsupported source citations")

    @staticmethod
    def _render_completion(
        draft: StoredDraft,
        findings: tuple[Finding, ...],
        evidence: StoredRetrievedEvidence,
    ) -> str:
        """Render concise labels from application-held source metadata only."""

        labels = {
            item.source_id: ", ".join(
                part
                for part in (
                    item.document_name,
                    f"page {item.page_number}" if item.page_number is not None else None,
                    item.section,
                )
                if part
            )
            for item in evidence.evidence
        }
        for finding in findings:
            for reference in finding.evidence:
                labels.setdefault(
                    reference.source_id,
                    ", ".join(
                        part
                        for part in (
                            reference.document_name,
                            f"page {reference.page_number}"
                            if reference.page_number is not None
                            else None,
                            reference.section,
                        )
                        if part
                    )
                    or "uploaded inspection input",
                )
        rendered = "; ".join(
            f"{source_id}: {labels[source_id]}" for source_id in draft.draft.evidence_source_ids
        )
        return f"{draft.draft.summary}\n\nSources: {rendered}"

    @staticmethod
    def _validate_sandbox_inputs(arguments: object, admission: WorkflowRunAdmission) -> None:
        if not isinstance(arguments, SandboxArguments):
            raise ToolValidationError("code workflow requires the sandbox argument schema")
        source_ids = {item.source_id for item in admission.selected_uploads}
        if (
            arguments.workspace_id != admission.run.session_id
            or arguments.source_file_id not in source_ids
        ):
            raise ToolValidationError("sandbox proposal references an unapproved upload")
        if arguments.test_file_id is not None and arguments.test_file_id not in source_ids:
            raise ToolValidationError("sandbox proposal references an unapproved test upload")

    async def _fail(self, admission: WorkflowRunAdmission, failure_code: str) -> None:
        """Make asynchronous failures durable and safely visible to the owner."""

        try:
            current = await self._workflows.get_run(
                workflow_run_id=admission.run.workflow_run_id,
                session_id=admission.run.session_id,
                owner_user_id=admission.run.owner_user_id,
            )
            if current is None or current.status not in {
                WorkflowRunStatus.QUEUED,
                WorkflowRunStatus.ACTIVE,
            }:
                return
            await self._workflows.finalize_failure(
                run=current,
                failure_code=failure_code,
                message=build_failure_message(current, created_at=datetime.now(UTC)),
            )
        except Exception:
            return

    @staticmethod
    def _failure_code(error: Exception) -> str:
        if isinstance(error, AIError):
            return "ai_operation_failed"
        if isinstance(error, ToolValidationError):
            return "tool_policy_rejected"
        if isinstance(error, WorkflowRunnerStateError):
            return "workflow_validation_failed"
        return "workflow_internal_error"
