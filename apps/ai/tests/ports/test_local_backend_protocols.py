"""Structural checks for the pre-implementation Backend 2 integration seam."""

import inspect

from app.ports.local_backend import (
    ActivityEventStore,
    ApprovalStore,
    ArtifactStore,
    DraftResolver,
    DraftStore,
    IdentityStore,
    KnowledgeSourceStore,
    SessionFileStore,
    WorkflowStore,
)
from app.tools.contracts import SandboxExecutionRequest


def test_identity_store_supports_immutable_lookup_for_session_restoration() -> None:
    """The auth dependency can reload the current identity rather than trust JWT claims."""

    signature = inspect.signature(IdentityStore.get_by_id)

    assert tuple(signature.parameters) == ("self", "user_id")
    assert inspect.iscoroutinefunction(IdentityStore.get_by_id)


def test_workflow_store_exposes_atomic_stage_compare_and_set() -> None:
    """Backend 2 receives an explicit atomic operation, not transition authority."""

    signature = inspect.signature(WorkflowStore.compare_and_set_stage)

    assert tuple(signature.parameters) == (
        "self",
        "session_id",
        "workflow_run_id",
        "owner_user_id",
        "expected_stage",
        "expected_stage_version",
        "next_stage",
        "next_status",
        "sandbox_attempts",
    )
    assert inspect.iscoroutinefunction(WorkflowStore.compare_and_set_stage)


def test_workflow_store_exposes_owner_scoped_run_restoration() -> None:
    signature = inspect.signature(WorkflowStore.get_run)

    assert tuple(signature.parameters) == (
        "self",
        "workflow_run_id",
        "session_id",
        "owner_user_id",
    )
    assert inspect.iscoroutinefunction(WorkflowStore.get_run)


def test_workflow_store_exposes_owner_scoped_current_run_recovery() -> None:
    signature = inspect.signature(WorkflowStore.get_current_run)

    assert tuple(signature.parameters) == (
        "self",
        "session_id",
        "owner_user_id",
    )
    assert inspect.iscoroutinefunction(WorkflowStore.get_current_run)


def test_approval_store_exposes_atomic_pending_resolution() -> None:
    """Resolution is compare-and-set so duplicate requests cannot rerun a tool."""

    signature = inspect.signature(ApprovalStore.resolve_pending_approval)

    assert "expected_stage_version" in signature.parameters
    assert "decision" in signature.parameters
    assert inspect.iscoroutinefunction(ApprovalStore.resolve_pending_approval)


def test_approval_store_exposes_an_atomic_execution_claim() -> None:
    """Only the caller that changes an approval to queued may dispatch its tool."""

    signature = inspect.signature(ApprovalStore.claim_execution)

    assert "workflow_type" in signature.parameters
    assert "arguments_hash" in signature.parameters
    assert inspect.iscoroutinefunction(ApprovalStore.claim_execution)


def test_artifact_store_requires_the_winning_execution_claim() -> None:
    signature = inspect.signature(ArtifactStore.create)

    assert tuple(signature.parameters) == (
        "self",
        "artifact",
        "execution_claim_token",
    )
    assert inspect.iscoroutinefunction(ArtifactStore.create)


def test_sandbox_execution_contract_remains_unchanged() -> None:
    assert "execution_claim_token" not in SandboxExecutionRequest.model_fields


def test_activity_event_store_is_owner_scoped_and_cursor_driven() -> None:
    append = inspect.signature(ActivityEventStore.append)
    replay = inspect.signature(ActivityEventStore.replay)
    subscribe = inspect.signature(ActivityEventStore.subscribe)

    assert tuple(append.parameters) == ("self", "event", "owner_user_id")
    assert tuple(replay.parameters) == (
        "self",
        "session_id",
        "owner_user_id",
        "after_event_id",
    )
    assert tuple(subscribe.parameters) == (
        "self",
        "session_id",
        "owner_user_id",
        "after_event_id",
    )
    assert inspect.iscoroutinefunction(ActivityEventStore.append)
    assert inspect.iscoroutinefunction(ActivityEventStore.replay)
    assert not inspect.iscoroutinefunction(ActivityEventStore.subscribe)


def test_session_file_store_exposes_owner_scoped_metadata_and_path_recovery() -> None:
    save = inspect.signature(SessionFileStore.save_upload)
    get_upload = inspect.signature(SessionFileStore.get_upload)
    resolve = inspect.signature(SessionFileStore.resolve_approved_path)
    cleanup = inspect.signature(SessionFileStore.cleanup_session_uploads)

    assert tuple(save.parameters) == (
        "self",
        "session",
        "upload_id",
        "source_id",
        "file_name",
        "mime_type",
        "content",
    )
    assert tuple(get_upload.parameters) == (
        "self",
        "upload_id",
        "session_id",
        "owner_user_id",
    )
    assert tuple(resolve.parameters) == tuple(get_upload.parameters)
    assert tuple(cleanup.parameters) == (
        "self",
        "session_id",
        "owner_user_id",
    )
    assert all(
        inspect.iscoroutinefunction(method)
        for method in (
            SessionFileStore.save_upload,
            SessionFileStore.get_upload,
            SessionFileStore.resolve_approved_path,
            SessionFileStore.cleanup_session_uploads,
        )
    )


def test_knowledge_and_draft_ports_remain_narrow() -> None:
    assert tuple(inspect.signature(KnowledgeSourceStore.save_source).parameters) == (
        "self",
        "knowledge_source_id",
        "document_id",
        "source_id",
        "approved_by_user_id",
        "file_name",
        "mime_type",
        "content",
    )
    assert tuple(inspect.signature(DraftStore.save).parameters) == (
        "self",
        "workflow_run",
        "draft",
        "created_at",
    )
    assert tuple(inspect.signature(DraftResolver.resolve_for_export).parameters) == (
        "self",
        "request",
    )
