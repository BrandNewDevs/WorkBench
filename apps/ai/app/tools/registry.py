"""Deterministic registry and approval checks for the only MVP side effects."""

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast
from uuid import UUID, uuid4

from pydantic import JsonValue, ValidationError

from app.ai.schemas import ProposedToolCall, ToolDefinition
from app.api.contracts import ApiContractModel
from app.ports.backend2 import ApprovalStore, ArtifactExecutor, SandboxExecutor
from app.tools.contracts import (
    DocumentExportArguments,
    DocumentExportExecutionRequest,
    SandboxArguments,
    SandboxExecutionRequest,
    ToolArguments,
    ToolExecutionDispatch,
    ToolExecutionResult,
    ToolName,
    ValidatedToolCall,
)
from app.workflow.contracts import (
    Approval,
    ApprovalStatus,
    ExecutionStatus,
    WorkflowRun,
    WorkflowRunStatus,
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
        self,
        approval_store: ApprovalStore,
        artifact_executor: ArtifactExecutor,
        sandbox_executor: SandboxExecutor,
    ) -> None:
        self._approval_store = approval_store
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
        workflow_run: WorkflowRun,
        requested_at: datetime | None = None,
        approval_id: UUID | None = None,
    ) -> Approval:
        """Bind normalized intent to the awaiting-approval run state before persistence."""

        registration = _REGISTERED_TOOLS[call.tool_name]
        if registration.workflow_type is not workflow_run.workflow_type:
            raise ApprovalPolicyError("tool is not eligible for this workflow type")
        if (
            workflow_run.stage not in registration.execution_stages
            or workflow_run.status is not WorkflowRunStatus.WAITING_FOR_APPROVAL
        ):
            raise ApprovalPolicyError("approval is not being created at the tool execution stage")
        timestamp = requested_at if requested_at is not None else datetime.now(UTC)
        return Approval(
            approval_id=approval_id or uuid4(),
            session_id=workflow_run.session_id,
            workflow_run_id=workflow_run.workflow_run_id,
            owner_user_id=workflow_run.owner_user_id,
            workflow_type=workflow_run.workflow_type,
            stage=workflow_run.stage,
            stage_version=workflow_run.stage_version,
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
        workflow_run: WorkflowRun,
    ) -> ToolExecutionDispatch:
        """Dispatch only after the persisted approval exactly matches the current intent."""

        self._assert_execution_approved(
            call,
            approval=approval,
            workflow_run=workflow_run,
        )
        assert approval is not None  # Narrowed after the policy guard above.
        claim = await self._approval_store.claim_execution(
            approval_id=approval.approval_id,
            session_id=workflow_run.session_id,
            workflow_run_id=workflow_run.workflow_run_id,
            owner_user_id=workflow_run.owner_user_id,
            workflow_type=workflow_run.workflow_type,
            expected_stage=workflow_run.stage,
            expected_stage_version=workflow_run.stage_version,
            tool_name=call.tool_name.value,
            arguments_hash=argument_hash(call.arguments),
        )
        if claim is None:
            raise ApprovalPolicyError("approval could not be claimed for execution")
        if not claim.claimed_now:
            return ToolExecutionDispatch(
                approval=claim.approval,
                dispatched_now=False,
                result=await self._approval_store.get_execution_result(
                    approval_id=approval.approval_id,
                    session_id=workflow_run.session_id,
                    workflow_run_id=workflow_run.workflow_run_id,
                    owner_user_id=workflow_run.owner_user_id,
                ),
            )
        if call.tool_name is ToolName.REQUEST_DOCUMENT_EXPORT:
            assert isinstance(call.arguments, DocumentExportArguments)
            result: ToolExecutionResult = await self._artifact_executor.create_artifacts(
                DocumentExportExecutionRequest(
                    approval_id=approval.approval_id,
                    session_id=workflow_run.session_id,
                    workflow_run_id=workflow_run.workflow_run_id,
                    arguments=call.arguments,
                )
            )
        else:
            assert isinstance(call.arguments, SandboxArguments)
            result = await self._sandbox_executor.run(
                SandboxExecutionRequest(
                    approval_id=approval.approval_id,
                    session_id=workflow_run.session_id,
                    workflow_run_id=workflow_run.workflow_run_id,
                    arguments=call.arguments,
                )
            )
        persisted_approval = await self._approval_store.record_execution_result(
            approval_id=approval.approval_id,
            result=result,
        )
        if persisted_approval is None:
            raise ApprovalPolicyError("executor result could not be persisted")
        return ToolExecutionDispatch(
            approval=persisted_approval,
            dispatched_now=True,
            result=result,
        )

    def _assert_execution_approved(
        self,
        call: ValidatedToolCall,
        *,
        approval: Approval | None,
        workflow_run: WorkflowRun,
    ) -> None:
        if approval is None:
            raise ApprovalPolicyError("missing approval")
        registration = _REGISTERED_TOOLS[call.tool_name]
        if registration.workflow_type is not workflow_run.workflow_type:
            raise ApprovalPolicyError("tool is not eligible for this workflow type")
        if (
            workflow_run.stage not in registration.execution_stages
            or workflow_run.status is not WorkflowRunStatus.WAITING_FOR_APPROVAL
        ):
            raise ApprovalPolicyError("tool is not eligible for execution at the current stage")
        if approval.status is not ApprovalStatus.APPROVED:
            raise ApprovalPolicyError("approval is not approved")
        if approval.execution_status is ExecutionStatus.NOT_APPLICABLE:
            raise ApprovalPolicyError("approval cannot authorize execution")
        if (
            approval.session_id != workflow_run.session_id
            or approval.workflow_run_id != workflow_run.workflow_run_id
            or approval.owner_user_id != workflow_run.owner_user_id
            or approval.workflow_type is not workflow_run.workflow_type
            or approval.stage is not workflow_run.stage
            or approval.stage_version != workflow_run.stage_version
            or approval.tool_name != call.tool_name.value
        ):
            raise ApprovalPolicyError("approval does not bind to the current execution context")
        normalized_arguments = _normalized_arguments(call.arguments)
        if (
            approval.normalized_arguments != normalized_arguments
            or approval.arguments_hash != argument_hash(call.arguments)
        ):
            raise ApprovalPolicyError("approval intent has changed")
