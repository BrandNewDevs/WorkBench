"""Tests for status-dependent Backend 2 tool results."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.tools.contracts import (
    ArtifactFormat,
    ArtifactReference,
    DocumentExportResult,
    SandboxExecutionResult,
)
from app.workflow.contracts import ExecutionStatus


def test_completed_document_export_requires_an_artifact() -> None:
    """A successful export always identifies the created local artifact."""

    with pytest.raises(ValidationError):
        DocumentExportResult(status=ExecutionStatus.COMPLETED)


def test_completed_sandbox_execution_requires_a_passing_exit_status() -> None:
    """A completed sandbox result cannot carry a failed test outcome."""

    with pytest.raises(ValidationError):
        SandboxExecutionResult(status=ExecutionStatus.COMPLETED, exit_code=1, passed=False)


def test_failed_sandbox_execution_requires_a_sanitized_failure_code() -> None:
    """A failed sandbox execution remains auditable without raw output."""

    with pytest.raises(ValidationError):
        SandboxExecutionResult(status=ExecutionStatus.FAILED, passed=False)


def test_completed_document_export_accepts_an_artifact() -> None:
    """The result contract accepts the artifact metadata returned by Backend 2."""

    result = DocumentExportResult(
        status=ExecutionStatus.COMPLETED,
        artifacts=(
            ArtifactReference(
                artifact_id=uuid4(),
                format=ArtifactFormat.DOCX,
                file_name="approval-note.docx",
            ),
        ),
    )

    assert result.artifacts[0].format is ArtifactFormat.DOCX
