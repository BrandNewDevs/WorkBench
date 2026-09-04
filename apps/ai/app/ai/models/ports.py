"""Dependency-injection boundary for local model runtimes."""

from typing import Protocol

from app.ai.schemas import (
    EmbeddingRequest,
    EmbeddingResult,
    ModelProfile,
    ModelRuntimeHealth,
    TextGenerationRequest,
    TextGenerationResult,
    VisionAnalysis,
    VisualAnalysisRequest,
)


class ModelAdapter(Protocol):
    """Minimal model operations required by the AI engine.

    A later phase will implement this with local Ollama HTTP calls. Importing
    this protocol performs no I/O and does not require Ollama to be installed.
    """

    async def health(self, profile: ModelProfile) -> ModelRuntimeHealth:
        """Report local runtime and approved-model readiness."""
        ...

    async def generate_text(self, request: TextGenerationRequest) -> TextGenerationResult:
        """Run one bounded structured text generation request."""
        ...

    async def analyze_visual(self, request: VisualAnalysisRequest) -> VisionAnalysis:
        """Analyze only the backend-approved visual inputs in the request."""
        ...

    async def create_embeddings(self, request: EmbeddingRequest) -> EmbeddingResult:
        """Create embeddings using an approved local embedding model."""
        ...
