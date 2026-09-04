"""Structural checks for the pre-implementation Backend 2 integration seam."""

import inspect

from app.ports.backend2 import ApprovalStore, WorkflowStore


def test_workflow_store_exposes_atomic_stage_compare_and_set() -> None:
    """Backend 2 receives an explicit atomic operation, not transition authority."""

    signature = inspect.signature(WorkflowStore.compare_and_set_stage)

    assert tuple(signature.parameters) == (
        "self",
        "session_id",
        "workflow_run_id",
        "expected_stage",
        "expected_stage_version",
        "next_stage",
        "next_status",
        "sandbox_attempts",
    )
    assert inspect.iscoroutinefunction(WorkflowStore.compare_and_set_stage)


def test_approval_store_exposes_atomic_pending_resolution() -> None:
    """Resolution is compare-and-set so duplicate requests cannot rerun a tool."""

    signature = inspect.signature(ApprovalStore.resolve_pending_approval)

    assert "expected_stage_version" in signature.parameters
    assert "decision" in signature.parameters
    assert inspect.iscoroutinefunction(ApprovalStore.resolve_pending_approval)
