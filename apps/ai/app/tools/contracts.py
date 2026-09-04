"""Typed input, intent, and result contracts for the two approved MVP tools."""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.api.contracts import ApiContractModel
from app.workflow.contracts import Approval, ExecutionStatus


class ToolName(StrEnum):
    """Only names in this enum may be proposed or executed."""

    REQUEST_DOCUMENT_EXPORT = "request_document_export"
    RUN_SANDBOX = "run_sandbox"


class ArtifactFormat(StrEnum):
    """Explicitly approved draft output formats."""

    DOCX = "docx"
    PDF = "pdf"


class SandboxLanguage(StrEnum):
    """Languages permitted by the narrowly scoped MVP sandbox request."""

    PYTHON = "python"


class DocumentExportArguments(ApiContractModel):
    """A request to render one validated draft into explicit formats."""

    draft_id: UUID
    formats: tuple[ArtifactFormat, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_formats(self) -> "DocumentExportArguments":
        if len(self.formats) != len(set(self.formats)):
            raise ValueError("export formats must be unique")
        return self


class SandboxArguments(ApiContractModel):
    """File/workspace references for isolated execution; raw commands are absent."""

    workspace_id: UUID
    source_file_id: UUID
    language: SandboxLanguage
    test_file_id: UUID | None = None


ToolArguments = Annotated[
    DocumentExportArguments | SandboxArguments,
    Field(union_mode="left_to_right"),
]


class ValidatedToolCall(ApiContractModel):
    """Normalized call returned by the registry after typed validation."""

    tool_name: ToolName
    arguments: ToolArguments

    @model_validator(mode="after")
    def require_matching_arguments(self) -> "ValidatedToolCall":
        expected_type = (
            DocumentExportArguments
            if self.tool_name is ToolName.REQUEST_DOCUMENT_EXPORT
            else SandboxArguments
        )
        if not isinstance(self.arguments, expected_type):
            raise ValueError("tool name does not match its typed arguments")
        return self


class DocumentExportExecutionRequest(ApiContractModel):
    """Bound request passed to Backend 2 only after approval validation."""

    approval_id: UUID
    session_id: UUID
    workflow_run_id: UUID
    arguments: DocumentExportArguments


class SandboxExecutionRequest(ApiContractModel):
    """Bound sandbox request with no command-string escape hatch."""

    approval_id: UUID
    session_id: UUID
    workflow_run_id: UUID
    arguments: SandboxArguments


class ArtifactReference(ApiContractModel):
    """Metadata for an approved local draft artifact, never a filesystem path."""

    artifact_id: UUID
    format: ArtifactFormat
    file_name: str = Field(min_length=1, max_length=255)


class DocumentExportResult(ApiContractModel):
    """Typed result from Backend 2's document executor."""

    tool_name: Literal[ToolName.REQUEST_DOCUMENT_EXPORT] = ToolName.REQUEST_DOCUMENT_EXPORT
    status: ExecutionStatus
    artifacts: tuple[ArtifactReference, ...] = ()
    failure_code: str | None = Field(default=None, max_length=100)


class SandboxExecutionResult(ApiContractModel):
    """Sanitized summary returned from Backend 2's isolated sandbox executor."""

    tool_name: Literal[ToolName.RUN_SANDBOX] = ToolName.RUN_SANDBOX
    status: ExecutionStatus
    exit_code: int | None = None
    passed: bool | None = None
    failure_code: str | None = Field(default=None, max_length=100)


ToolExecutionResult = DocumentExportResult | SandboxExecutionResult


class ToolExecutionDispatch(ApiContractModel):
    """Whether this caller claimed execution and any durable prior result."""

    approval: Approval
    dispatched_now: bool
    result: ToolExecutionResult | None = None

    @model_validator(mode="after")
    def require_result_for_new_dispatch(self) -> "ToolExecutionDispatch":
        if self.dispatched_now and self.result is None:
            raise ValueError("a new dispatch must include its executor result")
        return self
