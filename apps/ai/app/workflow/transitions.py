"""Pure deterministic workflow transition policy."""

from app.workflow.contracts import WorkflowStage, WorkflowType


class WorkflowTransitionError(ValueError):
    """Raised when a controller requests a stage change outside the policy."""


_INSPECTION_TRANSITIONS: dict[WorkflowStage, frozenset[WorkflowStage]] = {
    WorkflowStage.COLLECTING_INPUTS: frozenset({WorkflowStage.EXTRACTING}),
    WorkflowStage.EXTRACTING: frozenset({WorkflowStage.RETRIEVING}),
    WorkflowStage.RETRIEVING: frozenset({WorkflowStage.DRAFTING}),
    WorkflowStage.DRAFTING: frozenset({WorkflowStage.VALIDATING}),
    WorkflowStage.VALIDATING: frozenset({WorkflowStage.AWAITING_APPROVAL}),
    WorkflowStage.AWAITING_APPROVAL: frozenset(
        {WorkflowStage.EXPORTING, WorkflowStage.APPROVAL_REJECTED}
    ),
    WorkflowStage.EXPORTING: frozenset({WorkflowStage.COMPLETED}),
}

_CODE_REPAIR_TRANSITIONS: dict[WorkflowStage, frozenset[WorkflowStage]] = {
    WorkflowStage.COLLECTING_INPUTS: frozenset({WorkflowStage.PLANNING}),
    WorkflowStage.PLANNING: frozenset({WorkflowStage.AWAITING_APPROVAL}),
    WorkflowStage.AWAITING_APPROVAL: frozenset(
        {WorkflowStage.SANDBOX_EXECUTING, WorkflowStage.APPROVAL_REJECTED}
    ),
    WorkflowStage.SANDBOX_EXECUTING: frozenset(
        {WorkflowStage.REPAIRING, WorkflowStage.COMPLETED}
    ),
    WorkflowStage.REPAIRING: frozenset({WorkflowStage.AWAITING_APPROVAL}),
}


def can_transition(
    workflow_type: WorkflowType,
    current: WorkflowStage,
    target: WorkflowStage,
    *,
    sandbox_attempts: int = 0,
    sandbox_passed: bool | None = None,
) -> bool:
    """Return whether the exact workflow state permits the requested transition."""

    if sandbox_attempts < 0:
        return False
    if current in {WorkflowStage.COMPLETED, WorkflowStage.FAILED, WorkflowStage.APPROVAL_REJECTED}:
        return False
    if target is WorkflowStage.FAILED:
        return True
    transitions = (
        _INSPECTION_TRANSITIONS
        if workflow_type is WorkflowType.INSPECTION_ANALYSIS
        else _CODE_REPAIR_TRANSITIONS
    )
    if target not in transitions.get(current, frozenset()):
        return False
    if workflow_type is WorkflowType.CODE_REPAIR and current is WorkflowStage.SANDBOX_EXECUTING:
        if target is WorkflowStage.REPAIRING:
            return sandbox_attempts == 1
        if target is WorkflowStage.COMPLETED:
            return sandbox_attempts >= 2 and sandbox_passed is True
    return True


def assert_transition(
    workflow_type: WorkflowType,
    current: WorkflowStage,
    target: WorkflowStage,
    *,
    sandbox_attempts: int = 0,
    sandbox_passed: bool | None = None,
) -> None:
    """Reject an invalid controller transition before the persistence port is called."""

    if not can_transition(
        workflow_type,
        current,
        target,
        sandbox_attempts=sandbox_attempts,
        sandbox_passed=sandbox_passed,
    ):
        raise WorkflowTransitionError(
            f"{workflow_type.value} cannot transition from {current.value} to {target.value}"
        )
