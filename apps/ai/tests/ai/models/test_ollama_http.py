"""Security and lifecycle tests for the local-only Ollama HTTP transport."""

import asyncio
from typing import Any, cast

import httpx
import pytest
from pydantic import ValidationError

from app.ai.errors import ModelRequestTimeout, ModelRuntimeUnavailable, OllamaPolicyViolation
from app.ai.models.ollama_http import (
    LocalOllamaHTTPClient,
    OllamaEndpoint,
    OllamaSettings,
)


@pytest.mark.parametrize(
    "base_url",
    (
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://127.42.10.9:11434",
        "http://[::1]:11434",
    ),
)
def test_loopback_ollama_origins_are_allowed(base_url: str) -> None:
    """Accept hostnames and IP forms that cannot leave the local machine."""

    settings = OllamaSettings.model_validate({"base_url": base_url})

    assert settings.origin.startswith("http://")


@pytest.mark.parametrize(
    "base_url",
    (
        "https://localhost:11434",
        "http://ollama.com",
        "http://localhost.example.com:11434",
        "http://192.168.1.20:11434",
        "http://8.8.8.8:11434",
        "http://user:password@localhost:11434",
        "http://localhost:11434/api",
        "http://localhost:11434?remote=true",
    ),
)
def test_non_loopback_or_ambiguous_origins_are_rejected(base_url: str) -> None:
    """Reject configurations that could send confidential data off-machine."""

    with pytest.raises(ValidationError):
        OllamaSettings.model_validate({"base_url": base_url})


async def test_tags_request_uses_the_validated_local_origin() -> None:
    """Send allowlisted traffic only to the configured loopback origin."""

    seen_urls: list[httpx.URL] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(request.url)
        return httpx.Response(200, json={"models": []})

    async with LocalOllamaHTTPClient(
        OllamaSettings(), transport=httpx.MockTransport(handler)
    ) as client:
        response = await client.request(OllamaEndpoint.TAGS)

    assert response.status_code == 200
    assert seen_urls == [httpx.URL("http://127.0.0.1:11434/api/tags")]


async def test_pull_endpoint_is_rejected_before_http() -> None:
    """Make runtime downloads impossible through the HTTP seam."""

    request_reached_transport = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_reached_transport
        request_reached_transport = True
        return httpx.Response(200, request=request)

    async with LocalOllamaHTTPClient(
        OllamaSettings(), transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(OllamaPolicyViolation):
            await client.request(cast(Any, "/api/pull"))

    assert request_reached_transport is False


async def test_timeout_becomes_a_typed_local_runtime_error() -> None:
    """Bound request duration even when an injected transport stalls."""

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200, request=request)

    settings = OllamaSettings(request_timeout_seconds=0.01)
    async with LocalOllamaHTTPClient(
        settings, transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(ModelRequestTimeout):
            await client.request(OllamaEndpoint.TAGS)


async def test_connection_failure_becomes_runtime_unavailable() -> None:
    """Expose connection failures without leaking lower-level HTTP details."""

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sanitized test failure", request=request)

    async with LocalOllamaHTTPClient(
        OllamaSettings(), transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(ModelRuntimeUnavailable):
            await client.request(OllamaEndpoint.TAGS)


async def test_caller_cancellation_is_not_converted_or_swallowed() -> None:
    """Let Backend 1 cancel in-flight work through normal asyncio semantics."""

    request_started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        request_started.set()
        await asyncio.sleep(10)
        return httpx.Response(200, request=request)

    async with LocalOllamaHTTPClient(
        OllamaSettings(), transport=httpx.MockTransport(handler)
    ) as client:
        task = asyncio.create_task(client.request(OllamaEndpoint.TAGS))
        await request_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
