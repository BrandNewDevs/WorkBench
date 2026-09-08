"""Opt-in acceptance check for the active local Qwen conversation profile."""

import os

import pytest

from app.ai.generation.conversation import LocalConversationGenerator
from app.ai.models import create_ollama_adapter, load_model_profile
from app.ai.schemas import (
    Capability,
    ConversationMessage,
    ConversationRequest,
    ModelStatus,
)

pytestmark = [
    pytest.mark.live_ollama,
    pytest.mark.skipif(
        os.getenv("WORKBENCH_RUN_LIVE_OLLAMA") != "1",
        reason="set WORKBENCH_RUN_LIVE_OLLAMA=1 with the profile's text model preloaded",
    ),
]


async def test_active_qwen_profile_passes_three_local_conversation_sessions() -> None:
    """Verify identity, clean output, and context three times on the selected device."""

    profile = load_model_profile()
    expected_model = profile.text_candidates[0]
    adapter = create_ollama_adapter(profile=profile)
    try:
        health = await adapter.health(profile)
        text_health = next(
            item for item in health.models if item.capability is Capability.TEXT
        )
        if (
            not health.runtime_ready
            or text_health.status is not ModelStatus.READY
            or text_health.selected_model != expected_model
        ):
            pytest.skip(f"preloaded {expected_model} is unavailable for local chat acceptance")

        generator = LocalConversationGenerator(adapter, profile)
        for run_number in range(1, 4):
            session_id = f"live-local-chat-{run_number}"
            memory_token = f"LOCAL-CONTEXT-{run_number}"
            first = await generator.reply(
                ConversationRequest(
                    session_id=session_id,
                    user_message=(
                        "Briefly introduce yourself, name the local model currently generating "
                        f"your answer, and remember this token: {memory_token}."
                    ),
                ),
                model=expected_model,
            )
            second = await generator.reply(
                ConversationRequest(
                    session_id=session_id,
                    user_message="What exact token did I ask you to remember?",
                    history=(
                        ConversationMessage(
                            role="user",
                            content=(
                                "Briefly introduce yourself, name the local model currently "
                                f"generating your answer, and remember this token: {memory_token}."
                            ),
                        ),
                        ConversationMessage(role="assistant", content=first.assistant_text),
                    ),
                ),
                model=expected_model,
            )

            combined = f"{first.assistant_text}\n{second.assistant_text}".lower()
            assert first.model == expected_model
            assert second.model == expected_model
            assert first.used_fallback is False
            assert second.used_fallback is False
            assert "workbench" in first.assistant_text.lower()
            assert expected_model.lower() in first.assistant_text.lower()
            assert memory_token.lower() in second.assistant_text.lower()
            assert "<think>" not in combined
            assert "</think>" not in combined
    finally:
        await adapter.close()
