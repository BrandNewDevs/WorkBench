"""Dependency-injection boundary for local model runtimes."""

from typing import Protocol

from app.ai.schemas import (
    EmbeddingRequest,
    EmbeddingResult,
    InstalledModel,
    ModelProfile,
    ModelRuntimeHealth,
    TextGenerationRequest,
    TextGenerationResult,
    VisionGenerationRequest,
)


class ModelAdapter(Protocol):
    """Minimal model operations required by the AI engine.

    A later phase will implement this with local Ollama HTTP calls. Importing
    this protocol performs no I/O and does not require Ollama to be installed.
    """

    async def list_models(self) -> tuple[InstalledModel, ...]:
        """List models already installed in the local runtime."""
        ...

    async def health(self, profile: ModelProfile) -> ModelRuntimeHealth:
        """Report local runtime and approved-model readiness."""
        ...

    async def generate_text(self, request: TextGenerationRequest) -> TextGenerationResult:
        """Run one bounded structured text generation request."""
        ...

    async def generate_vision(self, request: VisionGenerationRequest) -> TextGenerationResult:
        """Run structured generation over already-normalized image data."""
        ...

    async def create_embeddings(self, request: EmbeddingRequest) -> EmbeddingResult:
        """Create embeddings using an approved local embedding model."""
        ...

    async def unload(self) -> None:
        """Unload the active large generative model, if one is tracked."""
        ...

    async def close(self) -> None:
        """Release local runtime connections owned by the adapter."""
        ...
