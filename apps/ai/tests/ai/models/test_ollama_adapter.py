"""Contract tests for local Ollama model operations and fallback behavior."""

import json
from collections.abc import Callable, Coroutine
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.ai.errors import InvalidStructuredOutput, ModelNotInstalled, OllamaPolicyViolation
from app.ai.models.ollama import OllamaModelAdapter, create_ollama_adapter
from app.ai.models.ollama_http import OllamaSettings
from app.ai.models.ollama_wire import OllamaEmbedResponse
from app.ai.models.profiles import load_model_profile
from app.ai.schemas import (
    Capability,
    EmbeddingRequest,
    ModelStatus,
    TextGenerationRequest,
    VisionGenerationRequest,
)

RequestHandler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


def model_record(name: str) -> dict[str, object]:
    """Build a minimal tags record for one installed test model."""

    return {
        "name": name,
        "model": name,
        "size": 2_500_000_000,
        "digest": f"sha256:{name}",
        "details": {
            "family": name.split(":")[0],
            "parameter_size": "4B",
            "quantization_level": "Q4_K_M",
        },
    }


def tags_response(*names: str) -> dict[str, object]:
    """Build a tags response containing the requested model names."""

    return {"models": [model_record(name) for name in names]}


def chat_response(model: str, content: str = '{"status":"ok"}') -> dict[str, object]:
    """Build a non-streaming Ollama chat response with usage data."""

    return {
        "model": model,
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": "stop",
        "total_duration": 12_000_000,
        "load_duration": 2_000_000,
        "prompt_eval_count": 10,
        "prompt_eval_duration": 3_000_000,
        "eval_count": 6,
        "eval_duration": 7_000_000,
    }


def embed_response(model: str, count: int) -> dict[str, object]:
    """Build an Ollama embedding response with one vector per input."""

    return {
        "model": model,
        "embeddings": [[0.1, 0.2, 0.3] for _ in range(count)],
        "total_duration": 8_000_000,
        "load_duration": 1_000_000,
        "prompt_eval_count": count * 4,
    }


def adapter_for(handler: RequestHandler) -> OllamaModelAdapter:
    """Create an adapter against an in-memory local HTTP transport."""

    return create_ollama_adapter(
        settings=OllamaSettings(unload_on_capability_switch=True),
        profile=load_model_profile(),
        transport=httpx.MockTransport(handler),
    )


async def test_list_models_maps_local_registry_metadata() -> None:
    """Validate and map the tags endpoint without loading a model."""

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json=tags_response("qwen3:4b"))

    adapter = adapter_for(handler)
    try:
        models = await adapter.list_models()
    finally:
        await adapter.close()

    assert models[0].name == "qwen3:4b"
    assert models[0].family == "qwen3"
    assert models[0].size_bytes == 2_500_000_000


async def test_health_selects_safe_profile_models() -> None:
    """Report the preferred text, vision, and embedding candidates as ready."""

    installed = ("qwen3:4b", "qwen3-vl:4b", "qwen3-embedding:0.6b")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=tags_response(*installed))

    profile = load_model_profile()
    adapter = adapter_for(handler)
    try:
        health = await adapter.health(profile)
    finally:
        await adapter.close()

    selected = {item.capability: item.selected_model for item in health.models}
    assert health.runtime_ready is True
    assert selected == {
        Capability.TEXT: "qwen3:4b",
        Capability.VISION: "qwen3-vl:4b",
        Capability.EMBEDDING: "qwen3-embedding:0.6b",
    }


async def test_health_distinguishes_missing_models() -> None:
    """Keep a reachable runtime separate from absent approved models."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=tags_response())

    profile = load_model_profile()
    adapter = adapter_for(handler)
    try:
        health = await adapter.health(profile)
    finally:
        await adapter.close()

    assert health.runtime_ready is True
    assert {item.status for item in health.models} == {ModelStatus.MISSING}


async def test_health_distinguishes_unavailable_runtime() -> None:
    """Return typed unavailable health rather than raising into the admin workflow."""

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("test runtime unavailable", request=request)

    profile = load_model_profile()
    adapter = adapter_for(handler)
    try:
        health = await adapter.health(profile)
    finally:
        await adapter.close()

    assert health.runtime_ready is False
    assert {item.status for item in health.models} == {ModelStatus.UNAVAILABLE}


async def test_structured_text_generation_is_local_non_streaming_and_measured() -> None:
    """Send the expected chat wire format and preserve non-sensitive metrics."""

    payloads: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=tags_response("qwen3:4b"))
        payload = json.loads(request.content)
        payloads.append(payload)
        return httpx.Response(200, json=chat_response(payload["model"]))

    profile = load_model_profile()
    adapter = adapter_for(handler)
    request = TextGenerationRequest(
        model="qwen3:4b",
        system_prompt="Return the required schema.",
        user_prompt="Summarize sanitized input.",
        output_schema={"type": "object"},
        limits=profile.text_limits,
    )
    result = await adapter.generate_text(request)
    await adapter.close()

    generation_payload = payloads[0]
    assert generation_payload["stream"] is False
    assert generation_payload["think"] is False
    assert generation_payload["format"] == {"type": "object"}
    assert result.structured_output == {"status": "ok"}
    assert result.metrics.prompt_eval_count == 10
    assert result.metrics.eval_count == 6
    assert result.metrics.client_elapsed_ms >= 0


async def test_embedding_generation_uses_local_embed_endpoint() -> None:
    """Return one validated local vector for every input string."""

    payloads: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=tags_response("qwen3-embedding:0.6b"))
        assert request.url.path == "/api/embed"
        payload = json.loads(request.content)
        payloads.append(payload)
        return httpx.Response(
            200,
            json=embed_response(payload["model"], len(payload["input"])),
        )

    adapter = adapter_for(handler)
    result = await adapter.create_embeddings(
        EmbeddingRequest(
            model="qwen3-embedding:0.6b",
            inputs=("first sanitized passage", "second sanitized passage"),
        )
    )
    await adapter.close()

    assert len(result.vectors) == 2
    assert payloads == [
        {
            "model": "qwen3-embedding:0.6b",
            "input": ["first sanitized passage", "second sanitized passage"],
            "truncate": False,
        }
    ]


async def test_missing_preferred_model_selects_configured_fallback() -> None:
    """Use only the next configured candidate and record why it was selected."""

    generated_models: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=tags_response("qwen3:1.7b"))
        payload = json.loads(request.content)
        if payload.get("keep_alive") == 0:
            return httpx.Response(200, json={"done": True})
        generated_models.append(payload["model"])
        return httpx.Response(200, json=chat_response(payload["model"]))

    profile = load_model_profile()
    adapter = adapter_for(handler)
    result = await adapter.generate_text(
        TextGenerationRequest(
            model="qwen3:4b",
            system_prompt="Return JSON.",
            user_prompt="Return status.",
            output_schema={"type": "object"},
            limits=profile.text_limits,
        )
    )
    await adapter.close()

    assert generated_models == ["qwen3:1.7b"]
    assert result.model == "qwen3:1.7b"
    assert result.used_fallback is True
    assert "not installed" in (result.fallback_reason or "")


async def test_capacity_failure_unloads_then_tries_one_fallback() -> None:
    """Recover once from a preferred-model load failure without a third attempt."""

    chat_payloads: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json=tags_response("qwen3:4b", "qwen3:1.7b"),
            )
        payload = json.loads(request.content)
        chat_payloads.append(payload)
        if payload.get("keep_alive") == 0:
            return httpx.Response(200, json={"done": True})
        if payload["model"] == "qwen3:4b":
            return httpx.Response(500, json={"error": "CUDA out of memory"})
        return httpx.Response(200, json=chat_response(payload["model"]))

    profile = load_model_profile()
    adapter = adapter_for(handler)
    result = await adapter.generate_text(
        TextGenerationRequest(
            model="qwen3:4b",
            system_prompt="Return JSON.",
            user_prompt="Return status.",
            output_schema={"type": "object"},
            limits=profile.text_limits,
        )
    )
    await adapter.close()

    inference_models = [
        payload["model"] for payload in chat_payloads if payload.get("keep_alive") != 0
    ]
    assert inference_models == ["qwen3:4b", "qwen3:1.7b"]
    assert any(
        payload["model"] == "qwen3:4b" and payload.get("keep_alive") == 0
        for payload in chat_payloads
    )
    assert result.model == "qwen3:1.7b"
    assert result.used_fallback is True


async def test_switching_from_text_to_vision_unloads_the_text_model() -> None:
    """Serialize large-model switches and request immediate unload with keep_alive zero."""

    chat_payloads: list[dict[str, Any]] = []
    installed = ("qwen3:4b", "qwen3-vl:4b")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=tags_response(*installed))
        payload = json.loads(request.content)
        chat_payloads.append(payload)
        if payload.get("keep_alive") == 0:
            return httpx.Response(200, json={"done": True})
        return httpx.Response(200, json=chat_response(payload["model"]))

    profile = load_model_profile()
    adapter = adapter_for(handler)
    await adapter.generate_text(
        TextGenerationRequest(
            model="qwen3:4b",
            system_prompt="Return JSON.",
            user_prompt="Return status.",
            output_schema={"type": "object"},
            limits=profile.text_limits,
        )
    )
    await adapter.generate_vision(
        VisionGenerationRequest(
            model="qwen3-vl:4b",
            system_prompt="Return JSON.",
            user_prompt="Inspect the image.",
            images_base64=("c2FuaXRpemVkLWltYWdl",),
            output_schema={"type": "object"},
            limits=profile.vision_limits,
        )
    )
    await adapter.close()

    assert chat_payloads[1] == {
        "model": "qwen3:4b",
        "messages": [],
        "stream": False,
        "keep_alive": 0,
    }
    assert chat_payloads[2]["model"] == "qwen3-vl:4b"
    assert chat_payloads[2]["messages"][1]["images"] == ["c2FuaXRpemVkLWltYWdl"]


async def test_unapproved_model_is_rejected_before_generation() -> None:
    """Prevent local Ollama cloud or operator-installed models bypassing the registry."""

    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=tags_response("gpt-oss:120b-cloud"))

    profile = load_model_profile()
    adapter = adapter_for(handler)
    with pytest.raises(OllamaPolicyViolation):
        await adapter.generate_text(
            TextGenerationRequest(
                model="gpt-oss:120b-cloud",
                system_prompt="Return JSON.",
                user_prompt="Confidential input.",
                output_schema={"type": "object"},
                limits=profile.text_limits,
            )
        )
    await adapter.close()

    assert requests == []


async def test_absent_primary_and_fallback_raise_model_not_installed() -> None:
    """Return an operator-facing missing-model error without downloading anything."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=tags_response())

    profile = load_model_profile()
    adapter = adapter_for(handler)
    with pytest.raises(ModelNotInstalled, match="not installed"):
        await adapter.generate_text(
            TextGenerationRequest(
                model="qwen3:4b",
                system_prompt="Return JSON.",
                user_prompt="Return status.",
                output_schema={"type": "object"},
                limits=profile.text_limits,
            )
        )
    await adapter.close()


async def test_malformed_structured_chat_output_is_rejected() -> None:
    """Never pass malformed model JSON into backend workflow logic."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=tags_response("qwen3:4b"))
        payload = json.loads(request.content)
        if payload.get("keep_alive") == 0:
            return httpx.Response(200, json={"done": True})
        return httpx.Response(200, json=chat_response(payload["model"], "not-json"))

    profile = load_model_profile()
    adapter = adapter_for(handler)
    with pytest.raises(InvalidStructuredOutput):
        await adapter.generate_text(
            TextGenerationRequest(
                model="qwen3:4b",
                system_prompt="Return JSON.",
                user_prompt="Return status.",
                output_schema={"type": "object"},
                limits=profile.text_limits,
            )
        )
    await adapter.close()


async def test_json_that_does_not_match_output_schema_is_rejected() -> None:
    """Reject syntactically valid JSON with the wrong application structure."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=tags_response("qwen3:4b"))
        payload = json.loads(request.content)
        if payload.get("keep_alive") == 0:
            return httpx.Response(200, json={"done": True})
        return httpx.Response(200, json=chat_response(payload["model"], "[]"))

    profile = load_model_profile()
    adapter = adapter_for(handler)
    with pytest.raises(InvalidStructuredOutput, match="did not match"):
        await adapter.generate_text(
            TextGenerationRequest(
                model="qwen3:4b",
                system_prompt="Return JSON.",
                user_prompt="Return status.",
                output_schema={
                    "type": "object",
                    "required": ["status"],
                    "properties": {"status": {"type": "string"}},
                },
                limits=profile.text_limits,
            )
        )
    await adapter.close()


async def test_invalid_output_schema_is_rejected_before_http() -> None:
    """Catch application schema mistakes before listing or loading a model."""

    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=tags_response("qwen3:4b"))

    profile = load_model_profile()
    adapter = adapter_for(handler)
    with pytest.raises(InvalidStructuredOutput, match="schema is invalid"):
        await adapter.generate_text(
            TextGenerationRequest(
                model="qwen3:4b",
                system_prompt="Return JSON.",
                user_prompt="Return status.",
                output_schema={"type": "not-a-json-schema-type"},
                limits=profile.text_limits,
            )
        )
    await adapter.close()

    assert requests == []


async def test_external_schema_reference_is_rejected_before_http() -> None:
    """Prevent JSON Schema validation from resolving anything over a network."""

    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=tags_response("qwen3:4b"))

    profile = load_model_profile()
    adapter = adapter_for(handler)
    with pytest.raises(OllamaPolicyViolation, match="external JSON Schema"):
        await adapter.generate_text(
            TextGenerationRequest(
                model="qwen3:4b",
                system_prompt="Return JSON.",
                user_prompt="Return status.",
                output_schema={"$ref": "https://example.com/confidential-schema.json"},
                limits=profile.text_limits,
            )
        )
    await adapter.close()

    assert requests == []


@pytest.mark.parametrize("non_finite", ("NaN", "Infinity", "-Infinity"))
def test_ollama_embedding_response_rejects_non_finite_values(non_finite: str) -> None:
    """Reject invalid numeric values at the Ollama wire boundary."""

    with pytest.raises(ValidationError):
        OllamaEmbedResponse.model_validate(
            {
                "model": "qwen3-embedding:0.6b",
                "embeddings": [[non_finite]],
            }
        )
