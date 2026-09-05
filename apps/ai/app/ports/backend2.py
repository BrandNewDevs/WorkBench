"""Narrow Backend 2 persistence and execution interfaces agreed before routes exist."""

from collections.abc import AsyncIterable, AsyncIterator
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import Field

from app.ai.schemas import ApprovedPath
from app.api.contracts import ApiContractModel
from app.auth.contracts import UserRole
from app.tools.contracts import (
    DocumentExportExecutionRequest,
    DocumentExportResult,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    ToolExecutionResult,
)
from app.workflow.contracts import (
    ActivityEvent,
    Approval,
    ApprovalDecision,
    ApprovalExecutionClaim,
    ApprovalResolution,
    UtcTimestamp,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowSession,
    WorkflowStage,
    WorkflowType,
)


class StoredIdentity(ApiContractModel):
    """Seeded local identity data provided to the future auth service."""

    user_id: UUID
    username: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=200)
    role: UserRole
    password_hash: str = Field(min_length=1)
    disabled: bool = False


class AuthSessionRecord(ApiContractModel):
    """Revocable JWT session metadata; tokens themselves are never stored here."""

    auth_session_id: UUID
    user_id: UUID
    token_id: UUID
    expires_at: UtcTimestamp
    revoked_at: UtcTimestamp | None = None


class WorkflowMessage(ApiContractModel):
    """Persistable conversation metadata without model reasoning."""

    message_id: UUID
    session_id: UUID
    author_user_id: UUID | None = None
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=20_000)
    created_at: UtcTimestamp


class StoredUpload(ApiContractModel):
    """Safe upload metadata; the approved path remains Backend 2 controlled."""

    upload_id: UUID
    session_id: UUID
    source_id: UUID
    file_name: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: UtcTimestamp


class AuditAction(StrEnum):
    """Security-relevant metadata categories safe to persist in local audit data."""

    AUTHENTICATION = "authentication"
    SESSION_CREATED = "sessionCreated"
    UPLOAD_ACCEPTED = "uploadAccepted"
    APPROVAL_REQUESTED = "approvalRequested"
    APPROVAL_RESOLVED = "approvalResolved"
    TOOL_EXECUTION = "toolExecution"
    WORKFLOW_FAILED = "workflowFailed"


class AuditRecord(ApiContractModel):
    """Sanitized audit metadata that excludes passwords, tokens, and source bodies."""

    audit_id: UUID
    action: AuditAction
    actor_user_id: UUID | None = None
    session_id: UUID | None = None
    workflow_run_id: UUID | None = None
    outcome: str = Field(min_length=1, max_length=100)
    occurred_at: UtcTimestamp


class SubsystemReadiness(ApiContractModel):
    """A non-sensitive local dependency health result for the future health route."""

    ready: bool
    detail: str | None = Field(default=None, max_length=500)


class SystemHealthReport(ApiContractModel):
    """Backend 2 readiness facts without network probing or endpoint behavior."""

    storage: SubsystemReadiness
    sandbox: SubsystemReadiness
    audit: SubsystemReadiness
    outbound_network_blocked: bool


class IdentityStore(Protocol):
    """Load pre-seeded local accounts; Backend 2 owns the SQLite implementation."""

    async def get_by_username(self, username: str) -> StoredIdentity | None:
        """Return an identity by normalized username."""
        ...

    async def get_by_id(self, user_id: UUID) -> StoredIdentity | None:
        """Return an identity by immutable user ID for session restoration."""
        ...


class AuthSessionStore(Protocol):
    """Persist and revoke server-side JWT session metadata."""

    async def create(self, record: AuthSessionRecord) -> None:
        """Persist a newly issued auth session."""
        ...

    async def get_active(self, token_id: UUID, now: UtcTimestamp) -> AuthSessionRecord | None:
        """Return an unexpired, non-revoked token session."""
        ...

    async def revoke(self, token_id: UUID, revoked_at: UtcTimestamp) -> bool:
        """Revoke the server-side token record."""
        ...


class WorkflowStore(Protocol):
    """Own durable workflow data while Backend 1 owns every transition decision."""

    async def create_session(self, session: WorkflowSession) -> WorkflowSession:
        """Atomically persist a newly created workflow session."""
        ...

    async def create_run(self, run: WorkflowRun) -> WorkflowRun:
        """Persist a run after Backend 1 selects its initial stage."""
        ...

    async def append_message(self, message: WorkflowMessage) -> WorkflowMessage:
        """Append a sanitized user or assistant message."""
        ...

    async def compare_and_set_stage(
        self,
        *,
        session_id: UUID,
        workflow_run_id: UUID,
        expected_stage: WorkflowStage,
        expected_stage_version: int,
        next_stage: WorkflowStage,
        next_status: WorkflowRunStatus,
        sandbox_attempts: int,
    ) -> WorkflowRun | None:
        """Atomically advance a run only when its expected state still matches."""
        ...


class SessionFileStore(Protocol):
    """Stream user-selected files into a contained local session workspace."""

    async def save_upload(
        self,
        *,
        session: WorkflowSession,
        upload_id: UUID,
        source_id: UUID,
        file_name: str,
        content: AsyncIterable[bytes],
    ) -> StoredUpload:
        """Atomically store validated upload content and metadata."""
        ...

    async def resolve_approved_path(
        self, upload_id: UUID, session_id: UUID
    ) -> ApprovedPath | None:
        """Return the exact stored input path after ownership checks."""
        ...


class ActivityEventStore(Protocol):
    """Persist ordered session events and make them available for replay/live delivery."""

    async def append(self, event: ActivityEvent) -> ActivityEvent:
        """Append one event with a Backend 2-assigned per-session sequence."""
        ...

    async def replay(self, session_id: UUID, after_event_id: int) -> list[ActivityEvent]:
        """Return durable events after the supplied event sequence."""
        ...

    def subscribe(self, session_id: UUID) -> AsyncIterator[ActivityEvent]:
        """Subscribe to new session events without embedding an SSE transport."""
        ...


class ApprovalStore(Protocol):
    """Persist immutable approval intent and resolve it with compare-and-set semantics."""

    async def create_pending(self, approval: Approval) -> Approval:
        """Persist a pending approval exactly as Backend 1 validated it."""
        ...

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
        """Resolve once or return the matching prior resolution without rerunning work."""
        ...

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
        """Atomically change a matching approved intent from not-started to queued."""
        ...

    async def record_execution_result(
        self, *, approval_id: UUID, result: ToolExecutionResult
    ) -> Approval | None:
        """Durably attach an executor result to the approval claimed by this process."""
        ...

    async def get_execution_result(
        self,
        *,
        approval_id: UUID,
        session_id: UUID,
        workflow_run_id: UUID,
        owner_user_id: UUID,
    ) -> ToolExecutionResult | None:
        """Return the durable result for an already-claimed matching approval."""
        ...


class AuditStore(Protocol):
    """Persist sanitized security metadata without content or secrets."""

    async def append(self, record: AuditRecord) -> None:
        """Append one audit record."""
        ...


class SystemHealthProvider(Protocol):
    """Report Backend 2 local dependency readiness for a future health route."""

    async def health(self) -> SystemHealthReport:
        """Return local storage, sandbox, audit, and outbound-network state."""
        ...


class ArtifactExecutor(Protocol):
    """Create only formats already bound into an approved export request."""

    async def create_artifacts(
        self, request: DocumentExportExecutionRequest
    ) -> DocumentExportResult:
        """Render the approved draft into its exact requested formats."""
        ...


class SandboxExecutor(Protocol):
    """Run the typed request in Backend 2's network-disabled sandbox."""

    async def run(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        """Execute no broader action than the approved typed request."""
        ...
