"""Tests for bounded, text-only local conversation generation."""

import pytest

from app.ai.errors import ConversationContextTooLarge
from app.ai.evaluation.samples import sample_inference_metrics, sample_model_profile
from app.ai.generation.conversation import LocalConversationGenerator
from app.ai.models.ports import ModelAdapter
from app.ai.schemas import (
    ConversationGenerationRequest,
    ConversationGenerationResult,
    ConversationMessage,
    ConversationRequest,
    EmbeddingRequest,
    EmbeddingResult,
    InstalledModel,
    ModelProfile,
    ModelRuntimeHealth,
    TextGenerationRequest,
    TextGenerationResult,
    VisionGenerationRequest,
)


class RecordingConversationAdapter:
    """Capture conversation requests without contacting a local model runtime."""

    def __init__(self) -> None:
        self.requests: list[ConversationGenerationRequest] = []

    async def list_models(self) -> tuple[InstalledModel, ...]:
        return ()

    async def health(self, profile: ModelProfile) -> ModelRuntimeHealth:
        del profile
        return ModelRuntimeHealth(runtime_ready=True, models=())

    async def generate_text(self, request: TextGenerationRequest) -> TextGenerationResult:
        del request
        raise AssertionError("ordinary chat must not use structured text generation")

    async def generate_conversation(
        self, request: ConversationGenerationRequest
    ) -> ConversationGenerationResult:
        self.requests.append(request)
        return ConversationGenerationResult(
            model=request.model,
            text="The local answer is ready.",
            metrics=sample_inference_metrics(),
        )

    async def generate_vision(self, request: VisionGenerationRequest) -> TextGenerationResult:
        del request
        raise AssertionError("ordinary chat must not use vision")

    async def create_embeddings(self, request: EmbeddingRequest) -> EmbeddingResult:
        del request
        raise AssertionError("ordinary chat must not use embeddings")

    async def unload(self) -> None:
        return None

    async def close(self) -> None:
        return None


def _accepts_model_adapter(adapter: ModelAdapter) -> ModelAdapter:
    """Keep the local recorder honest against the shared adapter contract."""

    return adapter


async def test_local_conversation_passes_exact_history_and_current_message() -> None:
    """Never silently trim, summarise, retrieve, or invoke a tool for basic chat."""

    adapter = RecordingConversationAdapter()
    generator = LocalConversationGenerator(adapter, sample_model_profile())

    reply = await generator.reply(
        ConversationRequest(
            session_id="session-chat-001",
            user_message="What did I ask you to remember?",
            history=(
                ConversationMessage(role="user", content="Remember the valve identifier V-17."),
                ConversationMessage(role="assistant", content="I will use V-17 in this chat."),
            ),
            timeout_seconds=30,
        ),
        model="qwen3:4b",
    )

    assert _accepts_model_adapter(adapter) is adapter
    assert reply.session_id == "session-chat-001"
    assert reply.assistant_text == "The local answer is ready."
    assert len(adapter.requests) == 1
    request = adapter.requests[0]
    assert request.model == "qwen3:4b"
    assert request.timeout_seconds == 30
    assert request.temperature == 0.2
    assert request.messages == (
        ConversationMessage(role="user", content="Remember the valve identifier V-17."),
        ConversationMessage(role="assistant", content="I will use V-17 in this chat."),
        ConversationMessage(role="user", content="What did I ask you to remember?"),
    )


@pytest.mark.parametrize(
    "history",
    (
        (
            ConversationMessage(role="user", content="First"),
            ConversationMessage(role="user", content="Second"),
        ),
        (ConversationMessage(role="user", content="Unanswered"),),
    ),
)
def test_conversation_request_rejects_incomplete_or_unordered_history(
    history: tuple[ConversationMessage, ...],
) -> None:
    """Require Backend 1 to supply complete, ordered session context."""

    with pytest.raises(ValueError, match="history"):
        ConversationRequest(
            session_id="session-chat-001",
            user_message="Continue.",
            history=history,
        )


async def test_local_conversation_rejects_context_over_budget_without_a_model_call() -> None:
    """Prevent an oversized history from being silently truncated by a model runtime."""

    adapter = RecordingConversationAdapter()
    profile = sample_model_profile().model_copy(
        update={
            "text_limits": sample_model_profile().text_limits.model_copy(
                update={"context_window": 16, "max_output_tokens": 8}
            )
        }
    )
    generator = LocalConversationGenerator(adapter, profile)

    with pytest.raises(ConversationContextTooLarge, match="context budget"):
        await generator.reply(
            ConversationRequest(
                session_id="session-chat-001",
                user_message="This cannot fit.",
                history=(
                    ConversationMessage(role="user", content="x" * 100),
                    ConversationMessage(role="assistant", content="Acknowledged."),
                ),
            ),
            model="qwen3:4b",
        )

    assert adapter.requests == []
