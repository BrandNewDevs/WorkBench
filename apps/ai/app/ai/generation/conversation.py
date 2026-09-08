"""Bounded ordinary text conversation over the injected local model seam."""

from app.ai.errors import ConversationContextTooLarge, InvalidStructuredOutput
from app.ai.models.ports import ModelAdapter
from app.ai.prompts.conversation import build_local_conversation_system_prompt
from app.ai.schemas import (
    ConversationGenerationRequest,
    ConversationMessage,
    ConversationReply,
    ConversationRequest,
    ModelProfile,
)

_CONSERVATIVE_CHARACTERS_PER_TOKEN = 2


class LocalConversationGenerator:
    """Generate a single local reply without tools, retrieval, or hidden history edits."""

    def __init__(self, model_adapter: ModelAdapter, model_profile: ModelProfile) -> None:
        self._model_adapter = model_adapter
        self._model_profile = model_profile

    async def reply(self, request: ConversationRequest, *, model: str) -> ConversationReply:
        """Return one validated reply for the exact supplied history and user message."""

        messages = (
            *request.history,
            ConversationMessage(role="user", content=request.user_message),
        )
        self._require_context_budget(messages)
        selected_model = model
        preserved_fallback_reason: str | None = None
        generation = None
        for attempt in range(2):
            try:
                generation = await self._model_adapter.generate_conversation(
                    ConversationGenerationRequest(
                        model=selected_model,
                        system_prompt=build_local_conversation_system_prompt(
                            correction=attempt == 1
                        ),
                        assistant_name="WorkBench",
                        disclose_runtime_model=True,
                        messages=messages,
                        limits=self._model_profile.text_limits,
                        timeout_seconds=request.timeout_seconds,
                        temperature=0.2,
                    )
                )
                break
            except InvalidStructuredOutput as error:
                if error.fallback_reason is not None:
                    preserved_fallback_reason = error.fallback_reason
                if error.model is not None:
                    selected_model = error.model
                if attempt == 1:
                    error.attach_fallback_reason(
                        error.fallback_reason or preserved_fallback_reason
                    )
                    raise

        if generation is None:  # pragma: no cover - the loop returns or raises
            raise InvalidStructuredOutput("local conversation returned no generation")
        fallback_reason = generation.fallback_reason or preserved_fallback_reason
        return ConversationReply(
            session_id=request.session_id,
            assistant_text=generation.text,
            model=generation.model,
            metrics=generation.metrics,
            used_fallback=generation.used_fallback or fallback_reason is not None,
            fallback_reason=fallback_reason,
        )

    def _require_context_budget(self, messages: tuple[ConversationMessage, ...]) -> None:
        """Reject oversized input rather than silently dropping confidential history.

        Ollama accepts a token budget, not a deterministic tokenizer interface.  Two
        characters per token is deliberately conservative for mixed-language input.
        The profile also reserves its configured output budget for the assistant reply.
        """

        limits = self._model_profile.text_limits
        available_tokens = limits.context_window - limits.max_output_tokens
        character_budget = available_tokens * _CONSERVATIVE_CHARACTERS_PER_TOKEN
        input_characters = len(
            build_local_conversation_system_prompt(correction=True)
        ) + sum(
            len(message.content) for message in messages
        )
        if available_tokens <= 0 or input_characters > character_budget:
            raise ConversationContextTooLarge(
                "conversation history exceeds the configured local context budget"
            )
