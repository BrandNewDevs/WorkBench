"""Behavior tests for structured local text generation workflows."""

import json
from collections.abc import Sequence

from pydantic import JsonValue

from app.ai.evaluation.samples import (
    sample_inference_metrics,
    sample_model_profile,
    sample_task,
)
from app.ai.fakes import FakeModelAdapter
from app.ai.generation import StructuredTextGenerator
from app.ai.schemas import (
    AgentContext,
    ConversationMessage,
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
