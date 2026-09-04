"""Local Ollama adapter with approved-model fallback and resource control."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import suppress
from time import perf_counter
from types import TracebackType
from typing import TypeVar

import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

from app.ai.errors import (
    AIError,
    InvalidStructuredOutput,
    ModelCapacityError,
    ModelNotInstalled,
    ModelRequestFailed,
    OllamaPolicyViolation,
)
from app.ai.models.ollama_http import (
    LocalOllamaHTTPClient,
    OllamaEndpoint,
    OllamaSettings,
)
from app.ai.models.ollama_wire import (
    OllamaChatResponse,
    OllamaEmbedResponse,
    OllamaTagsResponse,
)
from app.ai.models.profiles import load_model_profile
from app.ai.schemas import (
    Capability,
    EmbeddingRequest,
    EmbeddingResult,
    InferenceMetrics,
    InstalledModel,
    ModelHealth,
    ModelProfile,
    ModelRuntimeHealth,
    ModelStatus,
    TextGenerationRequest,
    TextGenerationResult,
    VisionGenerationRequest,
)

ResultT = TypeVar("ResultT")
_JSON_VALUE_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
_CAPACITY_MARKERS = (
    "out of memory",
    "insufficient memory",
    "not enough memory",
    "failed to load model",
    "cuda error",
    "cuda out of memory",
    "allocation failed",
)


class OllamaModelAdapter:
    """Implement all local model operations behind one policy-enforcing interface."""

    def __init__(
        self,
        client: LocalOllamaHTTPClient,
        settings: OllamaSettings,
        profile: ModelProfile,
    ) -> None:
        self._client = client
        self._settings = settings
        self._profile = profile
        self._inference_lock = asyncio.Lock()
        self._active_generative_model: str | None = None

    async def list_models(self) -> tuple[InstalledModel, ...]:
        """List preinstalled local models without pulling or loading them."""

        response = await self._client.request(OllamaEndpoint.TAGS)
        self._raise_for_status(response)
        try:
            payload = OllamaTagsResponse.model_validate_json(response.content)
        except ValidationError as error:
            raise InvalidStructuredOutput("Ollama returned an invalid model list") from error

        return tuple(
            InstalledModel(
                name=model.name,
                size_bytes=model.size,
                digest=model.digest,
                family=model.details.family,
                parameter_size=model.details.parameter_size,
                quantization_level=model.details.quantization_level,
            )
            for model in payload.models
        )

    async def health(self, profile: ModelProfile) -> ModelRuntimeHealth:
        """Distinguish unavailable runtime, missing candidates, and ready models."""

        try:
            installed = await self.list_models()
        except AIError as error:
            error_code = type(error).__name__
            return ModelRuntimeHealth(
                runtime_ready=False,
                runtime_error=error_code,
                models=tuple(
                    ModelHealth(
                        capability=capability,
                        status=ModelStatus.UNAVAILABLE,
                        installed=False,
                        last_error=error_code,
                    )
                    for capability in Capability
                ),
            )

        installed_names = {model.name for model in installed}
        return ModelRuntimeHealth(
            runtime_ready=True,
            models=tuple(
                self._capability_health(capability, profile, installed_names)
                for capability in Capability
            ),
        )

    async def generate_text(self, request: TextGenerationRequest) -> TextGenerationResult:
        """Run non-streaming structured text generation with one approved fallback."""

        async with self._inference_lock:
            result, selected_model, fallback_reason = await self._with_fallback(
                Capability.TEXT,
                request.model,
                lambda model: self._chat(
                    model=model,
                    system_prompt=request.system_prompt,
                    user_prompt=request.user_prompt,
                    images_base64=(),
                    output_schema=request.output_schema,
                    context_window=request.limits.context_window,
                    max_output_tokens=request.limits.max_output_tokens,
                    timeout_seconds=request.limits.timeout_seconds,
                    temperature=request.temperature,
                ),
            )
        return result.model_copy(
            update={
                "model": selected_model,
                "used_fallback": fallback_reason is not None,
                "fallback_reason": fallback_reason,
            }
        )

    async def generate_vision(self, request: VisionGenerationRequest) -> TextGenerationResult:
        """Run structured vision generation over caller-normalized base64 images."""

        async with self._inference_lock:
            result, selected_model, fallback_reason = await self._with_fallback(
                Capability.VISION,
                request.model,
                lambda model: self._chat(
                    model=model,
                    system_prompt=request.system_prompt,
                    user_prompt=request.user_prompt,
                    images_base64=request.images_base64,
                    output_schema=request.output_schema,
                    context_window=request.limits.context_window,
                    max_output_tokens=request.limits.max_output_tokens,
                    timeout_seconds=request.limits.timeout_seconds,
                    temperature=request.temperature,
                ),
            )
        return result.model_copy(
            update={
                "model": selected_model,
                "used_fallback": fallback_reason is not None,
                "fallback_reason": fallback_reason,
            }
        )

    async def create_embeddings(self, request: EmbeddingRequest) -> EmbeddingResult:
        """Generate local embeddings with one approved fallback candidate."""

        async with self._inference_lock:
            result, selected_model, fallback_reason = await self._with_fallback(
                Capability.EMBEDDING,
                request.model,
                lambda model: self._embed(model, request.inputs),
            )
        return result.model_copy(
            update={
                "model": selected_model,
                "used_fallback": fallback_reason is not None,
                "fallback_reason": fallback_reason,
            }
        )

    async def unload(self) -> None:
        """Unload the tracked text or vision model and release GPU memory."""

        async with self._inference_lock:
            if self._active_generative_model is None:
                return
            await self._unload_model(self._active_generative_model)
            self._active_generative_model = None

    async def close(self) -> None:
        """Unload tracked model state and close the local HTTP connection pool."""

        try:
            await self.unload()
        finally:
            await self._client.close()

    async def __aenter__(self) -> "OllamaModelAdapter":
        """Support FastAPI lifespan ownership and deterministic test cleanup."""

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close runtime resources without suppressing exceptions."""

        del exc_type, exc_value, traceback
        await self.close()

    async def _with_fallback(
        self,
        capability: Capability,
        requested_model: str,
        operation: Callable[[str], Awaitable[ResultT]],
    ) -> tuple[ResultT, str, str | None]:
        candidates = self._candidate_chain(capability, requested_model)
        installed_names = {model.name for model in await self.list_models()}
        fallback_reason: str | None = None
        last_error: ModelNotInstalled | ModelCapacityError | None = None

        for index, candidate in enumerate(candidates):
            if candidate not in installed_names:
                last_error = ModelNotInstalled(
                    f"approved {capability.value} model '{candidate}' is not installed"
                )
                if index == 0 and len(candidates) > 1:
                    fallback_reason = (
                        f"Preferred model '{candidate}' is not installed; tried "
                        f"'{candidates[1]}'."
                    )
                continue

            if capability in (Capability.TEXT, Capability.VISION):
                await self._prepare_generative_model(candidate)
            try:
                return await operation(candidate), candidate, fallback_reason
            except (ModelNotInstalled, ModelCapacityError) as error:
                last_error = error
                if capability in (Capability.TEXT, Capability.VISION):
                    await self._best_effort_unload(candidate)
                if index == 0 and len(candidates) > 1:
                    fallback_reason = (
                        f"Preferred model '{candidate}' could not run; tried "
                        f"'{candidates[1]}'."
                    )

        if last_error is not None:
            raise last_error
        raise ModelNotInstalled(f"no approved {capability.value} model is installed")

    def _candidate_chain(self, capability: Capability, requested_model: str) -> tuple[str, ...]:
        candidates = self._candidates_for(capability, self._profile)
        if requested_model not in candidates:
            raise OllamaPolicyViolation(
                f"model '{requested_model}' is not approved by profile '{self._profile.profile_id}'"
            )
        start = candidates.index(requested_model)
        return candidates[start : start + 2]

    async def _prepare_generative_model(self, model: str) -> None:
        current = self._active_generative_model
        if (
            current is not None
            and current != model
            and self._settings.unload_on_capability_switch
        ):
            await self._unload_model(current)
        self._active_generative_model = model

    async def _best_effort_unload(self, model: str) -> None:
        with suppress(AIError):
            await self._unload_model(model)
        if self._active_generative_model == model:
            self._active_generative_model = None

    async def _unload_model(self, model: str) -> None:
        response = await self._client.request(
            OllamaEndpoint.CHAT,
            payload={
                "model": model,
                "messages": [],
                "stream": False,
                "keep_alive": 0,
            },
        )
        self._raise_for_status(response, model=model)

    async def _chat(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        images_base64: tuple[str, ...],
        output_schema: dict[str, JsonValue],
        context_window: int,
        max_output_tokens: int,
        timeout_seconds: float,
        temperature: float,
    ) -> TextGenerationResult:
        user_message: dict[str, object] = {"role": "user", "content": user_prompt}
        if images_base64:
            user_message["images"] = list(images_base64)
        payload: dict[str, object] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                user_message,
            ],
            "stream": False,
            "think": False,
            "format": output_schema,
            "keep_alive": self._settings.keep_alive,
            "options": {
                "temperature": temperature,
                "num_ctx": context_window,
                "num_predict": max_output_tokens,
            },
        }
        started = perf_counter()
        response = await self._client.request(
            OllamaEndpoint.CHAT,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        elapsed_ms = (perf_counter() - started) * 1_000
        self._raise_for_status(response, model=model)
        try:
            result = OllamaChatResponse.model_validate_json(response.content)
            structured_output = _JSON_VALUE_ADAPTER.validate_python(
                json.loads(result.message.content)
            )
        except (ValidationError, json.JSONDecodeError) as error:
            raise InvalidStructuredOutput(
                "Ollama returned invalid structured chat output"
            ) from error
        if result.model != model:
            raise InvalidStructuredOutput("Ollama response model did not match the selected model")
        if not result.done:
            raise InvalidStructuredOutput("Ollama non-streaming chat response was incomplete")

        return TextGenerationResult(
            model=model,
            text=result.message.content,
            structured_output=structured_output,
            done_reason=result.done_reason,
            metrics=self._chat_metrics(result, elapsed_ms),
        )

    async def _embed(self, model: str, inputs: tuple[str, ...]) -> EmbeddingResult:
        started = perf_counter()
        response = await self._client.request(
            OllamaEndpoint.EMBED,
            payload={
                "model": model,
                "input": list(inputs),
                "truncate": False,
            },
        )
        elapsed_ms = (perf_counter() - started) * 1_000
        self._raise_for_status(response, model=model)
        try:
            result = OllamaEmbedResponse.model_validate_json(response.content)
        except ValidationError as error:
            raise InvalidStructuredOutput("Ollama returned invalid embedding output") from error
        if result.model != model:
            raise InvalidStructuredOutput("Ollama response model did not match the selected model")
        if len(result.embeddings) != len(inputs):
            raise InvalidStructuredOutput("Ollama returned the wrong number of embedding vectors")
        if any(not vector for vector in result.embeddings):
            raise InvalidStructuredOutput("Ollama returned an empty embedding vector")
        dimensions = {len(vector) for vector in result.embeddings}
        if len(dimensions) != 1:
            raise InvalidStructuredOutput("Ollama returned inconsistent embedding dimensions")

        return EmbeddingResult(
            model=model,
            vectors=result.embeddings,
            metrics=InferenceMetrics(
                client_elapsed_ms=elapsed_ms,
                total_duration_ns=result.total_duration,
                load_duration_ns=result.load_duration,
                prompt_eval_count=result.prompt_eval_count,
            ),
        )

    @staticmethod
    def _chat_metrics(result: OllamaChatResponse, elapsed_ms: float) -> InferenceMetrics:
        return InferenceMetrics(
            client_elapsed_ms=elapsed_ms,
            total_duration_ns=result.total_duration,
            load_duration_ns=result.load_duration,
            prompt_eval_count=result.prompt_eval_count,
            prompt_eval_duration_ns=result.prompt_eval_duration,
            eval_count=result.eval_count,
            eval_duration_ns=result.eval_duration,
        )

    @classmethod
    def _raise_for_status(cls, response: httpx.Response, *, model: str | None = None) -> None:
        if response.is_success:
            return

        error_text = cls._safe_error_text(response)
        model_label = f" '{model}'" if model is not None else ""
        if model is not None and (response.status_code == 404 or "not found" in error_text):
            raise ModelNotInstalled(f"local model{model_label} is not installed")
        if any(marker in error_text for marker in _CAPACITY_MARKERS):
            raise ModelCapacityError(f"local model{model_label} could not fit or load")
        raise ModelRequestFailed(f"local Ollama request failed with HTTP {response.status_code}")

    @staticmethod
    def _safe_error_text(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return ""
        if not isinstance(payload, dict):
            return ""
        error = payload.get("error")
        return error.lower() if isinstance(error, str) else ""

    @staticmethod
    def _candidates_for(capability: Capability, profile: ModelProfile) -> tuple[str, ...]:
        if capability is Capability.TEXT:
            return profile.text_candidates
        if capability is Capability.VISION:
            return profile.vision_candidates
        return profile.embedding_candidates

    @classmethod
    def _capability_health(
        cls,
        capability: Capability,
        profile: ModelProfile,
        installed_names: set[str],
    ) -> ModelHealth:
        candidates = cls._candidates_for(capability, profile)
        selected = next(
            (candidate for candidate in candidates if candidate in installed_names),
            None,
        )
        if selected is None:
            return ModelHealth(
                capability=capability,
                status=ModelStatus.MISSING,
                installed=False,
                last_error="No approved candidate is installed.",
            )

        used_fallback = selected != candidates[0]
        fallback_reason = None
        if used_fallback:
            fallback_reason = (
                f"Preferred model '{candidates[0]}' is not installed; selected '{selected}'."
            )
        return ModelHealth(
            capability=capability,
            status=ModelStatus.READY,
            installed=True,
            loadable=True,
            selected_model=selected,
            fallback_reason=fallback_reason,
        )


def create_ollama_adapter(
    *,
    settings: OllamaSettings | None = None,
    profile: ModelProfile | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> OllamaModelAdapter:
    """Compose the adapter without network activity or runtime model downloads."""

    selected_settings = settings or OllamaSettings()
    selected_profile = profile or load_model_profile()
    client = LocalOllamaHTTPClient(selected_settings, transport=transport)
    return OllamaModelAdapter(client, selected_settings, selected_profile)
