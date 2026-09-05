"""Tests for Backend 1's endpoint-neutral workflow contracts."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.auth.contracts import AuthenticatedUser, UserRole
from app.workflow.contracts import (
    Approval,
    ApprovalDecision,
    ApprovalStatus,
    ExecutionStatus,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowSession,
    WorkflowStage,
    WorkflowStatus,
    WorkflowType,
)


def test_contracts_use_camel_case_json_and_uuid_strings() -> None:
    """Keep public IDs and field aliases stable for the future desktop client."""

    user = AuthenticatedUser(
        user_id=uuid4(),
        auth_session_id=uuid4(),
        username="inspector",
        display_name="Inspector",
        role=UserRole.EMPLOYEE,
    )

    payload = user.model_dump(by_alias=True, mode="json")

    assert isinstance(payload["userId"], str)
    assert isinstance(payload["authSessionId"], str)
    assert "user_id" not in payload


def test_approval_requires_a_consistent_resolution() -> None:
    """An approval cannot claim to be resolved without its immutable resolution data."""

    with pytest.raises(ValidationError):
        Approval(
            approval_id=uuid4(),
            session_id=uuid4(),
            workflow_run_id=uuid4(),
            owner_user_id=uuid4(),
            workflow_type=WorkflowType.CODE_REPAIR,
            stage=WorkflowStage.AWAITING_APPROVAL,
            stage_version=2,
            tool_name="run_sandbox",
            normalized_arguments={},
            arguments_hash="0" * 64,
            status=ApprovalStatus.APPROVED,
            requested_at=datetime.now(UTC),
        )


def test_rejected_approval_cannot_claim_execution() -> None:
    """A rejected request is terminal for that exact side-effect intent."""

    with pytest.raises(ValidationError):
        Approval(
            approval_id=uuid4(),
            session_id=uuid4(),
            workflow_run_id=uuid4(),
            owner_user_id=uuid4(),
            workflow_type=WorkflowType.CODE_REPAIR,
            stage=WorkflowStage.AWAITING_APPROVAL,
            stage_version=2,
            tool_name="run_sandbox",
            normalized_arguments={},
            arguments_hash="0" * 64,
            status=ApprovalStatus.REJECTED,
            requested_at=datetime.now(UTC),
            resolved_at=datetime.now(UTC),
            resolved_by_user_id=uuid4(),
            decision=ApprovalDecision.REJECTED,
            execution_status=ExecutionStatus.QUEUED,
        )


def test_pending_approval_cannot_claim_execution() -> None:
    """Only a resolved approval may be queued for an executor."""

    with pytest.raises(ValidationError):
        Approval(
            approval_id=uuid4(),
            session_id=uuid4(),
            workflow_run_id=uuid4(),
            owner_user_id=uuid4(),
            workflow_type=WorkflowType.CODE_REPAIR,
            stage=WorkflowStage.AWAITING_APPROVAL,
            stage_version=2,
            tool_name="run_sandbox",
            normalized_arguments={},
            arguments_hash="0" * 64,
            requested_at=datetime.now(UTC),
            execution_status=ExecutionStatus.QUEUED,
        )


def test_timestamps_must_be_utc() -> None:
    """Persisted event and workflow timestamps are never naive or offset-local."""

    with pytest.raises(ValidationError):
        Approval(
            approval_id=uuid4(),
            session_id=uuid4(),
            workflow_run_id=uuid4(),
            owner_user_id=uuid4(),
            workflow_type=WorkflowType.CODE_REPAIR,
            stage=WorkflowStage.AWAITING_APPROVAL,
            stage_version=2,
            tool_name="run_sandbox",
            normalized_arguments={},
            arguments_hash="0" * 64,
            requested_at=datetime.now(timezone(timedelta(hours=5, minutes=30))),
        )


def test_workflow_type_is_explicit() -> None:
    """The only Phase 0 workflow types remain the MVP inspection and repair flows."""

    assert {workflow_type.value for workflow_type in WorkflowType} == {
        "inspectionAnalysis",
        "codeRepair",
    }


@pytest.mark.parametrize(
    "model",
    [
        lambda: WorkflowSession(
            session_id=uuid4(),
            owner_user_id=uuid4(),
            workflow_type=WorkflowType.INSPECTION_ANALYSIS,
            title="Inspection review",
            stage=WorkflowStage.PLANNING,
            status=WorkflowStatus.ACTIVE,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ),
        lambda: WorkflowRun(
            workflow_run_id=uuid4(),
            session_id=uuid4(),
            owner_user_id=uuid4(),
            workflow_type=WorkflowType.INSPECTION_ANALYSIS,
            stage=WorkflowStage.AWAITING_APPROVAL,
            stage_version=3,
            status=WorkflowRunStatus.COMPLETED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ),
        lambda: WorkflowRun(
            workflow_run_id=uuid4(),
            session_id=uuid4(),
            owner_user_id=uuid4(),
            workflow_type=WorkflowType.CODE_REPAIR,
            stage=WorkflowStage.SANDBOX_EXECUTING,
            stage_version=3,
            status=WorkflowRunStatus.ACTIVE,
            sandbox_attempts=0,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ),
    ],
)
def test_workflow_contracts_reject_inconsistent_states(
    model: Callable[[], WorkflowSession | WorkflowRun],
) -> None:
    """Invalid workflow type, stage, status, and attempt combinations never persist."""

    with pytest.raises(ValidationError):
        model()
