"""Regression tests for content-free model-attempt observations."""

import json

import httpx
import pytest

from app.ai.errors import InvalidStructuredOutput
from app.ai.evaluation.observability import ObservedModelAdapter
from app.ai.evaluation.samples import sample_generation_limits
from app.ai.fakes import FakeModelAdapter
from app.ai.models.ollama import create_ollama_adapter
from app.ai.models.ollama_http import OllamaSettings
from app.ai.models.profiles import load_model_profile
from app.ai.schemas import Capability, EmbeddingRequest, TextGenerationRequest


def _model_record(name: str) -> dict[str, object]:
    return {
        "name": name,
        "model": name,
        "size": 1,
        "digest": f"sha256:{name}",
        "details": {},
    }


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
    assert snapshot.model_client_elapsed_ms == 12.5
    assert snapshot.model_total_duration_ns == 12_000_000
    assert snapshot.model_load_duration_ns == 2_000_000
    assert snapshot.generation_duration_ns == 7_000_000
    assert snapshot.model_selections == ((Capability.TEXT, "qwen3:4b"),)


@pytest.mark.parametrize(
    "response_updates",
    (
        {"message": {"role": "assistant", "content": "not-json"}},
        {"model": "unexpected-model:latest"},
        {"done": False},
    ),
    ids=("malformed-content", "model-mismatch", "incomplete-response"),
)
async def test_adapter_level_invalid_text_records_failed_fallback_attempt(
    response_updates: dict[str, object],
) -> None:
    """Retain completed fallback evidence for each rejected Ollama response."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [_model_record("qwen3:1.7b")]})
        payload = json.loads(request.content)
        if payload.get("keep_alive") == 0:
            return httpx.Response(200, json={"done": True})
        response_data: dict[str, object] = {
            "model": payload["model"],
            "message": {"role": "assistant", "content": '{"status":"ok"}'},
            "done": True,
            "total_duration": 12_000_000,
            "load_duration": 2_000_000,
            "prompt_eval_count": 10,
            "eval_count": 6,
            "eval_duration": 7_000_000,
        }
        response_data.update(response_updates)
        return httpx.Response(200, json=response_data)

    observed = ObservedModelAdapter(
        create_ollama_adapter(
            settings=OllamaSettings(unload_on_capability_switch=True),
            profile=load_model_profile(),
            transport=httpx.MockTransport(handler),
        )
    )
    request = TextGenerationRequest(
        model="qwen3:4b",
        system_prompt="Return structured output.",
        user_prompt="Use the supplied fixture.",
        output_schema={"type": "object"},
        limits=sample_generation_limits(),
    )
    try:
        with pytest.raises(InvalidStructuredOutput):
            await observed.generate_text(request)
    finally:
        await observed.close()

    snapshot = observed.snapshot()
    assert snapshot.schema_failures == 1
    assert snapshot.fallback_uses == 1
    assert snapshot.model_invocations == 1
    assert snapshot.prompt_tokens == 10
    assert snapshot.generated_tokens == 6
    assert snapshot.model_total_duration_ns == 12_000_000
    assert snapshot.model_load_duration_ns == 2_000_000
    assert snapshot.generation_duration_ns == 7_000_000
    assert snapshot.model_client_elapsed_ms >= 0
    assert snapshot.model_selections == ((Capability.TEXT, "qwen3:1.7b"),)


async def test_malformed_ollama_envelope_records_known_attempt_evidence() -> None:
    """Record selected model and client latency even when usage fields cannot parse."""

    model = "qwen3:4b"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [_model_record(model)]})
        payload = json.loads(request.content)
        if payload.get("keep_alive") == 0:
            return httpx.Response(200, json={"done": True})
        return httpx.Response(200, content=b"not-json")

    observed = ObservedModelAdapter(
        create_ollama_adapter(
            settings=OllamaSettings(unload_on_capability_switch=True),
            profile=load_model_profile(),
            transport=httpx.MockTransport(handler),
        )
    )
    try:
        with pytest.raises(InvalidStructuredOutput):
            await observed.generate_text(
                TextGenerationRequest(
                    model=model,
                    system_prompt="Return structured output.",
                    user_prompt="Use the supplied fixture.",
                    output_schema={"type": "object"},
                    limits=sample_generation_limits(),
                )
            )
    finally:
        await observed.close()

    snapshot = observed.snapshot()
    assert snapshot.schema_failures == 1
    assert snapshot.model_invocations == 1
    assert snapshot.prompt_tokens == 0
    assert snapshot.generated_tokens == 0
    assert snapshot.model_client_elapsed_ms >= 0
    assert snapshot.model_selections == ((Capability.TEXT, model),)


async def test_adapter_level_invalid_embeddings_record_completed_attempt() -> None:
    """Retain embedding metrics when Ollama returns the wrong vector count."""

    model = "qwen3-embedding:0.6b"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [_model_record(model)]})
        return httpx.Response(
            200,
            json={
                "model": model,
                "embeddings": [[0.1, 0.2]],
                "total_duration": 8_000_000,
                "load_duration": 1_000_000,
                "prompt_eval_count": 4,
            },
        )

    observed = ObservedModelAdapter(
        create_ollama_adapter(
            settings=OllamaSettings(),
            profile=load_model_profile(),
            transport=httpx.MockTransport(handler),
        )
    )
    try:
        with pytest.raises(InvalidStructuredOutput):
            await observed.create_embeddings(
                EmbeddingRequest(model=model, inputs=("first", "second"))
            )
    finally:
        await observed.close()

    snapshot = observed.snapshot()
    assert snapshot.schema_failures == 1
    assert snapshot.model_invocations == 1
    assert snapshot.prompt_tokens == 4
    assert snapshot.model_total_duration_ns == 8_000_000
    assert snapshot.model_load_duration_ns == 1_000_000
    assert snapshot.model_client_elapsed_ms >= 0
    assert snapshot.model_selections == ((Capability.EMBEDDING, model),)
