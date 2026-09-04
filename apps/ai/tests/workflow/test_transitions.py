"""Tests for deterministic workflow-stage policy."""

import pytest

from app.workflow.contracts import WorkflowStage, WorkflowType
from app.workflow.transitions import WorkflowTransitionError, assert_transition, can_transition


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (WorkflowStage.COLLECTING_INPUTS, WorkflowStage.EXTRACTING),
        (WorkflowStage.EXTRACTING, WorkflowStage.RETRIEVING),
        (WorkflowStage.RETRIEVING, WorkflowStage.DRAFTING),
        (WorkflowStage.DRAFTING, WorkflowStage.VALIDATING),
        (WorkflowStage.VALIDATING, WorkflowStage.AWAITING_APPROVAL),
        (WorkflowStage.AWAITING_APPROVAL, WorkflowStage.EXPORTING),
        (WorkflowStage.AWAITING_APPROVAL, WorkflowStage.APPROVAL_REJECTED),
        (WorkflowStage.EXPORTING, WorkflowStage.COMPLETED),
    ],
)
def test_all_inspection_transitions_are_permitted(
    current: WorkflowStage, target: WorkflowStage
) -> None:
    """Inspection analysis follows the fixed extract-to-export sequence."""

    assert can_transition(WorkflowType.INSPECTION_ANALYSIS, current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (WorkflowStage.COLLECTING_INPUTS, WorkflowStage.PLANNING),
        (WorkflowStage.PLANNING, WorkflowStage.AWAITING_APPROVAL),
        (WorkflowStage.AWAITING_APPROVAL, WorkflowStage.SANDBOX_EXECUTING),
        (WorkflowStage.AWAITING_APPROVAL, WorkflowStage.APPROVAL_REJECTED),
        (WorkflowStage.SANDBOX_EXECUTING, WorkflowStage.REPAIRING),
        (WorkflowStage.REPAIRING, WorkflowStage.AWAITING_APPROVAL),
        (WorkflowStage.SANDBOX_EXECUTING, WorkflowStage.COMPLETED),
    ],
)
def test_all_code_repair_transitions_are_permitted(
    current: WorkflowStage, target: WorkflowStage
) -> None:
    """The repair path uses a second approval before the successful rerun."""

    attempts = 2 if target is WorkflowStage.COMPLETED else 1
    assert can_transition(
        WorkflowType.CODE_REPAIR,
        current,
        target,
        sandbox_attempts=attempts,
        sandbox_passed=True if target is WorkflowStage.COMPLETED else None,
    )


@pytest.mark.parametrize(
    ("workflow_type", "current", "target", "sandbox_attempts"),
    [
        (
            WorkflowType.INSPECTION_ANALYSIS,
            WorkflowStage.COLLECTING_INPUTS,
            WorkflowStage.DRAFTING,
            0,
        ),
        (WorkflowType.CODE_REPAIR, WorkflowStage.PLANNING, WorkflowStage.EXTRACTING, 0),
        (WorkflowType.CODE_REPAIR, WorkflowStage.SANDBOX_EXECUTING, WorkflowStage.COMPLETED, 1),
        (WorkflowType.CODE_REPAIR, WorkflowStage.SANDBOX_EXECUTING, WorkflowStage.COMPLETED, 2),
        (WorkflowType.CODE_REPAIR, WorkflowStage.SANDBOX_EXECUTING, WorkflowStage.REPAIRING, 2),
        (WorkflowType.CODE_REPAIR, WorkflowStage.COMPLETED, WorkflowStage.FAILED, 2),
        (
            WorkflowType.INSPECTION_ANALYSIS,
            WorkflowStage.APPROVAL_REJECTED,
            WorkflowStage.EXTRACTING,
            0,
        ),
    ],
)
def test_illegal_transitions_are_rejected(
    workflow_type: WorkflowType,
    current: WorkflowStage,
    target: WorkflowStage,
    sandbox_attempts: int,
) -> None:
    """No planner result can skip stages, revive terminal work, or bypass the rerun."""

    assert not can_transition(
        workflow_type, current, target, sandbox_attempts=sandbox_attempts
    )
    with pytest.raises(WorkflowTransitionError):
        assert_transition(workflow_type, current, target, sandbox_attempts=sandbox_attempts)


@pytest.mark.parametrize(
    ("workflow_type", "stage"),
    [
        (WorkflowType.INSPECTION_ANALYSIS, WorkflowStage.COLLECTING_INPUTS),
        (WorkflowType.INSPECTION_ANALYSIS, WorkflowStage.EXTRACTING),
        (WorkflowType.INSPECTION_ANALYSIS, WorkflowStage.RETRIEVING),
        (WorkflowType.INSPECTION_ANALYSIS, WorkflowStage.DRAFTING),
        (WorkflowType.INSPECTION_ANALYSIS, WorkflowStage.VALIDATING),
        (WorkflowType.INSPECTION_ANALYSIS, WorkflowStage.AWAITING_APPROVAL),
        (WorkflowType.INSPECTION_ANALYSIS, WorkflowStage.EXPORTING),
        (WorkflowType.CODE_REPAIR, WorkflowStage.COLLECTING_INPUTS),
        (WorkflowType.CODE_REPAIR, WorkflowStage.PLANNING),
        (WorkflowType.CODE_REPAIR, WorkflowStage.AWAITING_APPROVAL),
        (WorkflowType.CODE_REPAIR, WorkflowStage.SANDBOX_EXECUTING),
        (WorkflowType.CODE_REPAIR, WorkflowStage.REPAIRING),
    ],
)
def test_any_active_stage_may_fail(workflow_type: WorkflowType, stage: WorkflowStage) -> None:
    """Expected dependency failures can be recorded from every in-progress stage."""

    assert can_transition(workflow_type, stage, WorkflowStage.FAILED)


def test_failed_second_sandbox_attempt_cannot_complete() -> None:
    """Completion requires the approved repair rerun to pass."""

    assert not can_transition(
        WorkflowType.CODE_REPAIR,
        WorkflowStage.SANDBOX_EXECUTING,
        WorkflowStage.COMPLETED,
        sandbox_attempts=2,
        sandbox_passed=False,
    )
