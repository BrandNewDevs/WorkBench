"""Tests for typed proposal validation and approval-gated dispatch."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.ai.schemas import ProposedToolCall
from app.tools.contracts import (
    ArtifactFormat,
    DocumentExportExecutionRequest,
    DocumentExportResult,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    ToolExecutionResult,
)
from app.tools.registry import ApprovalPolicyError, ToolRegistry, ToolValidationError
from app.workflow.contracts import (
    Approval,
    ApprovalDecision,
    ApprovalExecutionClaim,
    ApprovalResolution,
    ApprovalStatus,
    ExecutionStatus,
    UtcTimestamp,
    WorkflowStage,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowType,
)


@dataclass
class RecordingArtifactExecutor:
    """A no-I/O fake proving policy checks precede Backend 2 execution."""

    calls: list[DocumentExportExecutionRequest] = field(default_factory=list)

    async def create_artifacts(
        self, request: DocumentExportExecutionRequest
    ) -> DocumentExportResult:
        self.calls.append(request)
        return DocumentExportResult(status=ExecutionStatus.COMPLETED)


@dataclass
class RecordingSandboxExecutor:
    """A no-I/O fake proving raw commands never reach an executor."""

    calls: list[SandboxExecutionRequest] = field(default_factory=list)

    async def run(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        self.calls.append(request)
        return SandboxExecutionResult(status=ExecutionStatus.COMPLETED, exit_code=0, passed=True)


@dataclass
class RecordingApprovalStore:
    """A no-I/O fake providing the atomic claim required before dispatch."""

    approvals: dict[UUID, Approval] = field(default_factory=dict)
    results: dict[UUID, ToolExecutionResult] = field(default_factory=dict)
    claims: list[UUID] = field(default_factory=list)

    async def create_pending(self, approval: Approval) -> Approval:
        self.approvals[approval.approval_id] = approval
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
        approval = self.approvals.get(approval_id)
        if approval is None or (
            approval.session_id != session_id
            or approval.workflow_run_id != workflow_run_id
            or approval.owner_user_id != owner_user_id
            or approval.stage is not expected_stage
            or approval.stage_version != expected_stage_version
        ):
            return None
        if approval.status is not ApprovalStatus.PENDING:
            return ApprovalResolution(approval=approval, resolved_now=False)
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
        resolved = Approval.model_validate(
            {
                **approval.model_dump(),
                "status": status,
                "decision": decision,
                "resolved_at": resolved_at,
                "resolved_by_user_id": owner_user_id,
                "comment": comment,
                "execution_status": execution_status,
            }
        )
        self.approvals[approval_id] = resolved
        return ApprovalResolution(approval=resolved, resolved_now=True)

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
        approval = self.approvals.get(approval_id)
        if approval is None or (
            approval.session_id != session_id
            or approval.workflow_run_id != workflow_run_id
            or approval.owner_user_id != owner_user_id
            or approval.workflow_type is not workflow_type
            or approval.stage is not expected_stage
            or approval.stage_version != expected_stage_version
            or approval.tool_name != tool_name
            or approval.arguments_hash != arguments_hash
        ):
            return None
        if approval.execution_status is not ExecutionStatus.NOT_STARTED:
            return ApprovalExecutionClaim(approval=approval, claimed_now=False)
        claimed = Approval.model_validate(
            {**approval.model_dump(), "execution_status": ExecutionStatus.QUEUED}
        )
        self.approvals[approval_id] = claimed
        self.claims.append(approval_id)
        return ApprovalExecutionClaim(approval=claimed, claimed_now=True)

    async def record_execution_result(
        self, *, approval_id: UUID, result: ToolExecutionResult
    ) -> Approval | None:
        approval = self.approvals.get(approval_id)
        if approval is None or approval.execution_status is not ExecutionStatus.QUEUED:
            return None
        persisted = Approval.model_validate(
            {**approval.model_dump(), "execution_status": result.status}
        )
        self.approvals[approval_id] = persisted
        self.results[approval_id] = result
        return persisted

    async def get_execution_result(
        self,
        *,
        approval_id: UUID,
        session_id: UUID,
        workflow_run_id: UUID,
        owner_user_id: UUID,
    ) -> ToolExecutionResult | None:
        approval = self.approvals.get(approval_id)
        if approval is None or (
            approval.session_id != session_id
            or approval.workflow_run_id != workflow_run_id
            or approval.owner_user_id != owner_user_id
        ):
            return None
        return self.results.get(approval_id)


def _registry() -> tuple[
    ToolRegistry, RecordingApprovalStore, RecordingArtifactExecutor, RecordingSandboxExecutor
]:
    approvals = RecordingApprovalStore()
    artifacts = RecordingArtifactExecutor()
    sandbox = RecordingSandboxExecutor()
    return ToolRegistry(approvals, artifacts, sandbox), approvals, artifacts, sandbox


def _waiting_run(
    workflow_type: WorkflowType = WorkflowType.INSPECTION_ANALYSIS,
) -> WorkflowRun:
    return WorkflowRun(
        workflow_run_id=uuid4(),
        session_id=uuid4(),
        owner_user_id=uuid4(),
        workflow_type=workflow_type,
        stage=WorkflowStage.AWAITING_APPROVAL,
        stage_version=4,
        status=WorkflowRunStatus.WAITING_FOR_APPROVAL,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _export_proposal(draft_id: UUID | None = None) -> ProposedToolCall:
    return ProposedToolCall(
        tool_name="request_document_export",
        arguments={"draftId": str(draft_id or uuid4()), "formats": [ArtifactFormat.DOCX.value]},
        explanation="The user requested an editable local draft.",
    )


def _approved(approval: Approval) -> Approval:
    return Approval.model_validate(
        {
            **approval.model_dump(),
            "status": ApprovalStatus.APPROVED,
            "decision": ApprovalDecision.APPROVED,
            "resolved_at": datetime.now(UTC),
            "resolved_by_user_id": uuid4(),
            "execution_status": ExecutionStatus.NOT_STARTED,
        }
    )


def _rejected(approval: Approval) -> Approval:
    return Approval.model_validate(
        {
            **approval.model_dump(),
            "status": ApprovalStatus.REJECTED,
            "decision": ApprovalDecision.REJECTED,
            "resolved_at": datetime.now(UTC),
            "resolved_by_user_id": uuid4(),
            "execution_status": ExecutionStatus.NOT_APPLICABLE,
        }
    )


def test_registry_exposes_only_stage_eligible_tools() -> None:
    """The planner never receives a shell or a tool from another workflow."""

    registry, _, _, _ = _registry()

    assert [tool.name for tool in registry.definitions_for(
        WorkflowType.INSPECTION_ANALYSIS, WorkflowStage.VALIDATING
    )] == ["request_document_export"]
    assert [tool.name for tool in registry.definitions_for(
        WorkflowType.CODE_REPAIR, WorkflowStage.REPAIRING
    )] == ["run_sandbox"]
    assert registry.definitions_for(WorkflowType.INSPECTION_ANALYSIS, WorkflowStage.DRAFTING) == ()


@pytest.mark.parametrize(
    "proposal",
    [
        ProposedToolCall(tool_name="delete_everything", arguments={}, explanation="No."),
        ProposedToolCall(
            tool_name="request_document_export",
            arguments={"draftId": str(uuid4()), "formats": [], "command": "ignored"},
            explanation="No raw commands.",
        ),
        ProposedToolCall(
            tool_name="run_sandbox",
            arguments={
                "workspaceId": str(uuid4()),
                "sourceFileId": str(uuid4()),
                "language": "python",
                "command": "python unapproved.py",
            },
            explanation="No raw commands.",
        ),
    ],
)
def test_unknown_or_malformed_proposals_are_rejected(proposal: ProposedToolCall) -> None:
    """Unknown fields such as command are rejected by the typed argument schema."""

    registry, _, _, _ = _registry()

    with pytest.raises(ToolValidationError):
        registry.validate_proposal(
            proposal,
            workflow_type=(
                WorkflowType.CODE_REPAIR
                if proposal.tool_name == "run_sandbox"
                else WorkflowType.INSPECTION_ANALYSIS
            ),
            stage=(
                WorkflowStage.PLANNING
                if proposal.tool_name == "run_sandbox"
                else WorkflowStage.VALIDATING
            ),
        )


def test_wrong_proposal_stage_is_rejected() -> None:
    """Export cannot be proposed before the draft passes validation."""

    registry, _, _, _ = _registry()

    with pytest.raises(ToolValidationError):
        registry.validate_proposal(
            _export_proposal(),
            workflow_type=WorkflowType.INSPECTION_ANALYSIS,
            stage=WorkflowStage.DRAFTING,
        )


async def test_approved_call_reaches_only_the_bound_executor() -> None:
    """A matching approved intent dispatches to the document executor exactly once."""

    registry, approvals, artifacts, sandbox = _registry()
    workflow_run = _waiting_run()
    call = registry.validate_proposal(
        _export_proposal(),
        workflow_type=WorkflowType.INSPECTION_ANALYSIS,
        stage=WorkflowStage.VALIDATING,
    )
    approval = registry.create_pending_approval(
        call,
        workflow_run=workflow_run,
    )
    approved = _approved(approval)
    approvals.approvals[approval.approval_id] = approved

    result = await registry.execute_approved(
        call,
        approval=approved,
        workflow_run=workflow_run,
    )

    assert result.dispatched_now is True
    assert result.result is not None
    assert result.result.status is ExecutionStatus.COMPLETED
    assert len(artifacts.calls) == 1
    assert sandbox.calls == []


@pytest.mark.parametrize("case", ["missing", "rejected", "tampered", "wrong_stage"])
async def test_invalid_approval_never_reaches_an_executor(case: str) -> None:
    """Missing, rejected, changed, and stale approval records stop before side effects."""

    registry, approvals, artifacts, sandbox = _registry()
    workflow_run = _waiting_run()
    call = registry.validate_proposal(
        _export_proposal(),
        workflow_type=WorkflowType.INSPECTION_ANALYSIS,
        stage=WorkflowStage.VALIDATING,
    )
    approval = registry.create_pending_approval(
        call,
        workflow_run=workflow_run,
    )
    approved = _approved(approval)
    supplied: Approval | None = approved
    approvals.approvals[approval.approval_id] = approved
    if case == "missing":
        supplied = None
    elif case == "rejected":
        supplied = _rejected(approval)
    elif case == "tampered":
        supplied = Approval.model_validate(
            {
                **_approved(approval).model_dump(),
                "normalized_arguments": {"draftId": str(uuid4()), "formats": ["docx"]},
            }
        )
    elif case == "wrong_stage":
        workflow_run = WorkflowRun(
            workflow_run_id=workflow_run.workflow_run_id,
            session_id=workflow_run.session_id,
            owner_user_id=workflow_run.owner_user_id,
            workflow_type=workflow_run.workflow_type,
            stage=WorkflowStage.EXPORTING,
            stage_version=workflow_run.stage_version + 1,
            status=WorkflowRunStatus.ACTIVE,
            created_at=workflow_run.created_at,
            updated_at=datetime.now(UTC),
        )

    with pytest.raises(ApprovalPolicyError):
        await registry.execute_approved(
            call,
            approval=supplied,
            workflow_run=workflow_run,
        )

    assert artifacts.calls == []
    assert sandbox.calls == []


async def test_retry_returns_a_durable_result_without_a_second_dispatch() -> None:
    """Only the atomic claimer executes; a retry receives the recorded result."""

    registry, approvals, artifacts, _ = _registry()
    workflow_run = _waiting_run()
    call = registry.validate_proposal(
        _export_proposal(),
        workflow_type=WorkflowType.INSPECTION_ANALYSIS,
        stage=WorkflowStage.VALIDATING,
    )
    approval = registry.create_pending_approval(call, workflow_run=workflow_run)
    approved = _approved(approval)
    approvals.approvals[approval.approval_id] = approved

    first = await registry.execute_approved(call, approval=approved, workflow_run=workflow_run)
    retry = await registry.execute_approved(
        call,
        approval=first.approval,
        workflow_run=workflow_run,
    )

    assert first.dispatched_now is True
    assert retry.dispatched_now is False
    assert retry.result == first.result
    assert len(artifacts.calls) == 1


def test_tool_cannot_create_an_approval_for_another_workflow_type() -> None:
    """A directly constructed call cannot bypass the registry's workflow binding."""

    registry, _, _, _ = _registry()
    sandbox_call = registry.validate_proposal(
        ProposedToolCall(
            tool_name="run_sandbox",
            arguments={
                "workspaceId": str(uuid4()),
                "sourceFileId": str(uuid4()),
                "language": "python",
            },
            explanation="Run the approved repair task.",
        ),
        workflow_type=WorkflowType.CODE_REPAIR,
        stage=WorkflowStage.PLANNING,
    )

    with pytest.raises(ApprovalPolicyError):
        registry.create_pending_approval(sandbox_call, workflow_run=_waiting_run())
