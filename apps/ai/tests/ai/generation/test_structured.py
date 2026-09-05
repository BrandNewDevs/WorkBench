"""Behavior tests for structured local text generation workflows."""

import json
from collections.abc import Sequence

import pytest
from pydantic import JsonValue

from app.ai.errors import GroundingViolation, InvalidToolProposal
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
    CodeRepairContent,
    CodeRepairRequest,
    ConversationMessage,
    DraftRequest,
    GroundedDraft,
    ProposedToolCall,
    TaskPlan,
    TextGenerationRequest,
    TextGenerationResult,
    ToolDefinition,
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


def export_tool() -> ToolDefinition:
    """Return one Backend 1-owned tool contract for proposal tests."""

    return ToolDefinition(
        name="request_document_export",
        description="Ask Backend 1 to consider exporting the approved draft.",
        input_schema={
            "type": "object",
            "properties": {"format": {"type": "string", "enum": ["docx", "pdf"]}},
            "required": ["format"],
            "additionalProperties": False,
        },
    )


async def test_tool_proposal_returns_only_an_allowed_validated_call() -> None:
    """Propose one Backend 1-supplied tool without invoking it."""

    output: dict[str, JsonValue] = {
        "toolName": "request_document_export",
        "arguments": {"format": "docx"},
        "explanation": "The employee requested a Word deliverable.",
    }
    adapter = ScriptedTextAdapter((recorded_output(output),))
    generator = StructuredTextGenerator(adapter, sample_model_profile())
    context = AgentContext(
        task=sample_task(),
        conversation=(
            ConversationMessage(role="user", content="Export the draft as a Word file."),
        ),
        allowed_tools=(export_tool(),),
    )

    result = await generator.propose_action(context)

    assert result.response_text is None
    assert result.tool_call is not None
    assert result.tool_call.tool_name == "request_document_export"
    assert result.tool_call.arguments == {"format": "docx"}
    request = adapter.requests[0]
    expected_schema = ProposedToolCall.model_json_schema(by_alias=True)
    assert request.output_schema == expected_schema
    assert json.dumps(expected_schema, sort_keys=True) in request.user_prompt
    assert "request_document_export" in request.user_prompt
    assert "tool-proposal-v1" in request.user_prompt
    assert request.temperature == 0


@pytest.mark.parametrize(
    "output",
    (
        {
            "toolName": "arbitrary_shell",
            "arguments": {},
            "explanation": "Use an unapproved tool.",
        },
        {
            "toolName": "request_document_export",
            "arguments": {"format": "xlsx"},
            "explanation": "Use an argument outside the allowed schema.",
        },
    ),
    ids=("unknown-tool", "invalid-arguments"),
)
async def test_unknown_tools_and_invalid_arguments_are_retried_then_rejected(
    output: dict[str, JsonValue],
) -> None:
    """Enforce Backend 1's exact registry after structured model generation."""

    adapter = ScriptedTextAdapter((recorded_output(output), recorded_output(output)))
    generator = StructuredTextGenerator(adapter, sample_model_profile())
    context = AgentContext(
        task=sample_task(),
        conversation=(ConversationMessage(role="user", content="Export the draft."),),
        allowed_tools=(export_tool(),),
    )

    with pytest.raises(InvalidToolProposal, match="after one retry"):
        await generator.propose_action(context)

    assert len(adapter.requests) == 2
    assert "previous tool proposal was invalid" in adapter.requests[1].user_prompt


async def test_no_allowed_tools_returns_text_without_model_inference() -> None:
    """Avoid asking a model to invent an action when Backend 1 allows none."""

    adapter = ScriptedTextAdapter(())
    generator = StructuredTextGenerator(adapter, sample_model_profile())
    context = AgentContext(
        task=sample_task(),
        conversation=(),
        allowed_tools=(),
    )

    result = await generator.propose_action(context)

    assert result.response_text == "No backend-approved tools are available."
    assert result.tool_call is None
    assert adapter.requests == []


def code_repair_output(*, language: str = "python") -> dict[str, JsonValue]:
    """Return recorded corrected code without model-controlled runtime metadata."""

    return {
        "language": language,
        "correctedCode": "def total(values):\n    return sum(values)\n",
        "changeSummary": "Replaced the fixed total with a sum of the supplied values.",
    }


def code_repair_request() -> CodeRepairRequest:
    """Return sanitized sandbox feedback for a failed Python test."""

    return CodeRepairRequest(
        task="Correct the total calculation without changing the function name.",
        language="python",
        code="def total(values):\n    return 3\n",
        test_output="test_total: expected 4, got 3",
        error_output="AssertionError: 3 != 4",
    )


async def test_code_repair_uses_sandbox_feedback_and_returns_corrected_code() -> None:
    """Return structured source for Backend 2 to test in a later sandbox run."""

    adapter = ScriptedTextAdapter(
        (recorded_output(code_repair_output(), model="qwen3:1.7b"),)
    )
    generator = StructuredTextGenerator(adapter, sample_model_profile())

    result = await generator.repair_code(code_repair_request())

    assert result.language == "python"
    assert "return sum(values)" in result.corrected_code
    assert result.change_summary
    assert result.model == "qwen3:1.7b"
    request = adapter.requests[0]
    expected_schema = CodeRepairContent.model_json_schema(by_alias=True)
    assert request.output_schema == expected_schema
    assert json.dumps(expected_schema, sort_keys=True) in request.user_prompt
    assert "expected 4, got 3" in request.user_prompt
    assert "AssertionError: 3 != 4" in request.user_prompt
    assert "code-repair-v1" in request.user_prompt
    assert request.temperature == 0


async def test_code_repair_retries_when_model_changes_the_language() -> None:
    """Keep the requested language application-controlled across one correction."""

    adapter = ScriptedTextAdapter(
        (
            recorded_output(code_repair_output(language="javascript")),
            recorded_output(code_repair_output()),
        )
    )
    generator = StructuredTextGenerator(adapter, sample_model_profile())

    result = await generator.repair_code(code_repair_request())

    assert result.language == "python"
    assert len(adapter.requests) == 2
    assert "previous repair response was invalid" in adapter.requests[1].user_prompt
