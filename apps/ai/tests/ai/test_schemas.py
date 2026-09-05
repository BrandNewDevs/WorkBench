"""Validation tests for the Phase 0 AI data contracts."""

from math import inf, nan
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.ai.evaluation.samples import (
    representative_contracts,
    sample_inference_metrics,
    sample_model_profile,
    sample_task,
)
from app.ai.schemas import (
    AgentProposal,
    ApprovedPath,
    ApprovedVisualInput,
    Capability,
    CapabilityDecision,
    EmbeddingResult,
    KnowledgeQuery,
    ModelHealth,
    ModelStatus,
    SourceDocument,
    VisionGenerationRequest,
    VisualAnalysisRequest,
    VisualBytesInput,
    VisualMimeType,
)


def test_every_contract_accepts_a_representative_example() -> None:
    """Keep every public schema constructible without a running service."""

    contracts = representative_contracts()

    assert contracts
    assert all(contract.model_dump() for contract in contracts)


def test_contracts_serialize_with_camel_case_json_fields() -> None:
    """Honor the explicit JSON naming convention used by Backend 1."""

    payload = sample_model_profile().model_dump(by_alias=True, mode="json")

    assert payload["profileId"] == "safe-8gb"
    assert payload["textCandidates"] == ["qwen3:4b", "qwen3:1.7b"]
    assert "profile_id" not in payload


@pytest.mark.parametrize(
    ("response_text", "tool_call"),
    [(None, None), ("Done", {"toolName": "save", "arguments": {}, "explanation": "Save"})],
)
def test_agent_proposal_requires_exactly_one_outcome(
    response_text: str | None, tool_call: object | None
) -> None:
    """Reject missing outcomes and response/tool combinations."""

    with pytest.raises(ValidationError):
        AgentProposal.model_validate({"responseText": response_text, "toolCall": tool_call})


def test_ready_model_health_requires_a_loadable_selected_model() -> None:
    """Do not describe a missing model as ready."""

    with pytest.raises(ValidationError):
        ModelHealth(
            capability=Capability.TEXT,
            status=ModelStatus.READY,
            installed=False,
            loadable=False,
        )


def test_fallback_decision_requires_a_reason() -> None:
    """Fallback selection must remain visible for status and audit events."""

    with pytest.raises(ValidationError):
        CapabilityDecision(
            capability=Capability.TEXT,
            selected_model="qwen3:1.7b",
            reason="Preferred model was not selected.",
            used_fallback=True,
        )


@pytest.mark.parametrize("non_finite", (nan, inf, -inf))
def test_public_embedding_contract_rejects_non_finite_values(non_finite: float) -> None:
    """Prevent invalid vectors from reaching retrieval or persistent storage."""

    with pytest.raises(ValidationError):
        EmbeddingResult(
            model="qwen3-embedding:0.6b",
            vectors=((non_finite,),),
            metrics=sample_inference_metrics(),
        )


def test_knowledge_query_rejects_blank_text() -> None:
    """Stop empty semantic searches before they consume local model time."""

    with pytest.raises(ValidationError):
        KnowledgeQuery(text="   ")


@pytest.mark.parametrize("image", ("", "not-base64", "===="))
def test_vision_request_rejects_invalid_base64_images(image: str) -> None:
    """Reject malformed normalized image input before it reaches Ollama."""

    profile = sample_model_profile()
    with pytest.raises(ValidationError):
        VisionGenerationRequest(
            model="qwen3-vl:4b",
            system_prompt="Return JSON.",
            user_prompt="Inspect the image.",
            images_base64=(image,),
            output_schema={"type": "object"},
            limits=profile.vision_limits,
        )


def test_visual_request_accepts_only_explicit_approved_or_byte_inputs() -> None:
    """Make filesystem authority visible in the public AI contract."""

    approved = ApprovedVisualInput(
        approved_path=ApprovedPath(
            path=Path("/approved/session/report.pdf"),
            source_id="report-1",
            session_id="session-1",
        ),
        mime_type=VisualMimeType.PDF,
        document_name="report.pdf",
    )
    uploaded = VisualBytesInput(
        content=b"sanitized-image-bytes",
        source_id="photo-1",
        session_id="session-1",
        mime_type=VisualMimeType.PNG,
        document_name="photo.png",
    )

    request = VisualAnalysisRequest(inputs=(approved, uploaded), task=sample_task())

    assert request.inputs == (approved, uploaded)
    assert "sanitized-image-bytes" not in repr(request)


def test_visual_request_rejects_an_unapproved_raw_path() -> None:
    """Do not let callers bypass Backend 2's ApprovedPath wrapper."""

    with pytest.raises(ValidationError):
        VisualAnalysisRequest.model_validate(
            {
                "inputs": [
                    {
                        "inputKind": "approvedPath",
                        "approvedPath": "/arbitrary/report.pdf",
                        "mimeType": "application/pdf",
                        "documentName": "report.pdf",
                    }
                ],
                "task": sample_task().model_dump(by_alias=True, mode="json"),
            }
        )


def test_source_document_bytes_require_application_owned_identity() -> None:
    """Keep confidential bytes hidden while requiring a traceable source ID."""

    document = SourceDocument(
        document_id="sop-1",
        document_name="sop.txt",
        mime_type="text/plain",
        source_id="sop-source-1",
        content=b"Inspection procedure",
    )

    assert document.effective_source_id == "sop-source-1"
    assert "Inspection procedure" not in repr(document)

    with pytest.raises(ValidationError):
        SourceDocument(
            document_id="sop-1",
            document_name="sop.txt",
            mime_type="text/plain",
            content=b"Missing source ID",
        )

    with pytest.raises(ValidationError):
        SourceDocument(
            document_id="sop-1",
            document_name="sop.txt",
            mime_type="text/plain",
            source_id="sop-source-1",
            approved_path=ApprovedPath(
                path=Path("/approved/sop.txt"),
                source_id="sop-source-1",
                session_id="session-1",
            ),
            content=b"Ambiguous second input",
        )
