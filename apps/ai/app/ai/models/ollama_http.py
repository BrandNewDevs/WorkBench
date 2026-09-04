"""Policy-enforcing asynchronous HTTP transport for the local Ollama runtime."""

import asyncio
from enum import StrEnum
from ipaddress import ip_address
from types import TracebackType

import httpx
from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.ai.errors import ModelRequestTimeout, ModelRuntimeUnavailable, OllamaPolicyViolation


class OllamaEndpoint(StrEnum):
    """Complete endpoint allowlist for the workstation MVP."""

    TAGS = "/api/tags"
    CHAT = "/api/chat"
    EMBED = "/api/embed"


class OllamaSettings(BaseSettings):
    """Environment-backed local runtime settings with a strict loopback URL."""

    model_config = SettingsConfigDict(
        env_prefix="WORKBENCH_OLLAMA_",
        extra="ignore",
        frozen=True,
    )

    base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:11434")
    request_timeout_seconds: float = Field(default=180, gt=0)
    keep_alive: str | int = "5m"
    unload_on_capability_switch: bool = True

    @field_validator("base_url")
    @classmethod
    def require_loopback_origin(cls, url: AnyHttpUrl) -> AnyHttpUrl:
        """Reject cloud, LAN, credentialed, and path-prefixed Ollama URLs."""

        if url.scheme != "http":
            raise ValueError("Ollama base URL must use HTTP on the local loopback interface")
        if url.username is not None or url.password is not None:
            raise ValueError("Ollama base URL must not contain credentials")
        if url.query is not None or url.fragment is not None or url.path not in (None, "/"):
            raise ValueError("Ollama base URL must be an origin without a path, query, or fragment")

        host = (url.host or "").strip("[]").lower()
        if host == "localhost":
            return url
        try:
            address = ip_address(host)
        except ValueError as error:
            raise ValueError("Ollama base URL host must be localhost or a loopback IP") from error
        if not address.is_loopback:
            raise ValueError("Ollama base URL host must be localhost or a loopback IP")
        return url

    @property
    def origin(self) -> str:
        """Return the validated origin without a trailing slash."""

        return str(self.base_url).rstrip("/")


class LocalOllamaHTTPClient:
    """Small local-only interface hiding HTTP policy, timeout, and cancellation details."""

    def __init__(
        self,
        settings: OllamaSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.origin,
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def request(
        self,
        endpoint: OllamaEndpoint,
        *,
        payload: dict[str, object] | None = None,
    ) -> httpx.Response:
        """Call one allowlisted endpoint and preserve caller cancellation."""

        if not isinstance(endpoint, OllamaEndpoint):
            raise OllamaPolicyViolation("Ollama endpoint is not in the local runtime allowlist")

        method = "GET" if endpoint is OllamaEndpoint.TAGS else "POST"
        try:
            async with asyncio.timeout(self._settings.request_timeout_seconds):
                return await self._client.request(method, endpoint.value, json=payload)
        except TimeoutError as error:
            raise ModelRequestTimeout("local Ollama request timed out") from error
        except httpx.TimeoutException as error:
            raise ModelRequestTimeout("local Ollama request timed out") from error
        except httpx.RequestError as error:
            raise ModelRuntimeUnavailable("local Ollama runtime is unavailable") from error

    async def close(self) -> None:
        """Release the owned HTTP connection pool."""

        await self._client.aclose()

    async def __aenter__(self) -> "LocalOllamaHTTPClient":
        """Support deterministic client cleanup in composition roots and tests."""

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the connection pool without suppressing exceptions."""

        del exc_type, exc_value, traceback
        await self.close()
