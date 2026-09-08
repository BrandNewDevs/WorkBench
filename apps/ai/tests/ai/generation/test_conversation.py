"""Tests for bounded, text-only local conversation generation."""

import pytest

import app.ai.generation.conversation as conversation_module
from app.ai.errors import ConversationContextTooLarge, InvalidStructuredOutput
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

    def __init__(
        self,
        results: tuple[ConversationGenerationResult | InvalidStructuredOutput, ...] = (),
    ) -> None:
        self.requests: list[ConversationGenerationRequest] = []
        self.results = list(results)

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
        if self.results:
            result = self.results.pop(0)
            if isinstance(result, InvalidStructuredOutput):
                raise result
            return result
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
    assert request.assistant_name == "WorkBench"
    assert request.disclose_runtime_model is True
    assert "local-conversation-v2" in request.system_prompt
    assert "pretrained knowledge" in request.system_prompt
    assert "official authority" in request.system_prompt
    assert "Who is the prime minister" not in request.system_prompt
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


async def test_local_conversation_retries_invalid_output_once_without_changing_history() -> None:
    """Ask for one corrected final answer, then return only the validated retry."""

    first_error = InvalidStructuredOutput(
        "reasoning leaked into the conversation answer",
        model="qwen3:1.7b",
        metrics=sample_inference_metrics(),
        fallback_reason="Preferred model could not run; tried qwen3:1.7b.",
    )
    adapter = RecordingConversationAdapter(
        (
            first_error,
            ConversationGenerationResult(
                model="qwen3:1.7b",
                text="I am WorkBench, running locally with qwen3:1.7b.",
                metrics=sample_inference_metrics(),
            ),
        )
    )
    generator = LocalConversationGenerator(adapter, sample_model_profile())

    reply = await generator.reply(
        ConversationRequest(session_id="session-chat-retry", user_message="Introduce yourself."),
        model="qwen3:4b",
    )

    assert len(adapter.requests) == 2
    assert adapter.requests[0].messages == adapter.requests[1].messages
    assert "previous response was invalid" not in adapter.requests[0].system_prompt.lower()
    assert "previous response was invalid" in adapter.requests[1].system_prompt.lower()
    assert adapter.requests[1].model == "qwen3:1.7b"
    assert reply.model == "qwen3:1.7b"
    assert reply.used_fallback is True
    assert reply.fallback_reason == first_error.fallback_reason


async def test_local_conversation_retry_uses_only_the_remaining_request_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The correction attempt must not restart the caller's timeout budget."""

    clock = iter((100.0, 106.0))
    monkeypatch.setattr(conversation_module, "perf_counter", lambda: next(clock))
    adapter = RecordingConversationAdapter(
        (
            InvalidStructuredOutput("first invalid response"),
            ConversationGenerationResult(
                model="qwen3:4b",
                text="Corrected final answer.",
                metrics=sample_inference_metrics(),
            ),
        )
    )
    generator = LocalConversationGenerator(adapter, sample_model_profile())

    reply = await generator.reply(
        ConversationRequest(
            session_id="session-chat-deadline",
            user_message="Hello.",
            timeout_seconds=10,
        ),
        model="qwen3:4b",
    )

    assert reply.assistant_text == "Corrected final answer."
    assert [request.timeout_seconds for request in adapter.requests] == [10, 4]


async def test_local_conversation_stops_after_the_second_invalid_output() -> None:
    """Never guess an answer after the one allowed correction attempt."""

    adapter = RecordingConversationAdapter(
        (
            InvalidStructuredOutput("first invalid response"),
            InvalidStructuredOutput("second invalid response"),
        )
    )
    generator = LocalConversationGenerator(adapter, sample_model_profile())

    with pytest.raises(InvalidStructuredOutput, match="second invalid response"):
        await generator.reply(
            ConversationRequest(session_id="session-chat-invalid", user_message="Hello."),
            model="qwen3:4b",
        )

    assert len(adapter.requests) == 2
