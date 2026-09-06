"""Immutable state and event contracts for Backend 1 workflows."""

import json
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import (
    AfterValidator,
    Field,
    JsonValue,
    ValidationInfo,
    field_validator,
    model_validator,
)

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


_WINDOWS_RESERVED_FILE_NAMES = frozenset(
    {
        "AUX",
        "CON",
        "NUL",
        "PRN",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
)
_ACTIVITY_EVENT_MAX_PAYLOAD_BYTES = 8 * 1024


class _EmptyActivityPayload(ApiContractModel):
    pass


class _UploadAcceptedPayload(ApiContractModel):
    upload_id: UUID
    source_id: UUID
    file_name: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=1)

    @field_validator("file_name")
    @classmethod
    def require_safe_file_name(cls, value: str) -> str:
        windows_stem = value.split(".", maxsplit=1)[0].upper()
        if (
            value in {".", ".."}
            or any(character in '<>:"/\\|?*' for character in value)
            or any(ord(character) < 32 for character in value)
            or value.endswith((" ", "."))
            or windows_stem in _WINDOWS_RESERVED_FILE_NAMES
        ):
            raise ValueError("fileName must be one safe local path component")
        return value


class _MessageActivityPayload(ApiContractModel):
    message_id: UUID


class _StageChangedPayload(ApiContractModel):
    previous_stage: WorkflowStage
    stage: WorkflowStage
    stage_version: int = Field(ge=0)
    status: WorkflowRunStatus


class _WorkflowProgressPayload(ApiContractModel):
    stage: WorkflowStage
    completed_units: int = Field(ge=0)
    total_units: int = Field(ge=1)

    @model_validator(mode="after")
    def require_bounded_progress(self) -> "_WorkflowProgressPayload":
        if self.completed_units > self.total_units:
            raise ValueError("completedUnits must not exceed totalUnits")
        return self


class _ApprovalRequiredPayload(ApiContractModel):
    approval_id: UUID


class _ApprovalResolvedPayload(ApiContractModel):
    approval_id: UUID
    decision: ApprovalDecision


class _ArtifactCreatedPayload(ApiContractModel):
    artifact_id: UUID


class _SandboxCompletedPayload(ApiContractModel):
    status: ExecutionStatus
    exit_code: int | None = None
    passed: bool
    failure_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        pattern=r"^[a-z][a-z0-9_]*$",
    )

    @model_validator(mode="after")
    def require_terminal_result(self) -> "_SandboxCompletedPayload":
        if self.status is ExecutionStatus.COMPLETED:
            if self.exit_code != 0 or not self.passed or self.failure_code is not None:
                raise ValueError("completed sandbox metadata must report a passing exit")
        elif self.status is ExecutionStatus.FAILED:
            if self.passed or self.failure_code is None:
                raise ValueError("failed sandbox metadata requires a failure code")
        else:
            raise ValueError("sandbox activity status must be completed or failed")
        return self


class _WorkflowFailedPayload(ApiContractModel):
    stage: WorkflowStage
    failure_code: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z][a-z0-9_]*$",
    )


_ACTIVITY_PAYLOAD_MODELS: dict[ActivityEventType, type[ApiContractModel]] = {
    ActivityEventType.SESSION_CREATED: _EmptyActivityPayload,
    ActivityEventType.UPLOAD_ACCEPTED: _UploadAcceptedPayload,
    ActivityEventType.MESSAGE_ACCEPTED: _MessageActivityPayload,
    ActivityEventType.STAGE_CHANGED: _StageChangedPayload,
    ActivityEventType.PROGRESS: _WorkflowProgressPayload,
    ActivityEventType.MESSAGE_COMPLETED: _MessageActivityPayload,
    ActivityEventType.APPROVAL_REQUIRED: _ApprovalRequiredPayload,
    ActivityEventType.APPROVAL_RESOLVED: _ApprovalResolvedPayload,
    ActivityEventType.ARTIFACT_CREATED: _ArtifactCreatedPayload,
    ActivityEventType.SANDBOX_COMPLETED: _SandboxCompletedPayload,
    ActivityEventType.WORKFLOW_FAILED: _WorkflowFailedPayload,
}


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
    # Renderer-supplied idempotency key; null only for sessions stored before
    # the key existed. A retry of the same create returns the stored session.
    client_session_id: UUID | None = None

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

    @field_validator("payload")
    @classmethod
    def validate_and_normalize_payload(
        cls,
        value: dict[str, JsonValue],
        info: ValidationInfo,
    ) -> dict[str, JsonValue]:
        """Allow only bounded, event-specific, user-visible metadata."""

        event_type = info.data.get("event_type")
        if not isinstance(event_type, ActivityEventType):
            raise ValueError("eventType must be validated before payload")
        validated = _ACTIVITY_PAYLOAD_MODELS[event_type].model_validate(value)
        canonical = validated.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
        serialized = json.dumps(
            canonical,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(serialized.encode("utf-8")) > _ACTIVITY_EVENT_MAX_PAYLOAD_BYTES:
            raise ValueError("canonical activity payload must not exceed 8 KiB")
        return canonical


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
    execution_claim_token: UUID | None = None

    @model_validator(mode="after")
    def require_token_for_new_claim(self) -> "ApprovalExecutionClaim":
        """Expose a token only to the dispatcher that won the claim."""

        if self.claimed_now != (self.execution_claim_token is not None):
            raise ValueError("only a new execution claim may include a claim token")
        return self
