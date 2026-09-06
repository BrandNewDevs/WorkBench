"""Regression tests for content-free model-attempt observations."""

import pytest

from app.ai.errors import InvalidStructuredOutput
from app.ai.evaluation.observability import ObservedModelAdapter
from app.ai.evaluation.samples import sample_generation_limits
from app.ai.fakes import FakeModelAdapter
from app.ai.schemas import Capability, TextGenerationRequest


async def test_completed_invalid_result_still_records_model_and_metrics() -> None:
    """Keep diagnostic evidence for an invocation whose returned JSON fails its schema."""

    observed = ObservedModelAdapter(FakeModelAdapter())
    request = TextGenerationRequest(
        model="qwen3:4b",
        system_prompt="Return structured output.",
        user_prompt="Use the supplied fixture.",
        output_schema={
            "type": "object",
            "required": ["requiredField"],
            "properties": {"requiredField": {"type": "string"}},
        },
        limits=sample_generation_limits(),
    )

    with pytest.raises(InvalidStructuredOutput):
        await observed.generate_text(request)

    snapshot = observed.snapshot()
    assert snapshot.schema_failures == 1
    assert dict(snapshot.schema_failures_by_capability)[Capability.TEXT] == 1
    assert snapshot.model_invocations == 1
    assert snapshot.prompt_tokens == 12
    assert snapshot.generated_tokens == 8
    assert snapshot.model_total_duration_ns == 12_000_000
    assert snapshot.model_load_duration_ns == 2_000_000
    assert snapshot.generation_duration_ns == 7_000_000
    assert snapshot.model_selections == ((Capability.TEXT, "qwen3:4b"),)
