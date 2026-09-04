"""Tests for strict WorkBench-owned Ollama request payloads."""

import pytest
from pydantic import ValidationError

from app.ai.models.ollama_wire import (
    OllamaChatMessage,
    OllamaChatRequest,
    OllamaEmbedRequest,
    OllamaGenerationOptions,
    OllamaUnloadRequest,
)


def test_chat_request_serializes_the_validated_ollama_shape() -> None:
    """Build outgoing JSON from a strict request model."""

    request = OllamaChatRequest(
        model="qwen3:4b",
        messages=(
            OllamaChatMessage(role="system", content="Return JSON."),
            OllamaChatMessage(role="user", content="Return status."),
        ),
        format={"type": "object"},
        keep_alive="5m",
        options=OllamaGenerationOptions(
            temperature=0,
            num_ctx=8_192,
            num_predict=1_024,
        ),
    )

    payload = request.model_dump(mode="json", exclude_none=True)

    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["messages"][1] == {"role": "user", "content": "Return status."}


@pytest.mark.parametrize(
    ("request_type", "payload"),
    (
        (
            OllamaEmbedRequest,
            {
                "model": "qwen3-embedding:0.6b",
                "input": ["sanitized text"],
                "truncate": False,
                "unknown": True,
            },
        ),
        (
            OllamaUnloadRequest,
            {
                "model": "qwen3:4b",
                "messages": [],
                "stream": False,
                "keep_alive": 0,
                "pull": True,
            },
        ),
    ),
)
def test_request_models_reject_unknown_fields(
    request_type: type[OllamaEmbedRequest] | type[OllamaUnloadRequest],
    payload: dict[str, object],
) -> None:
    """Turn misspelled or unapproved outgoing fields into local validation errors."""

    with pytest.raises(ValidationError):
        request_type.model_validate(payload)


def test_unload_request_cannot_be_changed_into_streaming_or_keepalive() -> None:
    """Preserve the exact controlled-unload semantics at runtime."""

    with pytest.raises(ValidationError):
        OllamaUnloadRequest.model_validate(
            {
                "model": "qwen3:4b",
                "messages": [],
                "stream": True,
                "keep_alive": "5m",
            }
        )


def test_unload_request_rejects_inference_messages() -> None:
    """Prevent the dedicated unload shape from carrying prompt content."""

    with pytest.raises(ValidationError, match="must not contain messages"):
        OllamaUnloadRequest(
            model="qwen3:4b",
            messages=(OllamaChatMessage(role="user", content="Do work"),),
        )
