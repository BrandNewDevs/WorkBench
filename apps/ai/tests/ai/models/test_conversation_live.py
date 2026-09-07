"""Opt-in two-turn Qwen3:4b smoke test for the local Jetson conversation path."""

import os

import pytest

from app.ai.models import create_ollama_adapter, load_model_profile
from app.ai.schemas import (
    Capability,
    ConversationGenerationRequest,
    ConversationMessage,
    ModelStatus,
)

pytestmark = [
    pytest.mark.live_ollama,
    pytest.mark.skipif(
        os.getenv("WORKBENCH_RUN_LIVE_OLLAMA") != "1",
        reason="set WORKBENCH_RUN_LIVE_OLLAMA=1 with preloaded qwen3:4b",
    ),
]


async def test_preloaded_qwen3_4b_keeps_two_turn_local_conversation_context() -> None:
    """Verify a real local Qwen response and one follow-up without downloads or web access."""

    profile = load_model_profile()
    adapter = create_ollama_adapter(profile=profile)
    try:
        health = await adapter.health(profile)
        text_health = next(
            item for item in health.models if item.capability is Capability.TEXT
        )
        if (
            not health.runtime_ready
            or text_health.status is not ModelStatus.READY
            or text_health.selected_model != "qwen3:4b"
        ):
            pytest.skip("preloaded qwen3:4b is unavailable for the local conversation check")

        first = await adapter.generate_conversation(
            ConversationGenerationRequest(
                model="qwen3:4b",
                system_prompt="Reply briefly and only from the supplied conversation.",
                messages=(
                    ConversationMessage(
                        role="user",
                        content="Remember this exact token: ORIN-LOCAL-TEST.",
                    ),
                ),
                limits=profile.text_limits,
            )
        )
        second = await adapter.generate_conversation(
            ConversationGenerationRequest(
                model="qwen3:4b",
                system_prompt="Reply briefly and only from the supplied conversation.",
                messages=(
                    ConversationMessage(
                        role="user",
                        content="Remember this exact token: ORIN-LOCAL-TEST.",
                    ),
                    ConversationMessage(role="assistant", content=first.text),
                    ConversationMessage(
                        role="user",
                        content="What exact token did I ask you to remember?",
                    ),
                ),
                limits=profile.text_limits,
            )
        )
    finally:
        await adapter.close()

    assert first.model == "qwen3:4b"
    assert first.text.strip()
    assert second.model == "qwen3:4b"
    assert "ORIN-LOCAL-TEST" in second.text.upper()
