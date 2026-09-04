"""Opt-in checks against the separately installed local Ollama runtime."""

import os

import pytest

from app.ai.models.ollama import create_ollama_adapter
from app.ai.models.profiles import load_model_profile
from app.ai.schemas import Capability, EmbeddingRequest, ModelStatus, TextGenerationRequest

pytestmark = [
    pytest.mark.live_ollama,
    pytest.mark.skipif(
        os.getenv("WORKBENCH_RUN_LIVE_OLLAMA") != "1",
        reason="set WORKBENCH_RUN_LIVE_OLLAMA=1 on a machine with preloaded approved models",
    ),
]


async def test_preloaded_models_support_text_and_embedding_requests() -> None:
    """Verify the real local runtime without downloading a missing model."""

    profile = load_model_profile()
    adapter = create_ollama_adapter(profile=profile)
    try:
        health = await adapter.health(profile)
        health_by_capability = {item.capability: item for item in health.models}
        assert health.runtime_ready is True
        assert health_by_capability[Capability.TEXT].status is ModelStatus.READY
        assert health_by_capability[Capability.EMBEDDING].status is ModelStatus.READY

        text_model = health_by_capability[Capability.TEXT].selected_model
        embedding_model = health_by_capability[Capability.EMBEDDING].selected_model
        assert text_model is not None
        assert embedding_model is not None

        text_result = await adapter.generate_text(
            TextGenerationRequest(
                model=text_model,
                system_prompt="Return JSON matching the supplied schema.",
                user_prompt="Return a short status confirming local inference.",
                output_schema={
                    "type": "object",
                    "properties": {"status": {"type": "string"}},
                    "required": ["status"],
                },
                limits=profile.text_limits,
            )
        )
        embedding_result = await adapter.create_embeddings(
            EmbeddingRequest(
                model=embedding_model,
                inputs=("sanitized local integration test",),
            )
        )
    finally:
        await adapter.close()

    assert text_result.model == text_model
    assert isinstance(text_result.structured_output, dict)
    assert len(embedding_result.vectors) == 1
    assert embedding_result.vectors[0]
