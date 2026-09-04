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
)
from app.tools.registry import ApprovalPolicyError, ToolRegistry, ToolValidationError
from app.workflow.contracts import (
    Approval,
    ApprovalDecision,
    ApprovalStatus,
    ExecutionStatus,
    WorkflowStage,
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


@dataclass(frozen=True)
class PolicyContext:
    """Stable IDs used to make exact approval binding assertions readable."""

    session_id: UUID = field(default_factory=uuid4)
    workflow_run_id: UUID = field(default_factory=uuid4)
    owner_user_id: UUID = field(default_factory=uuid4)


def _registry() -> tuple[ToolRegistry, RecordingArtifactExecutor, RecordingSandboxExecutor]:
    artifacts = RecordingArtifactExecutor()
    sandbox = RecordingSandboxExecutor()
    return ToolRegistry(artifacts, sandbox), artifacts, sandbox


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
            "execution_status": ExecutionStatus.QUEUED,
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

    registry, _, _ = _registry()

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

    registry, _, _ = _registry()

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

    registry, _, _ = _registry()

    with pytest.raises(ToolValidationError):
        registry.validate_proposal(
            _export_proposal(),
            workflow_type=WorkflowType.INSPECTION_ANALYSIS,
            stage=WorkflowStage.DRAFTING,
        )


async def test_approved_call_reaches_only_the_bound_executor() -> None:
    """A matching approved intent dispatches to the document executor exactly once."""

    registry, artifacts, sandbox = _registry()
    context = PolicyContext()
    call = registry.validate_proposal(
        _export_proposal(),
        workflow_type=WorkflowType.INSPECTION_ANALYSIS,
        stage=WorkflowStage.VALIDATING,
    )
    approval = registry.create_pending_approval(
        call,
        session_id=context.session_id,
        workflow_run_id=context.workflow_run_id,
        owner_user_id=context.owner_user_id,
        stage=WorkflowStage.AWAITING_APPROVAL,
        stage_version=4,
    )

    result = await registry.execute_approved(
        call,
        approval=_approved(approval),
        session_id=context.session_id,
        workflow_run_id=context.workflow_run_id,
        owner_user_id=context.owner_user_id,
        stage=WorkflowStage.AWAITING_APPROVAL,
        stage_version=4,
    )

    assert result.status is ExecutionStatus.COMPLETED
    assert len(artifacts.calls) == 1
    assert sandbox.calls == []


@pytest.mark.parametrize("case", ["missing", "rejected", "tampered", "wrong_stage"])
async def test_invalid_approval_never_reaches_an_executor(case: str) -> None:
    """Missing, rejected, changed, and stale approval records stop before side effects."""

    registry, artifacts, sandbox = _registry()
    context = PolicyContext()
    call = registry.validate_proposal(
        _export_proposal(),
        workflow_type=WorkflowType.INSPECTION_ANALYSIS,
        stage=WorkflowStage.VALIDATING,
    )
    approval = registry.create_pending_approval(
        call,
        session_id=context.session_id,
        workflow_run_id=context.workflow_run_id,
        owner_user_id=context.owner_user_id,
        stage=WorkflowStage.AWAITING_APPROVAL,
        stage_version=4,
    )
    supplied: Approval | None = _approved(approval)
    stage = WorkflowStage.AWAITING_APPROVAL
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
        stage = WorkflowStage.EXPORTING

    with pytest.raises(ApprovalPolicyError):
        await registry.execute_approved(
            call,
            approval=supplied,
            session_id=context.session_id,
            workflow_run_id=context.workflow_run_id,
            owner_user_id=context.owner_user_id,
            stage=stage,
            stage_version=4,
        )

    assert artifacts.calls == []
    assert sandbox.calls == []
