"""Content-free model observations collected by the golden evaluator."""

from dataclasses import dataclass

from pydantic import JsonValue

from app.ai.errors import InvalidStructuredOutput
from app.ai.models.ports import ModelAdapter
from app.ai.models.structured_output import validate_structured_output
from app.ai.schemas import (
    Capability,
    EmbeddingRequest,
    EmbeddingResult,
    InferenceMetrics,
    InstalledModel,
    ModelProfile,
    ModelRuntimeHealth,
    TextGenerationRequest,
    TextGenerationResult,
    VisionGenerationRequest,
)


@dataclass(frozen=True, slots=True)
class ObservationSnapshot:
    """Monotonic counters used to isolate one run from the next."""

    schema_failures: int
    fallback_uses: int
    model_invocations: int
    prompt_tokens: int
    generated_tokens: int
    model_total_duration_ns: int
    model_load_duration_ns: int
    generation_duration_ns: int
    model_selections: tuple[tuple[Capability, str], ...]


class ObservedModelAdapter:
    """Validate and count local model results without recording confidential content."""

    def __init__(self, adapter: ModelAdapter) -> None:
        self._adapter = adapter
        self._schema_failures = 0
        self._fallback_uses = 0
        self._model_invocations = 0
        self._prompt_tokens = 0
        self._generated_tokens = 0
        self._model_total_duration_ns = 0
        self._model_load_duration_ns = 0
        self._generation_duration_ns = 0
        self._model_selections: list[tuple[Capability, str]] = []

    def snapshot(self) -> ObservationSnapshot:
        """Capture content-free counters before or after one evaluation run."""

        return ObservationSnapshot(
            schema_failures=self._schema_failures,
            fallback_uses=self._fallback_uses,
            model_invocations=self._model_invocations,
            prompt_tokens=self._prompt_tokens,
            generated_tokens=self._generated_tokens,
            model_total_duration_ns=self._model_total_duration_ns,
            model_load_duration_ns=self._model_load_duration_ns,
            generation_duration_ns=self._generation_duration_ns,
            model_selections=tuple(self._model_selections),
        )

    async def list_models(self) -> tuple[InstalledModel, ...]:
        return await self._adapter.list_models()

    async def health(self, profile: ModelProfile) -> ModelRuntimeHealth:
        return await self._adapter.health(profile)

    async def generate_text(self, request: TextGenerationRequest) -> TextGenerationResult:
        try:
            result = await self._adapter.generate_text(request)
        except InvalidStructuredOutput:
            self._schema_failures += 1
            raise
        self._validate_result(Capability.TEXT, request.output_schema, result)
        return result

    async def generate_vision(self, request: VisionGenerationRequest) -> TextGenerationResult:
        try:
            result = await self._adapter.generate_vision(request)
        except InvalidStructuredOutput:
            self._schema_failures += 1
            raise
        self._validate_result(Capability.VISION, request.output_schema, result)
        return result

    async def create_embeddings(self, request: EmbeddingRequest) -> EmbeddingResult:
        result = await self._adapter.create_embeddings(request)
        self._record_fallback(result.used_fallback)
        self._record_inference(Capability.EMBEDDING, result.model, result.metrics)
        return result

    async def unload(self) -> None:
        await self._adapter.unload()

    async def close(self) -> None:
        await self._adapter.close()

    def _validate_result(
        self,
        capability: Capability,
        schema: dict[str, JsonValue],
        result: TextGenerationResult,
    ) -> None:
        try:
            validate_structured_output(schema, result.structured_output)
        except InvalidStructuredOutput:
            self._schema_failures += 1
            raise
        self._record_fallback(result.used_fallback)
        self._record_inference(capability, result.model, result.metrics)

    def _record_fallback(self, used_fallback: bool) -> None:
        if used_fallback:
            self._fallback_uses += 1

    def _record_inference(
        self,
        capability: Capability,
        model: str,
        metrics: InferenceMetrics,
    ) -> None:
        """Aggregate only non-confidential model and timing metadata."""

        self._model_invocations += 1
        self._prompt_tokens += metrics.prompt_eval_count or 0
        self._generated_tokens += metrics.eval_count or 0
        self._model_total_duration_ns += metrics.total_duration_ns or 0
        self._model_load_duration_ns += metrics.load_duration_ns or 0
        self._generation_duration_ns += metrics.eval_duration_ns or 0
        self._model_selections.append((capability, model))
