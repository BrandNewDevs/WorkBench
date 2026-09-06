"""Employee chat request and response-envelope contracts for the local API."""

from pydantic import Field

from app.api.contracts import ApiContractModel
from app.ports.backend2 import WorkflowMessage
from app.workflow.contracts import WorkflowSession, WorkflowType


class ChatSessionCreateRequest(ApiContractModel):
    """Create one owned chat thread backed by a workflow session."""

    workflow_type: WorkflowType
    title: str = Field(min_length=1, max_length=200)


class ChatSessionListEnvelope(ApiContractModel):
    """The employee's own chat threads, most recently updated first."""

    sessions: list[WorkflowSession]


class ChatMessageAppendRequest(ApiContractModel):
    """One employee-authored chat message; the service strips outer whitespace."""

    content: str = Field(min_length=1, max_length=20_000)


class ChatMessageListEnvelope(ApiContractModel):
    """The latest messages of one chat thread in chronological order."""

    messages: list[WorkflowMessage]
