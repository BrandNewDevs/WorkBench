"""Deterministic registry and approval checks for the only MVP side effects."""

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast
from uuid import UUID, uuid4

from pydantic import JsonValue, ValidationError

from app.ai.schemas import ProposedToolCall, ToolDefinition
from app.api.contracts import ApiContractModel
from app.ports.backend2 import ArtifactExecutor, SandboxExecutor
from app.tools.contracts import (
    DocumentExportArguments,
    DocumentExportExecutionRequest,
    SandboxArguments,
    SandboxExecutionRequest,
    ToolArguments,
    ToolExecutionResult,
    ToolName,
    ValidatedToolCall,
)
from app.workflow.contracts import (
    Approval,
    ApprovalStatus,
    ExecutionStatus,
    WorkflowStage,
    WorkflowType,
)


class ToolValidationError(ValueError):
    """A proposed tool is unknown, malformed, or unavailable at this stage."""


class ApprovalPolicyError(PermissionError):
    """An execution intent is missing, rejected, stale, or has been altered."""


class _RegisteredTool(ApiContractModel):
    """Static policy facts for one approved tool, not an LLM-supplied definition."""

    tool_name: ToolName
    workflow_type: WorkflowType
    proposal_stages: tuple[WorkflowStage, ...]
    execution_stages: tuple[WorkflowStage, ...]
    approval_required: bool = True


_REGISTERED_TOOLS: dict[ToolName, _RegisteredTool] = {
    ToolName.REQUEST_DOCUMENT_EXPORT: _RegisteredTool(
        tool_name=ToolName.REQUEST_DOCUMENT_EXPORT,
        workflow_type=WorkflowType.INSPECTION_ANALYSIS,
        proposal_stages=(WorkflowStage.VALIDATING,),
        execution_stages=(WorkflowStage.AWAITING_APPROVAL,),
    ),
    ToolName.RUN_SANDBOX: _RegisteredTool(
        tool_name=ToolName.RUN_SANDBOX,
        workflow_type=WorkflowType.CODE_REPAIR,
        proposal_stages=(WorkflowStage.PLANNING, WorkflowStage.REPAIRING),
        execution_stages=(WorkflowStage.AWAITING_APPROVAL,),
    ),
}


def _argument_model(
    tool_name: ToolName,
) -> type[DocumentExportArguments] | type[SandboxArguments]:
    if tool_name is ToolName.REQUEST_DOCUMENT_EXPORT:
        return DocumentExportArguments
    return SandboxArguments


def _normalized_arguments(arguments: ToolArguments) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], arguments.model_dump(by_alias=True, mode="json"))


def argument_hash(arguments: ToolArguments) -> str:
    """Return a canonical SHA-256 binding for typed, JSON-safe arguments."""

    normalized = _normalized_arguments(arguments)
    serialized = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(serialized.encode("utf-8")).hexdigest()


class ToolRegistry:
    """Validate proposals and dispatch only an exact approved execution intent."""

    def __init__(
        self, artifact_executor: ArtifactExecutor, sandbox_executor: SandboxExecutor
    ) -> None:
        self._artifact_executor = artifact_executor
        self._sandbox_executor = sandbox_executor

    def definitions_for(
        self, workflow_type: WorkflowType, stage: WorkflowStage
    ) -> tuple[ToolDefinition, ...]:
        """Expose only deterministic, currently eligible planner tool definitions."""

        definitions: list[ToolDefinition] = []
        for registration in _REGISTERED_TOOLS.values():
            if (
                registration.workflow_type is workflow_type
                and stage in registration.proposal_stages
            ):
                argument_model = _argument_model(registration.tool_name)
                definitions.append(
                    ToolDefinition(
                        name=registration.tool_name.value,
                        description=(
                            "Request the explicitly described local side effect; "
                            "it requires authenticated user approval before execution."
                        ),
                        input_schema=cast(
                            dict[str, JsonValue], argument_model.model_json_schema()
                        ),
                    )
                )
        return tuple(definitions)

    def validate_proposal(
        self,
        proposal: ProposedToolCall,
        *,
        workflow_type: WorkflowType,
        stage: WorkflowStage,
    ) -> ValidatedToolCall:
        """Reject unknown tools, unsafe fields, malformed values, and wrong stages."""

        try:
            tool_name = ToolName(proposal.tool_name)
        except ValueError as error:
            raise ToolValidationError("unknown tool") from error
        registration = _REGISTERED_TOOLS[tool_name]
        if (
            registration.workflow_type is not workflow_type
            or stage not in registration.proposal_stages
        ):
            raise ToolValidationError("tool is not eligible in the current workflow stage")
        try:
            arguments = _argument_model(tool_name).model_validate(proposal.arguments)
            return ValidatedToolCall(tool_name=tool_name, arguments=arguments)
        except ValidationError as error:
            raise ToolValidationError("tool arguments do not match the approved schema") from error

    def create_pending_approval(
        self,
        call: ValidatedToolCall,
        *,
        session_id: UUID,
        workflow_run_id: UUID,
        owner_user_id: UUID,
        stage: WorkflowStage,
        stage_version: int,
        requested_at: datetime | None = None,
        approval_id: UUID | None = None,
    ) -> Approval:
        """Bind normalized intent to the awaiting-approval run state before persistence."""

        registration = _REGISTERED_TOOLS[call.tool_name]
        if stage not in registration.execution_stages:
            raise ApprovalPolicyError("approval is not being created at the tool execution stage")
        timestamp = requested_at if requested_at is not None else datetime.now(UTC)
        return Approval(
            approval_id=approval_id or uuid4(),
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            owner_user_id=owner_user_id,
            stage=stage,
            stage_version=stage_version,
            tool_name=call.tool_name.value,
            normalized_arguments=_normalized_arguments(call.arguments),
            arguments_hash=argument_hash(call.arguments),
            requested_at=timestamp,
        )

    async def execute_approved(
        self,
        call: ValidatedToolCall,
        *,
        approval: Approval | None,
        session_id: UUID,
        workflow_run_id: UUID,
        owner_user_id: UUID,
        stage: WorkflowStage,
        stage_version: int,
    ) -> ToolExecutionResult:
        """Dispatch only after the persisted approval exactly matches the current intent."""

        self._assert_execution_approved(
            call,
            approval=approval,
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            owner_user_id=owner_user_id,
            stage=stage,
            stage_version=stage_version,
        )
        assert approval is not None  # Narrowed after the policy guard above.
        if call.tool_name is ToolName.REQUEST_DOCUMENT_EXPORT:
            assert isinstance(call.arguments, DocumentExportArguments)
            return await self._artifact_executor.create_artifacts(
                DocumentExportExecutionRequest(
                    approval_id=approval.approval_id,
                    session_id=session_id,
                    workflow_run_id=workflow_run_id,
                    arguments=call.arguments,
                )
            )
        assert isinstance(call.arguments, SandboxArguments)
        return await self._sandbox_executor.run(
            SandboxExecutionRequest(
                approval_id=approval.approval_id,
                session_id=session_id,
                workflow_run_id=workflow_run_id,
                arguments=call.arguments,
            )
        )

    def _assert_execution_approved(
        self,
        call: ValidatedToolCall,
        *,
        approval: Approval | None,
        session_id: UUID,
        workflow_run_id: UUID,
        owner_user_id: UUID,
        stage: WorkflowStage,
        stage_version: int,
    ) -> None:
        if approval is None:
            raise ApprovalPolicyError("missing approval")
        registration = _REGISTERED_TOOLS[call.tool_name]
        if stage not in registration.execution_stages:
            raise ApprovalPolicyError("tool is not eligible for execution at the current stage")
        if approval.status is not ApprovalStatus.APPROVED:
            raise ApprovalPolicyError("approval is not approved")
        if approval.execution_status not in {ExecutionStatus.NOT_STARTED, ExecutionStatus.QUEUED}:
            raise ApprovalPolicyError("approval has already completed execution")
        if (
            approval.session_id != session_id
            or approval.workflow_run_id != workflow_run_id
            or approval.owner_user_id != owner_user_id
            or approval.stage is not stage
            or approval.stage_version != stage_version
            or approval.tool_name != call.tool_name.value
        ):
            raise ApprovalPolicyError("approval does not bind to the current execution context")
        normalized_arguments = _normalized_arguments(call.arguments)
        if (
            approval.normalized_arguments != normalized_arguments
            or approval.arguments_hash != argument_hash(call.arguments)
        ):
            raise ApprovalPolicyError("approval intent has changed")
