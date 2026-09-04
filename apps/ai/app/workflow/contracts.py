"""Immutable state and event contracts for Backend 1 workflows."""

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, Field, JsonValue, field_validator, model_validator

from app.api.contracts import ApiContractModel


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value.astimezone(UTC)


UtcTimestamp = Annotated[datetime, AfterValidator(_require_utc)]


class WorkflowType(StrEnum):
    """The two deterministic MVP workflows."""

    INSPECTION_ANALYSIS = "inspectionAnalysis"
    CODE_REPAIR = "codeRepair"


class WorkflowStage(StrEnum):
    """Stages controlled by Backend 1 rather than by a planner."""

    COLLECTING_INPUTS = "collectingInputs"
    EXTRACTING = "extracting"
    RETRIEVING = "retrieving"
    DRAFTING = "drafting"
    VALIDATING = "validating"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaitingApproval"
    EXPORTING = "exporting"
    SANDBOX_EXECUTING = "sandboxExecuting"
    REPAIRING = "repairing"
    APPROVAL_REJECTED = "approvalRejected"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowStatus(StrEnum):
    """High-level state shown beside a more specific workflow stage."""

    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    APPROVAL_REJECTED = "approvalRejected"


class WorkflowRunStatus(StrEnum):
    """Lifecycle status of one immutable workflow run record."""

    QUEUED = "queued"
    ACTIVE = "active"
    WAITING_FOR_APPROVAL = "waitingForApproval"
    COMPLETED = "completed"
    FAILED = "failed"
    APPROVAL_REJECTED = "approvalRejected"


class ActivityEventType(StrEnum):
    """Durable event names intended for a later SSE transport."""

    SESSION_CREATED = "session.created"
    UPLOAD_ACCEPTED = "upload.accepted"
    MESSAGE_ACCEPTED = "message.accepted"
    STAGE_CHANGED = "workflow.stageChanged"
    PROGRESS = "workflow.progress"
    MESSAGE_COMPLETED = "message.completed"
    APPROVAL_REQUIRED = "approval.required"
    APPROVAL_RESOLVED = "approval.resolved"
    ARTIFACT_CREATED = "artifact.created"
    SANDBOX_COMPLETED = "sandbox.completed"
    WORKFLOW_FAILED = "workflow.failed"


class ApprovalStatus(StrEnum):
    """An approval is immutable except for its one atomic resolution."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalDecision(StrEnum):
    """The only user choices available for a pending side effect."""

    APPROVED = "approved"
    REJECTED = "rejected"


class ExecutionStatus(StrEnum):
    """Result state after an approval has been resolved."""

    NOT_APPLICABLE = "notApplicable"
    NOT_STARTED = "notStarted"
    QUEUED = "queued"
    COMPLETED = "completed"
    FAILED = "failed"


_INSPECTION_STAGES = frozenset(
    {
        WorkflowStage.COLLECTING_INPUTS,
        WorkflowStage.EXTRACTING,
        WorkflowStage.RETRIEVING,
        WorkflowStage.DRAFTING,
        WorkflowStage.VALIDATING,
        WorkflowStage.AWAITING_APPROVAL,
        WorkflowStage.EXPORTING,
        WorkflowStage.APPROVAL_REJECTED,
        WorkflowStage.COMPLETED,
        WorkflowStage.FAILED,
    }
)
_CODE_REPAIR_STAGES = frozenset(
    {
        WorkflowStage.COLLECTING_INPUTS,
        WorkflowStage.PLANNING,
        WorkflowStage.AWAITING_APPROVAL,
        WorkflowStage.SANDBOX_EXECUTING,
        WorkflowStage.REPAIRING,
        WorkflowStage.APPROVAL_REJECTED,
        WorkflowStage.COMPLETED,
        WorkflowStage.FAILED,
    }
)
_TERMINAL_STAGES = frozenset(
    {WorkflowStage.APPROVAL_REJECTED, WorkflowStage.COMPLETED, WorkflowStage.FAILED}
)


def _stage_matches_workflow(workflow_type: WorkflowType, stage: WorkflowStage) -> bool:
    allowed_stages = (
        _INSPECTION_STAGES
        if workflow_type is WorkflowType.INSPECTION_ANALYSIS
        else _CODE_REPAIR_STAGES
    )
    return stage in allowed_stages


def _status_matches_stage(status: WorkflowStatus, stage: WorkflowStage) -> bool:
    return (
        (status is WorkflowStatus.ACTIVE and stage not in _TERMINAL_STAGES)
        or (status is WorkflowStatus.COMPLETED and stage is WorkflowStage.COMPLETED)
        or (status is WorkflowStatus.FAILED and stage is WorkflowStage.FAILED)
        or (
            status is WorkflowStatus.APPROVAL_REJECTED
            and stage is WorkflowStage.APPROVAL_REJECTED
        )
    )


class WorkflowSession(ApiContractModel):
    """Durable user-owned workspace boundary without filesystem details."""

    session_id: UUID
    owner_user_id: UUID
    workflow_type: WorkflowType
    title: str = Field(min_length=1, max_length=200)
    stage: WorkflowStage = WorkflowStage.COLLECTING_INPUTS
    status: WorkflowStatus = WorkflowStatus.ACTIVE
    created_at: UtcTimestamp
    updated_at: UtcTimestamp

    @model_validator(mode="after")
    def require_consistent_workflow_state(self) -> "WorkflowSession":
        if not _stage_matches_workflow(self.workflow_type, self.stage):
            raise ValueError("workflow stage is not valid for this workflow type")
        if not _status_matches_stage(self.status, self.stage):
            raise ValueError("workflow status does not match its stage")
        return self


class WorkflowRun(ApiContractModel):
    """One versioned run; Backend 2 atomically persists stage changes."""

    workflow_run_id: UUID
    session_id: UUID
    owner_user_id: UUID
    workflow_type: WorkflowType
    stage: WorkflowStage
    stage_version: int = Field(ge=0)
    status: WorkflowRunStatus
    sandbox_attempts: int = Field(default=0, ge=0)
    created_at: UtcTimestamp
    updated_at: UtcTimestamp

    @model_validator(mode="after")
    def require_consistent_workflow_state(self) -> "WorkflowRun":
        if not _stage_matches_workflow(self.workflow_type, self.stage):
            raise ValueError("workflow stage is not valid for this workflow type")
        if (
            self.status is WorkflowRunStatus.QUEUED
            and self.stage is not WorkflowStage.COLLECTING_INPUTS
        ):
            raise ValueError("queued workflow runs must remain at collecting inputs")
        if self.status is WorkflowRunStatus.WAITING_FOR_APPROVAL:
            if self.stage is not WorkflowStage.AWAITING_APPROVAL:
                raise ValueError("waiting workflow runs must be awaiting approval")
        elif self.status is WorkflowRunStatus.COMPLETED:
            if self.stage is not WorkflowStage.COMPLETED:
                raise ValueError("completed workflow runs must be completed")
        elif self.status is WorkflowRunStatus.FAILED:
            if self.stage is not WorkflowStage.FAILED:
                raise ValueError("failed workflow runs must be failed")
        elif self.status is WorkflowRunStatus.APPROVAL_REJECTED:
            if self.stage is not WorkflowStage.APPROVAL_REJECTED:
                raise ValueError("rejected workflow runs must be approval rejected")
        elif self.status is WorkflowRunStatus.ACTIVE and self.stage in _TERMINAL_STAGES | {
            WorkflowStage.AWAITING_APPROVAL
        }:
            raise ValueError("active workflow runs must be in an active operation stage")
        if self.workflow_type is WorkflowType.INSPECTION_ANALYSIS and self.sandbox_attempts != 0:
            raise ValueError("inspection workflow runs cannot have sandbox attempts")
        if self.stage is WorkflowStage.SANDBOX_EXECUTING and self.sandbox_attempts < 1:
            raise ValueError("sandbox execution requires a recorded sandbox attempt")
        if self.stage is WorkflowStage.REPAIRING and self.sandbox_attempts != 1:
            raise ValueError("repairing requires exactly one prior sandbox attempt")
        if self.stage is WorkflowStage.COMPLETED and (
            self.workflow_type is WorkflowType.CODE_REPAIR and self.sandbox_attempts < 2
        ):
            raise ValueError("completed code repair requires a successful approved rerun")
        return self


class ActivityEvent(ApiContractModel):
    """Ordered, replayable session activity without confidential source contents."""

    event_id: int = Field(ge=0)
    session_id: UUID
    workflow_run_id: UUID | None = None
    event_type: ActivityEventType
    occurred_at: UtcTimestamp
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class Approval(ApiContractModel):
    """Exact, user-owned execution intent captured before a side effect."""

    approval_id: UUID
    session_id: UUID
    workflow_run_id: UUID
    owner_user_id: UUID
    workflow_type: WorkflowType
    stage: WorkflowStage
    stage_version: int = Field(ge=0)
    tool_name: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
    normalized_arguments: dict[str, JsonValue]
    arguments_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: UtcTimestamp
    resolved_at: UtcTimestamp | None = None
    resolved_by_user_id: UUID | None = None
    decision: ApprovalDecision | None = None
    comment: str | None = Field(default=None, max_length=1000)
    execution_status: ExecutionStatus = ExecutionStatus.NOT_STARTED

    @field_validator("comment")
    @classmethod
    def normalize_comment(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("comment must not be blank when supplied")
        return normalized

    @model_validator(mode="after")
    def require_consistent_resolution(self) -> "Approval":
        is_pending = self.status is ApprovalStatus.PENDING
        resolution_fields_present = (
            self.resolved_at is not None
            or self.resolved_by_user_id is not None
            or self.decision is not None
        )
        if is_pending and resolution_fields_present:
            raise ValueError("pending approval must not contain a resolution")
        if is_pending and self.execution_status is not ExecutionStatus.NOT_STARTED:
            raise ValueError("pending approval cannot have an execution status")
        if not is_pending and (
            self.resolved_at is None
            or self.resolved_by_user_id is None
            or self.decision is None
        ):
            raise ValueError("resolved approval requires timestamp, user, and decision")
        if self.status is ApprovalStatus.REJECTED:
            if self.decision is not ApprovalDecision.REJECTED:
                raise ValueError("rejected approval requires a rejected decision")
            if self.execution_status is not ExecutionStatus.NOT_APPLICABLE:
                raise ValueError("rejected approval cannot have an execution status")
        if (
            self.status is ApprovalStatus.APPROVED
            and self.decision is not ApprovalDecision.APPROVED
        ):
            raise ValueError("approved approval requires an approved decision")
        if (
            self.status is ApprovalStatus.APPROVED
            and self.execution_status is ExecutionStatus.NOT_APPLICABLE
        ):
            raise ValueError("approved approval requires an executable status")
        return self


class ApprovalResolution(ApiContractModel):
    """Outcome of an atomic approval-resolution attempt.

    A retry receives the existing immutable approval with ``resolved_now`` set
    to false, allowing the future endpoint to return the prior result without
    dispatching the side effect a second time.
    """

    approval: Approval
    resolved_now: bool


class ApprovalExecutionClaim(ApiContractModel):
    """Outcome of atomically reserving one approved side effect for execution."""

    approval: Approval
    claimed_now: bool
