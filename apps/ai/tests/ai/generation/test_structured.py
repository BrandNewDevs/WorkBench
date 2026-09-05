"""Behavior tests for structured local text generation workflows."""

import json
from collections.abc import Sequence

import pytest
from pydantic import JsonValue

from app.ai.errors import GroundingViolation
from app.ai.evaluation.samples import (
    sample_evidence_chunk,
    sample_finding,
    sample_inference_metrics,
    sample_model_profile,
    sample_task,
)
from app.ai.fakes import FakeModelAdapter
from app.ai.generation import StructuredTextGenerator
from app.ai.schemas import (
    AgentContext,
    ConversationMessage,
    DraftRequest,
    GroundedDraft,
    TaskPlan,
    TextGenerationRequest,
    TextGenerationResult,
)


class ScriptedTextAdapter(FakeModelAdapter):
    """Replay structured outputs at the local-model seam."""

    def __init__(self, outputs: Sequence[TextGenerationResult | Exception]) -> None:
        super().__init__()
        self.outputs = list(outputs)
        self.requests: list[TextGenerationRequest] = []

    async def generate_text(self, request: TextGenerationRequest) -> TextGenerationResult:
        """Return the next recorded output without contacting Ollama."""

        self.requests.append(request)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output


def recorded_output(value: JsonValue, *, model: str = "qwen3:4b") -> TextGenerationResult:
    """Wrap a JSON-compatible value in the low-level model result contract."""

    return TextGenerationResult(
        model=model,
        text=json.dumps(value),
        structured_output=value,
        metrics=sample_inference_metrics(),
    )


async def test_planning_returns_a_typed_sequence_and_next_step() -> None:
    """Give Backend 1 a bounded plan without executing any step."""

    output: dict[str, JsonValue] = {
        "objective": "Prepare a grounded approval-note draft.",
        "steps": [
            {
                "stepId": "review-findings",
                "instruction": "Review the supplied inspection findings.",
                "expectedOutput": "A concise list of supported observations.",
            },
            {
                "stepId": "draft-note",
                "instruction": "Draft the approval note from supplied evidence.",
                "expectedOutput": "Structured draft content for backend rendering.",
            },
        ],
        "nextStepId": "review-findings",
        "uncertainties": ["The unreadable gauge value must remain unresolved."],
    }
    adapter = ScriptedTextAdapter((recorded_output(output),))
    generator = StructuredTextGenerator(adapter, sample_model_profile())
    context = AgentContext(
        task=sample_task(),
        conversation=(
            ConversationMessage(role="user", content="Prepare an approval note."),
        ),
        allowed_tools=(),
    )

    result = await generator.plan_task(context)

    assert isinstance(result, TaskPlan)
    assert tuple(step.step_id for step in result.steps) == (
        "review-findings",
        "draft-note",
    )
    assert result.next_step_id == "review-findings"
    assert len(adapter.requests) == 1
    request = adapter.requests[0]
    expected_schema = TaskPlan.model_json_schema(by_alias=True)
    assert request.output_schema == expected_schema
    assert json.dumps(expected_schema, sort_keys=True) in request.user_prompt
    assert "UNTRUSTED DATA BEGIN" in request.user_prompt
    assert "task-planning-v1" in request.user_prompt
    assert request.temperature == 0


def grounded_draft_output(*, evidence_source_ids: list[str]) -> dict[str, JsonValue]:
    """Build one recorded approval-note result with explicit claim citations."""

    finding = sample_finding()
    cited_ids: list[JsonValue] = list(evidence_source_ids)
    return {
        "subject": "Corrosion follow-up",
        "summary": "Surface corrosion was observed during inspection.",
        "findings": [finding.model_dump(mode="json", by_alias=True)],
        "recommendation": "Approve thickness measurement before repair work.",
        "criticalClaims": [
            {
                "text": "Surface corrosion was observed near the lower flange.",
                "evidenceSourceIds": ["inspection-report-page-2"],
            },
            {
                "text": "Thickness measurement is required before repair approval.",
                "evidenceSourceIds": cited_ids,
            },
        ],
        "evidenceSourceIds": ["inspection-report-page-2", *cited_ids],
        "uncertainties": [
            "The image does not establish the remaining wall thickness."
        ],
    }


def draft_request() -> DraftRequest:
    """Return grounded input containing one observed finding and one SOP chunk."""

    return DraftRequest(
        subject="Corrosion follow-up",
        objective="Prepare an approval-note draft.",
        findings=(sample_finding(),),
        evidence=(sample_evidence_chunk(),),
        template_instructions="Use concise approval-note language.",
    )


async def test_grounded_drafting_returns_complete_cited_approval_content() -> None:
    """Return render-ready fields whose critical claims cite supplied source IDs."""

    output = grounded_draft_output(evidence_source_ids=["sop-corrosion-page-7"])
    adapter = ScriptedTextAdapter((recorded_output(output),))
    generator = StructuredTextGenerator(adapter, sample_model_profile())

    result = await generator.create_grounded_draft(draft_request())

    assert isinstance(result, GroundedDraft)
    assert result.subject == "Corrosion follow-up"
    assert len(result.critical_claims) == 2
    assert result.uncertainties
    assert set(result.evidence_source_ids) == {
        "inspection-report-page-2",
        "sop-corrosion-page-7",
    }
    request = adapter.requests[0]
    expected_schema = GroundedDraft.model_json_schema(by_alias=True)
    assert request.output_schema == expected_schema
    assert json.dumps(expected_schema, sort_keys=True) in request.user_prompt
    assert "UNTRUSTED FINDINGS BEGIN" in request.user_prompt
    assert "UNTRUSTED RETRIEVED EVIDENCE BEGIN" in request.user_prompt
    assert "grounded-approval-draft-v1" in request.user_prompt
    assert request.temperature == 0.2


async def test_unsupported_critical_claim_is_retried_then_rejected() -> None:
    """Reject model-invented source IDs instead of rendering an unsupported claim."""

    unsupported = grounded_draft_output(evidence_source_ids=["invented-source"])
    adapter = ScriptedTextAdapter(
        (recorded_output(unsupported), recorded_output(unsupported))
    )
    generator = StructuredTextGenerator(adapter, sample_model_profile())

    with pytest.raises(GroundingViolation, match="after one retry"):
        await generator.create_grounded_draft(draft_request())

    assert len(adapter.requests) == 2
    assert "previous draft was invalid" in adapter.requests[1].user_prompt
